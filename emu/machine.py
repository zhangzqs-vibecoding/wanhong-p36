"""Execute the P36 firmware and dispatch the emulated MMIO peripherals."""

from collections import Counter
from pathlib import Path
import struct

from unicorn import (
    UC_ARCH_ARM, UC_HOOK_CODE, UC_HOOK_INTR, UC_HOOK_MEM_READ, UC_HOOK_MEM_UNMAPPED,
    UC_HOOK_MEM_WRITE, UC_MODE_ARM, UC_MODE_LITTLE_ENDIAN, Uc,
)
from unicorn.arm_const import (
    UC_ARM_REG_CPSR, UC_ARM_REG_LR, UC_ARM_REG_PC, UC_ARM_REG_R0, UC_ARM_REG_R1,
    UC_ARM_REG_R2, UC_ARM_REG_R3, UC_ARM_REG_R5, UC_ARM_REG_SP, UC_ARM_REG_SPSR,
)

from audio import DSP_IRQ, IDMA_DATA, IDMA_SELECT, AudioDsp
from block_budget import BlockBudget
from jpeg import JpegDecoder
from keypad import GPIO_ENABLE, GPIO_INPUT, GPIO_IRQ_STATUS, Keypad
from lcd import LCD_BASE, Lcd
from nes import NesAudio
from sd import L2_BASE, SD_BASE, SDRAM_BASE, SDRAM_SIZE, SdCard
from timers import IRQ_ENABLE, IRQ_PENDING, IRQ_STATUS, TIMER_CYCLES_PER_SECOND, TIMER_IRQ, Timers
from touch import POINTER_MESSAGE, TOUCH_ADC_X, TOUCH_ADC_Y, TouchPanel
from video import RGB24_TO_RGB565, RGB_CONVERT_CONTROL, MjpegDecoder, convert_rgb24


PROJECT = Path(__file__).resolve().parents[1]
FIRMWARE = PROJECT / "sdcard" / "sword_elfd.bin"
MMIO_REGIONS = ((0x08000000, 0x10000), (0x20000000, 0x100000), (0x48000000, 0x10000))
VECTORS = (
    0x30000020, 0x302176C0, 0x30000100, 0x302176FC,
    0x30217738, 0x30000020, 0x30217618, 0x3021766C,
)
SELF_CLEAR = {0x08000004, 0x0800000C, 0x0800004C}
CHIP_ID = 0x33323236
BATTERY_ADC = 0x08000070
BATTERY_ADC_FULL = 640
SCHEDULER_IDLE = range(0x30216DE8, 0x30216E00, 4)
PCM_FEED_WAIT = range(0x30079350, 0x30079360, 4)
PCM_FEED_WAIT_RETURN = (0x30079354, 0x30079358)
POST_MESSAGE = 0x3000E6B0
INPUT_SCRATCH = 0xE00
INPUT_RETURN = 0xE80
NES_LOADED = 0x31E175EC
NES_KEYMAP = 0x305ADF7A
MEDIA_IRQ_SLICE = 30_000
MOVIE_STARTUP_MS = 1000
MOVIE_STARTUP_LIMIT = 24


class Machine:
    def __init__(self, firmware=FIRMWARE, sd_image=None, trace=False, saved_sectors=None):
        self.sd = SdCard(sd_image, trace=trace, saved_sectors=saved_sectors) if sd_image else None
        self.timers = Timers() if self.sd else None
        self.lcd = Lcd()
        self.reads = Counter()
        self.forced = {}
        self.l2_control = None
        self.rgb_convert_done = False
        self.media_clock_ns = None
        self.media_timer_last = None
        self.movie_startup_frames = ()
        self.irqs = 0
        raw = bytearray(firmware.read_bytes())
        self.keypad = Keypad(raw, SDRAM_BASE) if self.sd else None
        struct.pack_into("<I", raw, 0x30217848 - SDRAM_BASE, 0xE1A00000)
        if self.sd:
            for address in (0x30217910, 0x3021791C):
                struct.pack_into("<I", raw, address - SDRAM_BASE, 0xE12FFF1E)

        self.uc = Uc(UC_ARCH_ARM, UC_MODE_ARM | UC_MODE_LITTLE_ENDIAN)
        self.uc.mem_map(SDRAM_BASE, SDRAM_SIZE)
        self.uc.mem_write(SDRAM_BASE, bytes(raw))
        self.uc.mem_map(0, 0x1000)
        self.uc.mem_write(0, struct.pack("<16I", *(0xE59FF018,) * 8, *VECTORS))
        for base, size in MMIO_REGIONS:
            self.uc.mem_map(base, size)
        if self.sd:
            self.uc.mem_write(0x08000000, struct.pack("<I", CHIP_ID))
        self.uc.reg_write(UC_ARM_REG_SP, 0x31FF0000)
        self.uc.reg_write(UC_ARM_REG_PC, SDRAM_BASE)
        self.uc.reg_write(UC_ARM_REG_CPSR, 0xD3)
        self.audio = AudioDsp(self.uc, SDRAM_BASE, SDRAM_SIZE, trace) if self.sd else None
        self.nes_audio = NesAudio(self.uc, self.audio.publish_wave, trace) if self.sd else None
        self.block_budget = BlockBudget.load(self.uc) if self.sd else None
        self.touch = TouchPanel(self.uc, trace) if self.sd else None
        self.mjpeg = MjpegDecoder(self.uc, trace) if self.sd else None
        if self.sd:
            JpegDecoder(self.uc, SDRAM_BASE, SDRAM_SIZE, trace)
        self.use_hooks(trace)

    def use_hooks(self, trace):
        if self.sd:
            for base, size in MMIO_REGIONS:
                self.uc.hook_add(UC_HOOK_MEM_READ, self.on_read, begin=base, end=base + size - 1)
                self.uc.hook_add(UC_HOOK_MEM_WRITE, self.on_write, begin=base, end=base + size - 1)
        else:
            self.uc.hook_add(UC_HOOK_MEM_READ, self.on_read)
            self.uc.hook_add(UC_HOOK_MEM_WRITE, self.on_write)
        self.uc.hook_add(UC_HOOK_INTR, lambda *_: None)
        self.uc.hook_add(UC_HOOK_MEM_UNMAPPED, self.map_unknown)
        if trace:
            for address in (0x300103AC, 0x30010820, 0x30025BD8):
                self.uc.hook_add(UC_HOOK_CODE, self.trace_log, begin=address, end=address)
            self.uc.hook_add(UC_HOOK_CODE, self.trace_open, begin=0x30039BF0, end=0x30039BF0)
            for address in (0x300395BC, 0x30039628):
                self.uc.hook_add(UC_HOOK_CODE, self.trace_pointer, begin=address, end=address)
            self.uc.hook_add(UC_HOOK_CODE, self.trace_key, begin=0x300770B4, end=0x300770B4)

    @staticmethod
    def map_unknown(uc, access, address, size, value, user_data):
        uc.mem_map(address & ~0xFFF, 0x1000)
        return True

    def on_read(self, uc, access, address, size, value, user_data):
        if SDRAM_BASE <= address < SDRAM_BASE + SDRAM_SIZE:
            return
        register = address & ~3
        self.reads[register] += 1
        if self.sd and register == BATTERY_ADC:
            current = struct.unpack("<I", uc.mem_read(register, 4))[0]
            uc.mem_write(register, struct.pack("<I", (current & ~0x3FF) | BATTERY_ADC_FULL))
            return
        if self.audio and register == IDMA_DATA:
            uc.mem_write(register, struct.pack("<I", self.audio.read_data()))
            return
        if register == RGB_CONVERT_CONTROL and self.rgb_convert_done:
            value, = struct.unpack("<I", uc.mem_read(register, 4))
            uc.mem_write(register, struct.pack("<I", value & ~1))
            return
        if self.timers and register < 0x08000100:
            result = self.pending_irqs if register == IRQ_PENDING else self.timers.read_register(register)
            if register == IRQ_STATUS and self.gpio_pending:
                result |= GPIO_IRQ_STATUS
            if GPIO_INPUT <= register < GPIO_INPUT + 16:
                result = self.keypad.input(uc, register)
                if register == GPIO_INPUT:
                    result = (result & ~1) | int(not self.touch.down)
            elif register == TOUCH_ADC_X:
                result = self.touch.adc_x
            elif register == TOUCH_ADC_Y:
                result = self.touch.adc_y
            if result is not None:
                uc.mem_write(register, struct.pack("<I", result))
                return
        if self.sd and SD_BASE <= register < SD_BASE + 0x1000:
            result = self.sd.read_register(register)
            if result is not None:
                offset = address - register
                uc.mem_write(address, struct.pack("<I", result)[offset:offset + size])
            return
        if register == L2_BASE + 0x80 and self.l2_control is not None:
            uc.mem_write(register, struct.pack("<I", self.l2_control))
        if register in self.forced:
            uc.mem_write(address, struct.pack("<I", self.forced[register]))
        elif register in SELF_CLEAR:
            uc.mem_write(address, bytes(size))
        elif self.sd is None and self.reads[register] > 60_000:
            self.forced[register] = 0xFFFFFFFF
            print(f"  AUTO-SATISFY poll 0x{register:08x} (pc=0x{self.pc:08x})")
            uc.mem_write(address, struct.pack("<I", 0xFFFFFFFF))

    def on_write(self, uc, access, address, size, value, user_data):
        if SDRAM_BASE <= address < SDRAM_BASE + SDRAM_SIZE:
            return
        register = address & ~3
        value &= 0xFFFFFFFF
        if self.audio and register in (IDMA_SELECT, IDMA_DATA):
            self.audio.write_register(uc, register, value)
            return
        if self.sd and register == RGB_CONVERT_CONTROL:
            self.rgb_convert_done = value == RGB24_TO_RGB565 and convert_rgb24(uc)
        if self.touch and register == GPIO_ENABLE and not value & 1:
            self.touch.edge = False
        if self.timers and register < 0x08000100 and self.timers.write_register(register, value):
            return
        if LCD_BASE <= register < LCD_BASE + 0x1000:
            if self.lcd.write_register(register, value):
                self.capture_movie_startup(uc)
        if self.sd and (SD_BASE <= register < SD_BASE + 0x1000 or L2_BASE <= register < L2_BASE + 0x100):
            completed = self.sd.write_register(uc, register, value)
            if register == L2_BASE + 0x80:
                self.l2_control = completed
            return
        if register in SELF_CLEAR:
            value = 0
        uc.mem_write(register, struct.pack("<I", value))

    def capture_movie_startup(self, uc):
        movie = self.movie_position
        frames = self.movie_startup_frames
        if frames and (movie is None or frames[-1][2][0] != movie[0]):
            frames = self.movie_startup_frames = ()
        if movie is None or movie[1] >= MOVIE_STARTUP_MS or len(frames) >= MOVIE_STARTUP_LIMIT:
            return
        if frames and movie[1] <= frames[-1][2][1]:
            return
        # The decoder reuses the scanout buffer between successive LCD commits.
        scanout = self.lcd.read_frame(uc)
        if scanout is not None:
            self.movie_startup_frames = (*frames, (self.lcd.frames, scanout, movie))

    @property
    def pc(self):
        return self.uc.reg_read(UC_ARM_REG_PC)

    @property
    def idle(self):
        return self.pc in SCHEDULER_IDLE

    @property
    def nes_keys(self):
        if self.sd and any(self.uc.mem_read(NES_LOADED, 4)):
            return self.uc.mem_read(NES_KEYMAP, 8).hex()
        return None

    @property
    def movie_position(self):
        if self.audio and self.lcd.video_enabled:
            track, state = self.audio.mp3_stream
            pts = self.mjpeg.presentation_ms
            if state != "stopped" and track == self.audio.mp3_movie_track and pts is not None:
                return track, pts
        return None

    def run(self, count):
        while count > 0:
            step = count
            if self.timers:
                # Display conversion can cross a 5 ms timer deadline within a normal slice.
                step = min(count, MEDIA_IRQ_SLICE if self.realtime_media else 120_000)
                # TTS polls for space in the audio queue. Let the DSP drain it
                # while the ARM has no useful work to do in this loop.
                if self.audio.mode == "pcm" and (
                    self.pc in PCM_FEED_WAIT
                    or self.uc.reg_read(UC_ARM_REG_LR) in PCM_FEED_WAIT_RETURN
                ):
                    self.audio.request()
                if self.pending_irqs:
                    self.deliver_irq()
            thumb = (self.uc.reg_read(UC_ARM_REG_CPSR) >> 5) & 1
            begin = self.pc | thumb
            if self.block_budget and self.realtime_media and self.nes_audio.running:
                self.block_budget.run(begin, step)
            else:
                if self.block_budget:
                    self.block_budget.close()
                self.uc.emu_start(begin, 0, count=step)
            if self.pc == 0:
                link = self.uc.reg_read(UC_ARM_REG_LR)
                raise RuntimeError(f"firmware jumped to address 0 from 0x{link:08x}")
            if self.timers:
                timer_cycles = step
                if self.realtime_media:
                    now = self.media_clock_ns()
                    if self.media_timer_last is not None:
                        elapsed = now - self.media_timer_last
                        timer_cycles = elapsed * TIMER_CYCLES_PER_SECOND // 1_000_000_000
                    self.media_timer_last = now
                else:
                    self.media_timer_last = None
                self.timers.advance(timer_cycles)
                self.audio.advance(step)
            count -= step
        if self.nes_audio:
            self.nes_audio.advance()

    @property
    def realtime_media(self):
        return self.media_clock_ns is not None and (self.nes_audio.running or self.lcd.video_enabled)

    @property
    def gpio_pending(self):
        return self.keypad.pending(self.uc) or self.touch.pending(self.uc)

    @property
    def pending_irqs(self):
        pending = self.timers.read_register(IRQ_PENDING)
        if self.gpio_pending:
            pending |= TIMER_IRQ
        return pending | (DSP_IRQ if self.audio.pending else 0)

    def deliver_irq(self):
        cpsr = self.uc.reg_read(UC_ARM_REG_CPSR)
        if cpsr & 0x80:
            return
        enabled = struct.unpack("<I", self.uc.mem_read(IRQ_ENABLE, 4))[0]
        if not enabled & self.pending_irqs:
            return
        pc = self.pc
        self.uc.reg_write(UC_ARM_REG_CPSR, (cpsr & ~0x3F) | 0x92)
        self.uc.reg_write(UC_ARM_REG_SPSR, cpsr)
        self.uc.reg_write(UC_ARM_REG_LR, pc + 4)
        self.uc.reg_write(UC_ARM_REG_PC, 0x18)
        self.irqs += 1

    def post_pointer(self, x, y, phase):
        if not self.idle:
            return False
        if self.block_budget:
            self.block_budget.close()
        uc = self.uc
        saved = uc.context_save()
        stack = uc.reg_read(UC_ARM_REG_SP) - 8
        try:
            uc.mem_write(INPUT_SCRATCH, struct.pack("<5I", POINTER_MESSAGE, (x << 16) | phase, y, 0, 0))
            uc.mem_write(stack, struct.pack("<I", 1))
            uc.reg_write(UC_ARM_REG_R0, INPUT_SCRATCH)
            uc.reg_write(UC_ARM_REG_R1, 0)
            uc.reg_write(UC_ARM_REG_R2, 1)
            uc.reg_write(UC_ARM_REG_R3, 0)
            uc.reg_write(UC_ARM_REG_SP, stack)
            uc.reg_write(UC_ARM_REG_LR, INPUT_RETURN)
            uc.emu_start(POST_MESSAGE, INPUT_RETURN, count=100_000)
            if self.pc != INPUT_RETURN:
                raise RuntimeError(f"firmware pointer message did not return at 0x{self.pc:08x}")
        finally:
            uc.context_restore(saved)
        return True

    @staticmethod
    def trace_pointer(uc, address, size, user_data):
        pointer = uc.reg_read(UC_ARM_REG_R1)
        if SDRAM_BASE <= pointer <= SDRAM_BASE + SDRAM_SIZE - 12:
            event, packed, y = struct.unpack("<III", uc.mem_read(pointer, 12))
            if event == POINTER_MESSAGE:
                print(f"fw pointer phase={packed & 0xFFFF} x={packed >> 16} y={y}")

    @staticmethod
    def trace_key(uc, address, size, user_data):
        pointer = uc.reg_read(UC_ARM_REG_R0)
        code, _, phase = struct.unpack("<IBB2x", uc.mem_read(pointer, 8))
        print(f"fw keypad code=0x{code:02x} phase={phase}")

    @staticmethod
    def trace_log(uc, address, size, user_data):
        register = UC_ARM_REG_R0 if address == 0x30010820 else UC_ARM_REG_R2
        pointer = uc.reg_read(register)
        if SDRAM_BASE <= pointer < SDRAM_BASE + SDRAM_SIZE - 160:
            message = bytes(uc.mem_read(pointer, 160)).split(b"\0", 1)[0].strip()
            if message:
                link = uc.reg_read(UC_ARM_REG_LR)
                print(f"fw 0x{link:08x} {message.decode('gb18030', errors='replace')}")

    @staticmethod
    def trace_open(uc, address, size, user_data):
        pointer = uc.reg_read(UC_ARM_REG_R5)
        if SDRAM_BASE <= pointer < SDRAM_BASE + SDRAM_SIZE - 512:
            raw_path = uc.mem_read(pointer, 512)
            path = raw_path.decode("utf-16-le", errors="replace").split("\0", 1)[0]
            handle = uc.reg_read(UC_ARM_REG_R0)
            print(f"fw open {path!r} -> 0x{handle:08x}")
