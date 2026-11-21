"""P36 DSP mailbox and export of the firmware's MP3 and PCM audio."""

from io import BytesIO
import struct
import wave

from unicorn import UC_HOOK_CODE
from unicorn.arm_const import (
    UC_ARM_REG_LR, UC_ARM_REG_PC, UC_ARM_REG_R0, UC_ARM_REG_R1, UC_ARM_REG_R2,
    UC_ARM_REG_R3, UC_ARM_REG_R4,
)

from amr import AMR_PAUSE, AmrDsp


IDMA_SELECT = 0x20050020
IDMA_DATA = 0x20050024
DSP_IRQ = 1 << 5
MP3_INPUT_REQUEST = 5
MP3_FRAME_CLOCKS = 2_508_000
MP3_RATE = 0x4036
MP3_PAUSE = 0x4003
MP3_RING = 0x31D53CFC
MP3_RING_SIZE = 65_536
MP3_BATCH_FRAMES = 8
MOVIE_PLAY_BUFFER_RETURN = 0x3011F814
DSP_CLOCK_HZ = 96_000_000
WAV_COMMAND = 0x4001
WAV_RESULT = 0x400B
WAV_WORD_COUNT = 0x400C
WAV_BLOCK_WORDS = 0x800
WAV_INPUT_REQUEST = 0x0E
WAV_FINISHED = 0x0F
WAV_PCM_WRITES = (0x300426A4, 0x30054FA8)

MP3_SAMPLE_RATES = (44100, 48000, 32000)
MP3_BITRATES_V1 = (0, 32, 40, 48, 56, 64, 80, 96, 112, 128, 160, 192, 224, 256, 320, 0)
MP3_BITRATES_V2 = (0, 8, 16, 24, 32, 40, 48, 56, 64, 80, 96, 112, 128, 144, 160, 0)


def mp3_duration_ms(data):
    """Read the sample duration of a batch of complete Layer III frames."""
    offset = 0
    duration = 0
    while offset + 4 <= len(data):
        header, = struct.unpack_from(">I", data, offset)
        version = (header >> 19) & 3
        layer = (header >> 17) & 3
        rate_index = (header >> 10) & 3
        if header >> 21 != 0x7FF or version == 1 or layer != 1 or rate_index == 3:
            return None
        table = MP3_BITRATES_V1 if version == 3 else MP3_BITRATES_V2
        bitrate = table[(header >> 12) & 0xF]
        if not bitrate:
            return None
        rate = MP3_SAMPLE_RATES[rate_index] >> {3: 0, 2: 1, 0: 2}[version]
        samples = 1152 if version == 3 else 576
        offset += samples * bitrate * 125 // rate + ((header >> 9) & 1)
        duration += samples * 1000 / rate
    return duration if offset == len(data) else None


class AudioDsp:
    def __init__(self, uc, ram_base, ram_size, trace):
        self.trace = trace
        self.ram_base = ram_base
        self.ram_end = ram_base + ram_size
        self.selection = 0
        self.words = {}
        self.mode = None
        self.pending = False
        self.remaining = 0
        self.period = MP3_FRAME_CLOCKS
        self.clips = ()
        self.buffer = (0, 0, False)
        self.requests = 0
        self.mp3_stream = (0, "stopped")
        self.mp3_batch = bytearray()
        self.mp3_frames = 0
        self.mp3_movie_prefix = b""
        self.mp3_movie_track = 0
        self.mp3_movie_position = None
        self.mp3_clock = None
        self.mp3_deadline = 0
        self.pcm = bytearray()
        self.pcm_bytes = 0
        self.pcm_format = (16000, 1, 2)
        self.pcm_end = False
        self.volume = 20
        self.amr = AmrDsp(uc, self)
        for address, callback in (
            (0x300374BC, self.set_volume),
            (0x3001A69C, self.play_buffer),
            (0x3004BBDC, self.start_mp3),
            (0x3004D394, self.capture_mp3_frame),
            (0x3004D080, self.seek_mp3),
            (0x3004C000, self.stop_mp3),
            (0x3004BF88, self.finish_mp3),
            (0x30054040, self.start_pcm),
            (0x30053C68, self.finish_pcm),
        ):
            uc.hook_add(UC_HOOK_CODE, callback, begin=address, end=address)

    def set_volume(self, uc, address, size, data):
        level = uc.reg_read(UC_ARM_REG_R0)
        if level <= 20 and level != self.volume:
            self.volume = level
            if self.trace:
                print(f"audio volume level={level}")

    def play_buffer(self, uc, address, size, data):
        self.buffer = (uc.reg_read(UC_ARM_REG_R0), uc.reg_read(UC_ARM_REG_R1),
                       uc.reg_read(UC_ARM_REG_LR) == MOVIE_PLAY_BUFFER_RETURN)

    def start_mp3(self, uc, address, size, data):
        rate = self.words.get(MP3_RATE, 0)
        samples_per_frame = 1152 if rate >= 32_000 else 576
        period = DSP_CLOCK_HZ * samples_per_frame // rate if rate else MP3_FRAME_CLOCKS
        self.start("mp3", period)
        track, _ = self.mp3_stream
        self.mp3_stream = track, "stopped"
        pointer, length, movie = self.buffer
        self.buffer = (0, 0, False)
        if length > 4 and self.ram_base <= pointer < pointer + length <= self.ram_end:
            media = bytes(uc.mem_read(pointer, length))
            if media.startswith(b"ID3") or (media[0] == 0xFF and media[1] & 0xE0 == 0xE0):
                if movie:
                    self.start_file_mp3(rate, movie=True)
                    self.mp3_movie_prefix = media
                    return
                self.publish("audio/mpeg", media)
                if self.trace:
                    print(f"audio MP3 buffer=0x{pointer:08x} bytes={length} rate={rate}")
                return
        self.start_file_mp3(rate, movie=movie)

    def start_file_mp3(self, rate, *, movie=False):
        track = self.mp3_stream[0] + 1
        self.mp3_batch.clear()
        self.mp3_frames = 0
        self.mp3_movie_prefix = b""
        self.mp3_movie_track = track if movie else 0
        self.mp3_movie_position = 0 if movie else None
        self.mp3_stream = track, "playing"
        if self.mp3_clock:
            self.mp3_deadline = self.mp3_clock()
        if self.trace:
            print(f"audio MP3 file track={track} rate={rate}")

    def seek_mp3(self, uc, address, size, data):
        if self.mp3_stream[1] in ("playing", "paused"):
            self.start_file_mp3(self.words.get(MP3_RATE, 0),
                                movie=self.mp3_stream[0] == self.mp3_movie_track)

    def capture_mp3_frame(self, uc, address, size, data):
        if self.mp3_stream[1] != "playing" or self.mode != "mp3":
            return
        length = uc.reg_read(UC_ARM_REG_R3)
        if not 4 < length <= 4096:
            return
        header = bytes(uc.mem_read(uc.reg_read(UC_ARM_REG_R2), 4))
        if header[0] != 0xFF or header[1] & 0xE0 != 0xE0:
            return
        offset, = struct.unpack("<I", uc.mem_read(uc.reg_read(UC_ARM_REG_R4), 4))
        if offset >= MP3_RING_SIZE:
            return
        # The file decoder can prepend its probe header to the first frame,
        # while the ring still starts at the original header.
        first = min(length, MP3_RING_SIZE - offset)
        ring = uc.mem_read(MP3_RING + offset, first)
        if first < length:
            ring.extend(uc.mem_read(MP3_RING, length - first))
        if (self.mp3_frames == 0 and ring[0] == 0xFF
                and ring[1] & 0xFE == header[1] & 0xFE
                and ring[2] & 0xFE == header[2] & 0xFE):
            packet = ring
        else:
            packet = header + ring[:-4]
        if self.mp3_movie_prefix:
            # The first IRQ can reuse the preceding decoder's header and size.
            # Find the first real frame in the prefill and retain any skipped frames.
            prefix_end = self.mp3_movie_prefix.find(packet)
            if prefix_end < 0 and offset == 0:
                return
            if prefix_end > 0:
                self.mp3_batch.extend(self.mp3_movie_prefix[:prefix_end])
            self.mp3_movie_prefix = b""
        self.mp3_batch.extend(packet)
        self.mp3_frames += 1
        if self.mp3_frames % MP3_BATCH_FRAMES == 0:
            self.publish_mp3()

    def publish_mp3(self):
        if self.mp3_batch:
            batch = bytes(self.mp3_batch)
            position = self.mp3_movie_position
            if position is not None:
                duration = mp3_duration_ms(batch)
                if duration is None:
                    position = self.mp3_movie_position = None
                else:
                    self.mp3_movie_position += duration
            self.publish("audio/mpeg", batch, self.mp3_stream[0], position)
            self.mp3_batch.clear()

    def stop_mp3(self, uc, address, size, data):
        self.set_mp3_state("stopped")

    def set_mp3_state(self, state):
        track, previous = self.mp3_stream
        if previous not in ("playing", "paused") or previous == state:
            return
        self.mp3_stream = track, state
        if self.trace:
            print(f"audio MP3 file track={track} {state} frames={self.mp3_frames}")

    def finish_mp3(self, uc, address, size, data):
        status = uc.reg_read(UC_ARM_REG_R0)
        if self.mp3_stream[1] in ("playing", "paused"):
            self.publish_mp3()
            self.set_mp3_state("ended" if status == 0 else "stopped")
        self.finish("mp3")
        if self.trace:
            print(f"audio MP3 finished status={status} requests={self.requests}")

    def start_pcm(self, uc, address, size, data):
        rate = self.words.get(0x4008, 0)
        channels = self.words.get(0x4007, 0)
        bits = self.words.get(0x4009, 0)
        if self.words.get(0x4006) == 1 and rate and channels in (1, 2) and bits in (8, 16):
            width = bits // 8
            self.pcm_format = rate, channels, width
            self.pcm.clear()
            self.pcm_bytes = 0
            self.pcm_end = False
            period = DSP_CLOCK_HZ * WAV_BLOCK_WORDS * 2 // (rate * channels * width)
            self.start("pcm", period)
            if self.trace:
                print(f"audio PCM started rate={rate} channels={channels} bits={bits}")

    def finish_pcm(self, uc, address, size, data):
        if self.mode == "pcm":
            self.finish("pcm")
            if self.trace:
                print(f"audio PCM finished status={uc.reg_read(UC_ARM_REG_R0)} "
                      f"requests={self.requests} bytes={self.pcm_bytes}")

    def start(self, mode, period):
        self.mode = mode
        self.pending = False
        self.requests = 0
        self.period = period
        self.remaining = period

    def finish(self, mode):
        if self.mode == mode:
            self.mode = None
            self.pending = False

    def advance(self, instructions):
        if self.mode in ("amr record", "amr play"):
            self.amr.advance()
            return
        if self.mode == "mp3":
            if self.mp3_stream[1] == "paused":
                return
            if self.mp3_stream[1] == "playing" and self.mp3_clock:
                if not self.pending and self.mp3_clock() >= self.mp3_deadline:
                    self.request()
                    self.mp3_deadline += self.period / DSP_CLOCK_HZ
                return
        if self.mode:
            self.remaining -= instructions
            if self.remaining <= 0:
                self.request()

    def request(self):
        self.pending = True
        self.remaining = self.period

    def read_data(self):
        if self.mode == "pcm":
            if self.selection == WAV_WORD_COUNT:
                return WAV_BLOCK_WORDS
            if self.selection == WAV_COMMAND and self.pending:
                self.pending = False
                self.requests += 1
                return WAV_FINISHED if self.pcm_end else WAV_INPUT_REQUEST
        elif self.mode == "mp3" and self.selection == 0x4010 and self.pending:
            self.pending = False
            self.requests += 1
            return MP3_INPUT_REQUEST
        elif self.mode in ("amr record", "amr play"):
            value = self.amr.read_data(self.selection)
            if value is not None:
                return value
        return self.words.get(self.selection, 0)

    def write_register(self, uc, register, value):
        if register == IDMA_SELECT:
            self.selection = value
            return
        self.words[self.selection] = value & 0xFFFF
        if self.selection == AMR_PAUSE:
            self.amr.pause_playback(value)
        if self.mode == "mp3" and self.selection == MP3_PAUSE:
            if value == 1 and self.mp3_stream[1] == "playing":
                self.pending = False
                self.publish_mp3()
                self.set_mp3_state("paused")
            elif value == 0 and self.mp3_stream[1] == "paused":
                self.set_mp3_state("playing")
                if self.mp3_clock:
                    self.mp3_deadline = self.mp3_clock()
        if self.mode == "pcm":
            if uc.reg_read(UC_ARM_REG_PC) in WAV_PCM_WRITES:
                self.pcm.extend(struct.pack("<H", value & 0xFFFF))
            elif self.selection == WAV_RESULT:
                if value == 0xFFFF:
                    self.pcm_end = True
                elif self.pcm:
                    self.publish_pcm()

    def publish_pcm(self):
        self.pcm_bytes += len(self.pcm)
        self.publish_wave(self.pcm, *self.pcm_format)
        self.pcm.clear()

    def publish_wave(self, samples, rate, channels, width):
        output = BytesIO()
        with wave.open(output, "wb") as wav:
            wav.setnchannels(channels)
            wav.setsampwidth(width)
            wav.setframerate(rate)
            wav.writeframes(samples)
        self.publish("audio/wav", output.getvalue())

    def publish(self, media_type, data, track=0, position=None):
        sequence = self.clips[-1][0] + 1 if self.clips else 1
        self.clips = (*self.clips[-127:], (sequence, media_type, data, track, position))

    def read_clip(self, after):
        stream = self.mp3_stream
        clips = self.clips
        track, state = stream
        if state in ("playing", "paused") or (state == "ended" and after):
            batches = clips if after else clips[-3:]
            clip = next((item for item in batches if item[0] > after and item[3] == track), None)
            if clip or state != "ended":
                return stream, clip
        if not after:
            latest = clips[-1] if clips and clips[-1][3] == 0 else None
            return stream, latest
        return stream, next((item for item in clips if item[0] > after and item[3] == 0), None)
