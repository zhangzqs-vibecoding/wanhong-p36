"""Model the AMR-NB recorder and player on the P36 DSP mailbox."""

import ctypes
from queue import Empty, Full, Queue
import struct
from time import monotonic

from unicorn import UC_HOOK_CODE


AMR_COMMAND = 0x410F
AMR_PAUSE = 0x43CC
AMR_RATE = 8000
FRAME_SAMPLES = 160
FRAME_LENGTHS = (13, 14, 16, 18, 20, 21, 27, 32, 6, 0, 0, 0, 0, 0, 0, 1)
MIC_BLOCK_BYTES = 2 * FRAME_SAMPLES * 2
AMR_FLUSH_ON_STOP = 0x31D53CF8
AMR_RING = 0x31D53CFC
AMR_RING_SIZE = 0x10000
AMR_MODE = 0x31D6A432
RECORD_WINDOWS = (0x4802, 0x4822)
SAMPLES = ctypes.c_short * FRAME_SAMPLES
PACKET = ctypes.c_ubyte * 32


def load_codec():
    codec = ctypes.CDLL("libopencore-amrnb.so.0")
    codec.Encoder_Interface_init.argtypes = (ctypes.c_int,)
    codec.Encoder_Interface_init.restype = ctypes.c_void_p
    codec.Encoder_Interface_Encode.argtypes = (
        ctypes.c_void_p, ctypes.c_int, ctypes.POINTER(ctypes.c_short),
        ctypes.POINTER(ctypes.c_ubyte), ctypes.c_int,
    )
    codec.Encoder_Interface_Encode.restype = ctypes.c_int
    codec.Encoder_Interface_exit.argtypes = (ctypes.c_void_p,)
    codec.Encoder_Interface_exit.restype = None
    codec.Decoder_Interface_init.argtypes = ()
    codec.Decoder_Interface_init.restype = ctypes.c_void_p
    codec.Decoder_Interface_Decode.argtypes = (
        ctypes.c_void_p, ctypes.POINTER(ctypes.c_ubyte),
        ctypes.POINTER(ctypes.c_short), ctypes.c_int,
    )
    codec.Decoder_Interface_Decode.restype = None
    codec.Decoder_Interface_exit.argtypes = (ctypes.c_void_p,)
    codec.Decoder_Interface_exit.restype = None
    return codec


class AmrDsp:
    def __init__(self, uc, audio):
        self.uc = uc
        self.audio = audio
        self.codec = None
        self.encoder = None
        self.decoder = None
        self.deadline = 0
        self.paused = False
        self.input = Queue(maxsize=8)
        self.encoded = {}
        self.output = bytearray()
        self.frames = 0
        self.mic_blocks = 0
        for address, callback in (
            (0x300518F8, self.start_recording),
            (0x30051570, self.flush_recording),
            (0x3001DAE0, self.end_recording),
            (0x3004FB54, self.start_playback),
            (0x300522D4, self.start_playback),
            (0x30050334, self.decode_frame),
            (0x3004FD94, self.end_playback),
        ):
            uc.hook_add(UC_HOOK_CODE, callback, begin=address, end=address)

    @property
    def recording(self):
        return self.audio.mode == "amr record"

    def prepare_codec(self):
        if self.codec is None:
            try:
                self.codec = load_codec()
            except OSError:
                print("AMR audio unavailable. Install libopencore-amrnb0.", flush=True)
                return False
        return True

    def submit(self, block):
        if self.recording:
            try:
                self.input.put_nowait(block)
            except Full:
                # A bounded queue keeps stale microphone input out of later frames.
                return

    def clear_input(self):
        while True:
            try:
                self.input.get_nowait()
            except Empty:
                return

    def start_recording(self, uc, address, size, data):
        self.close()
        if self.prepare_codec():
            self.encoder = self.codec.Encoder_Interface_init(0)
        if self.encoder:
            self.frames = self.mic_blocks = 0
            self.deadline = monotonic()
            self.audio.start("amr record", 0)
            if self.audio.trace:
                print("AMR recording started rate=8000")

    def end_recording(self, *_):
        if self.encoder:
            if self.audio.trace:
                print(f"AMR recording stopped frames={self.frames} mic_blocks={self.mic_blocks}")
            self.audio.finish("amr record")
            self.codec.Encoder_Interface_exit(self.encoder)
            self.encoder = None
            self.encoded.clear()
            self.clear_input()

    def flush_recording(self, uc, *_):
        if self.recording:
            # Let the firmware write frames left in its recording ring buffer.
            uc.mem_write(AMR_FLUSH_ON_STOP, b"\x01")

    def start_playback(self, uc, address, size, data):
        self.close()
        if self.prepare_codec():
            self.decoder = self.codec.Decoder_Interface_init()
        if self.decoder:
            self.frames = 0
            self.paused = False
            self.deadline = monotonic()
            self.audio.start("amr play", 0)
            if self.audio.trace:
                print("AMR playback started rate=8000")

    def end_playback(self, *_):
        if self.decoder:
            self.publish()
            if self.audio.trace:
                print(f"AMR playback stopped frames={self.frames}")
            self.audio.finish("amr play")
            self.codec.Decoder_Interface_exit(self.decoder)
            self.decoder = None

    def close(self):
        self.end_recording()
        self.end_playback()

    def pause_playback(self, value):
        if self.audio.mode == "amr play":
            self.paused = bool(value)
            self.audio.pending = False
            self.deadline = monotonic()

    def advance(self):
        if self.audio.pending or (not self.recording and self.paused):
            return
        now = monotonic()
        if now < self.deadline:
            return
        if self.recording:
            self.encode_block()
        interval = 0.04 if self.recording else 0.02
        self.deadline = max(self.deadline + interval, now)
        self.audio.request()

    def encode_block(self):
        try:
            block = self.input.get_nowait()
            self.mic_blocks += 1
        except Empty:
            block = bytes(MIC_BLOCK_BYTES)
        mode = min(self.uc.mem_read(AMR_MODE, 1)[0], 7)
        for offset, register in enumerate(RECORD_WINDOWS):
            samples = SAMPLES.from_buffer_copy(block, offset * FRAME_SAMPLES * 2)
            packet = PACKET()
            count = self.codec.Encoder_Interface_Encode(self.encoder, mode, samples, packet, 0)
            self.encoded[register] = iter(packet[:count])
            self.frames += 1
        self.audio.words.update({0x4AAF: mode, 0x4AB0: 0, 0x4801: 1})

    def read_data(self, selection):
        if selection == AMR_COMMAND and self.audio.pending:
            self.audio.pending = False
            return 6 if self.recording else 1
        if self.recording and selection in self.encoded:
            return next(self.encoded[selection], 0)
        return None

    def decode_frame(self, uc, address, size, data):
        if not self.decoder or self.audio.mode != "amr play":
            return
        offset, = struct.unpack("<I", uc.mem_read(AMR_RING + AMR_RING_SIZE, 4))
        if offset >= AMR_RING_SIZE:
            return
        header = uc.mem_read(AMR_RING + offset, 1)[0]
        length = FRAME_LENGTHS[(header >> 3) & 15]
        if not length or header & 0x83 or not header & 4:
            return
        first = min(length, AMR_RING_SIZE - offset)
        packet = uc.mem_read(AMR_RING + offset, first)
        if first < length:
            packet.extend(uc.mem_read(AMR_RING, length - first))
        decoded = SAMPLES()
        self.codec.Decoder_Interface_Decode(
            self.decoder, PACKET.from_buffer_copy(packet.ljust(32, b"\0")), decoded, 0,
        )
        self.output.extend(bytes(decoded))
        self.frames += 1
        if len(self.output) >= 4 * FRAME_SAMPLES * 2:
            self.publish()

    def publish(self):
        if self.output:
            self.audio.publish_wave(self.output, AMR_RATE, 1, 2)
            self.output.clear()
