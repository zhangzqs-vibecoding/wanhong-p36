#!/usr/bin/env python3
"""
wemu.py - experimental hardware emulator for the Wanhong P36 "Sword V2.0.3" firmware.

Model
-----
CPU     : ARM925 (ARM9TDMI, ARMv4T), little endian
RAM     : 32 MB SDRAM @ 0x30000000 (firmware image at start)
Vectors : exception vectors must live at 0 -> we install a trampoline table there
MMU     : the firmware enables it; we NOP that one MCR so we run identity-mapped
SWI     : the firmware's SWI handler is a cache-maint trampoline; we swallow SVC
MMIO    : register file that reads back written values, plus:
            - explicit self-clearing "busy" bits (real hardware clears them)
            - timer-free operation (delays are calibrated busy loops)

Usage: python3 wemu.py [max_ins] [--trace]
"""
import sys, struct, zlib, collections
from unicorn import *
from unicorn.arm_const import *

FW = "/home/zzq/code/wanhong/sdcard/sword_elfd.bin"
BASE = 0x30000000
RAM_SIZE = 0x2000000            # 32 MB

# ---- exception vector trampoline (because the image vectors are linked @0x30000000)
VECTORS = {0: 0x30000020, 1: 0x302176c0, 2: 0x30000100, 3: 0x302176fc,
           4: 0x30217738, 5: 0x30000020, 6: 0x30217618, 7: 0x3021766c}

# ---- self-clearing status registers: write a "start", read back "done"
SELF_CLEAR = {0x08000004, 0x0800000C, 0x0800004C}

# ---- known peripheral blocks
DEVICES = {
    0x08000000: "sys/intc (pending 0xCC, enable 0x34) + dma/engine",
    0x20010000: "unknown #1 (19 regs, probably storage/MMC)",
    0x20020000: "unknown #2",
    0x20026000: "unknown #3 (2 regs used)",
    0x2002C000: "LCD controller (0x80 ctrl, 0x84 page-flip, 0x88/0x8C)",
    0x2002D000: "timer/RTC (0x04,0x08,0x14)",
    0x48000000: "DSP / IDMA",
}


def build_vector_page():
    page = bytearray(0x40)
    for i in range(8):
        struct.pack_into('<I', page, i * 4, 0xE59FF018)      # ldr pc,[pc,#0x18]
        struct.pack_into('<I', page, 0x20 + i * 4, VECTORS[i])
    return bytes(page)


class Machine:
    def __init__(self, trace=False):
        self.trace = trace
        self.mmio = {}                     # word addr -> value
        self.mmio_log = collections.Counter()
        raw = bytearray(open(FW, "rb").read())
        assert struct.unpack_from('<I', raw, 0x30217848 - BASE)[0] == 0xEE010F10
        struct.pack_into('<I', raw, 0x30217848 - BASE, 0xE1A00000)   # keep MMU off

        uc = self.uc = Uc(UC_ARCH_ARM, UC_MODE_ARM | UC_MODE_LITTLE_ENDIAN)
        uc.mem_map(BASE, RAM_SIZE)
        uc.mem_write(BASE, bytes(raw))
        uc.mem_map(0, 0x1000)
        uc.mem_write(0, build_vector_page())
        # pre-map the known peripheral windows so register write-back is reliable
        for pa, psz in [(0x08000000, 0x10000), (0x20000000, 0x100000),
                        (0x48000000, 0x10000), (0x10000000, 0x100000)]:
            try:
                uc.mem_map(pa, psz)
            except UcError:
                pass
        uc.reg_write(UC_ARM_REG_SP, 0x31FF0000)
        uc.reg_write(UC_ARM_REG_PC, BASE)
        uc.reg_write(UC_ARM_REG_CPSR, 0xD3)

        uc.hook_add(UC_HOOK_MEM_UNMAPPED, self._unmapped)
        uc.hook_add(UC_HOOK_MEM_READ, self._read)
        uc.hook_add(UC_HOOK_MEM_WRITE, self._write)
        uc.hook_add(UC_HOOK_INTR, self._intr)

    # ---------------- bus -------------------------------------------------
    def _in_ram(self, a):
        return BASE <= a < BASE + RAM_SIZE

    def _unmapped(self, uc, access, addr, size, value, user):
        page = addr & ~0xFFF
        try:
            uc.mem_map(page, 0x1000)
        except UcError:
            pass
        return True

    def _read(self, uc, access, addr, size, value, user):
        if self._in_ram(addr):
            return
        self.mmio_log[('R', addr & ~3)] += 1
        word = addr & ~3
        if word in SELF_CLEAR:
            # hardware: reading the busy register reports "done"
            self.mmio[word] = 0
            uc.mem_write(word, b'\x00\x00\x00\x00')

    def _write(self, uc, access, addr, size, value, user):
        if self._in_ram(addr):
            return
        word = addr & ~3
        self.mmio_log[('W', word)] += 1
        v = (value or 0) & 0xFFFFFFFF
        if word in SELF_CLEAR:
            v = 0                      # complete instantly
        self.mmio[word] = v
        uc.mem_write(word, struct.pack('<I', v))
        if self.trace:
            print(f"    W [0x{word:08x}] = 0x{v:08x}  pc=0x{uc.reg_read(UC_ARM_REG_PC):08x}")

    def _intr(self, uc, intno, user):
        pass                            # SVC is swallowed (handler is a no-op)

    # ---------------- control ---------------------------------------------
    def run(self, max_ins, chunk=5_000_000, verbose=True):
        done = 0
        while done < max_ins:
            try:
                self.uc.emu_start(self.uc.reg_read(UC_ARM_REG_PC), 0,
                                  count=min(chunk, max_ins - done),
                                  timeout=25 * 1000 * 1000)
            except UcError as e:
                if verbose:
                    print("stop:", e, "pc=0x%08x" % self.uc.reg_read(UC_ARM_REG_PC))
                break
            done += chunk
            if verbose and done % 50_000_000 == 0:
                print(f"  {done//1_000_000}M  pc=0x{self.uc.reg_read(UC_ARM_REG_PC):08x} "
                      f"sp=0x{self.uc.reg_read(UC_ARM_REG_SP):08x}")
        return done

    def dump(self, path, base=0, size=0x100000):
        open(path, "wb").write(self.uc.mem_read(base or BASE, size))


def write_png(path, w, h, pixels):        # pixels: list of (r,g,b), row-major
    raw = b''.join(b'\x00' + b''.join(struct.pack('BBB', *p) for p in pixels[y*w:(y+1)*w])
                   for y in range(h))
    def chunk(tag, data):
        c = struct.pack('>I', len(data)) + tag + data
        return c + struct.pack('>I', zlib.crc32(tag + data) & 0xFFFFFFFF)
    png = (b'\x89PNG\r\n\x1a\n'
           + chunk(b'IHDR', struct.pack('>IIBBBBB', w, h, 8, 2, 0, 0, 0))
           + chunk(b'IDAT', zlib.compress(raw, 6))
           + chunk(b'IEND', b''))
    open(path, 'wb').write(png)


def main():
    args = [a for a in sys.argv[1:] if not a.startswith('--')]
    n = int(args[0]) if args else 100_000_000
    m = Machine(trace='--trace' in sys.argv)
    print("devices:", {hex(k): v for k, v in DEVICES.items()})
    got = m.run(n)
    print(f"\nran {got//1_000_000}M instructions")
    print("pc=0x%08x lr=0x%08x sp=0x%08x cpsr=0x%08x" % (
        m.uc.reg_read(UC_ARM_REG_PC), m.uc.reg_read(UC_ARM_REG_LR),
        m.uc.reg_read(UC_ARM_REG_SP), m.uc.reg_read(UC_ARM_REG_CPSR)))
    print("\nmost-accessed MMIO registers:")
    for (op, a), c in m.mmio_log.most_common(20):
        print(f"  {op} 0x{a:08x}: {c}")
    m.dump("/home/zzq/code/wanhong/emu/sdram.bin", BASE, 0x200000)


if __name__ == '__main__':
    main()
