"""Render the APU register stream sent from the firmware's NES core to the DSP."""

import ctypes
from pathlib import Path
import struct
from time import monotonic_ns

from unicorn import UC_HOOK_CODE
from unicorn.arm_const import UC_ARM_REG_R0, UC_ARM_REG_R1

from sd import SDRAM_BASE, SDRAM_SIZE


LIBRARY = Path(__file__).with_name("nes_audio") / "libp36nes.so"
READ_MEMORY = ctypes.CFUNCTYPE(ctypes.c_int, ctypes.c_void_p, ctypes.c_uint)
PRG_BANKS = 0x31E18EBC
CPU_CLOCK = 1_789_773
SAMPLE_RATE = 32000
CHUNK_SAMPLES = 2048
CHUNK_CYCLES = CHUNK_SAMPLES * CPU_CLOCK // SAMPLE_RATE


def load_apu():
    library = ctypes.CDLL(str(LIBRARY))
    library.p36_nes_create.argtypes = (ctypes.c_int, ctypes.c_int, READ_MEMORY)
    library.p36_nes_create.restype = ctypes.c_void_p
    library.p36_nes_write.argtypes = (ctypes.c_void_p, ctypes.c_int, ctypes.c_uint, ctypes.c_int)
    library.p36_nes_write.restype = None
    library.p36_nes_render.argtypes = (
        ctypes.c_void_p, ctypes.c_int, ctypes.POINTER(ctypes.c_short), ctypes.c_int,
    )
    library.p36_nes_render.restype = ctypes.c_int
    library.p36_nes_destroy.argtypes = (ctypes.c_void_p,)
    library.p36_nes_destroy.restype = None
    return library


class NesAudio:
    def __init__(self, uc, publish_wave, trace):
        self.uc = uc
        self.publish_wave = publish_wave
        self.trace = trace
        self.library = None
        self.engine = None
        self.read_memory = READ_MEMORY(self.dmc_sample)
        self.sample_buffer = (ctypes.c_short * (CHUNK_SAMPLES + 2))()
        self.running = False
        self.started_at = 0
        self.pause_at = None
        self.rendered_cycles = 0
        self.samples = 0
        for address, callback in (
            (0x30099910, self.start),
            (0x3001DEA0, self.write_register),
            (0x3006A330, self.pause),
            (0x3006A384, self.resume),
            (0x3006A3B4, self.stop),
        ):
            uc.hook_add(UC_HOOK_CODE, callback, begin=address, end=address)

    def start(self, uc, address, size, data):
        self.close()
        self.running = True
        if self.library is None:
            try:
                self.library = load_apu()
            except OSError:
                print("NES audio unavailable. Build it with make -C emu/nes_audio", flush=True)
                return
        self.engine = self.library.p36_nes_create(SAMPLE_RATE, CPU_CLOCK, self.read_memory)
        self.started_at = monotonic_ns()
        if self.trace and self.engine:
            print(f"NES audio started rate={SAMPLE_RATE}")

    def write_register(self, uc, address, size, data):
        if self.engine is None or self.pause_at is not None:
            return
        register = uc.reg_read(UC_ARM_REG_R0)
        if 0x4000 <= register <= 0x4017 and register not in (0x4014, 0x4016):
            value = uc.reg_read(UC_ARM_REG_R1) & 0xFF
            self.library.p36_nes_write(self.engine, self.advance(), register, value)

    def advance(self):
        if not self.engine or self.pause_at is not None:
            return 0
        now = (monotonic_ns() - self.started_at) * CPU_CLOCK // 1_000_000_000
        cycles = now - self.rendered_cycles
        if cycles > 4 * CHUNK_CYCLES:
            # After a suspended host, resume near the current playback position.
            self.rendered_cycles = now - 2 * CHUNK_CYCLES
            cycles = 2 * CHUNK_CYCLES
        while cycles >= CHUNK_CYCLES:
            count = self.library.p36_nes_render(
                self.engine, CHUNK_CYCLES, self.sample_buffer, len(self.sample_buffer),
            )
            self.publish_wave(ctypes.string_at(self.sample_buffer, count * 2), SAMPLE_RATE, 1, 2)
            self.samples += count
            self.rendered_cycles += CHUNK_CYCLES
            cycles -= CHUNK_CYCLES
        return cycles

    def pause(self, uc, address, size, data):
        if self.engine and self.pause_at is None:
            self.pause_at = monotonic_ns()

    def resume(self, uc, address, size, data):
        if self.pause_at is not None:
            self.started_at += monotonic_ns() - self.pause_at
            self.pause_at = None

    def dmc_sample(self, opaque, address):
        bank, = struct.unpack("<I", self.uc.mem_read(PRG_BANKS + (address >> 11) * 4, 4))
        location = bank + (address & 0x7FF)
        if SDRAM_BASE <= location < SDRAM_BASE + SDRAM_SIZE:
            return self.uc.mem_read(location, 1)[0]
        return 0

    def stop(self, uc, address, size, data):
        self.close()

    def close(self):
        self.running = False
        if self.engine:
            self.library.p36_nes_destroy(self.engine)
            self.engine = None
            if self.trace:
                print(f"NES audio stopped samples={self.samples}")
        self.pause_at = None
        self.rendered_cycles = 0
        self.samples = 0
