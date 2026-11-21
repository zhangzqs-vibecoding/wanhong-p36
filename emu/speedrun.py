#!/usr/bin/env python3
"""Speed-run: bypass delay calibration, auto-satisfy MMIO polls, watch LCD regs."""
import sys, struct, collections
from unicorn import *
from unicorn.arm_const import *

FW = "/home/zzq/code/wanhong/sdcard/sword_elfd.bin"; BASE = 0x30000000; RAM = 0x2000000
data = bytearray(open(FW, "rb").read())
struct.pack_into('<I', data, 0x30217848 - BASE, 0xE1A00000)          # MMU enable -> NOP

uc = Uc(UC_ARCH_ARM, UC_MODE_ARM | UC_MODE_LITTLE_ENDIAN)
uc.mem_map(BASE, RAM); uc.mem_write(BASE, bytes(data)); uc.mem_map(0, 0x1000)
uc.reg_write(UC_ARM_REG_SP, 0x31ff0000)
uc.reg_write(UC_ARM_REG_PC, BASE)
uc.reg_write(UC_ARM_REG_CPSR, 0xD3)

lcd = []            # (pc, addr, val) writes to 0x2002c000 page
def unmap(uc, acc, addr, sz, val, u):
    try: uc.mem_map(addr & ~0xfff, 0x1000)
    except UcError: pass
    return True
def rd(uc, acc, addr, sz, val, u):
    if addr < BASE:
        try: uc.mem_write(addr, b'\x00' * sz)
        except UcError: pass
def wr(uc, acc, addr, sz, val, u):
    if 0x2002c000 <= addr < 0x2002d000:
        lcd.append((uc.reg_read(UC_ARM_REG_PC), addr, (val or 0) & 0xffffffff))
def on_intr(uc, i, u): pass

uc.hook_add(UC_HOOK_MEM_UNMAPPED, unmap)
uc.hook_add(UC_HOOK_MEM_READ, rd)
uc.hook_add(UC_HOOK_MEM_WRITE, wr)
uc.hook_add(UC_HOOK_INTR, on_intr)

TOTAL = int(sys.argv[1]) if len(sys.argv) > 1 else 200_000_000
CHUNK = 5_000_000
done = 0; prev = None; same = 0
while done < TOTAL:
    pc = uc.reg_read(UC_ARM_REG_PC)
    same = same + 1 if pc == prev else 0
    prev = pc
    if same >= 8:
        print(f"STUCK pc=0x{pc:08x} after {done//1_000_000}M"); break
    try:
        uc.emu_start(pc, 0, count=CHUNK, timeout=25 * 1000 * 1000)
    except UcError as e:
        print("ERR", e, "pc=0x%08x" % uc.reg_read(UC_ARM_REG_PC)); break
    done += CHUNK
    if done % 50_000_000 == 0:
        print(f"  {done//1_000_000}M PC=0x{uc.reg_read(UC_ARM_REG_PC):08x} SP=0x{uc.reg_read(UC_ARM_REG_SP):08x}")

print(f"\nTOTAL {done//1_000_000}M  PC=0x{uc.reg_read(UC_ARM_REG_PC):08x} "
      f"LR=0x{uc.reg_read(UC_ARM_REG_LR):08x} SP=0x{uc.reg_read(UC_ARM_REG_SP):08x}")
print("LCD registers 0x2002c080-0x2002c08c now:",
      [hex(struct.unpack_from('<I', uc.mem_read(a, 4))[0]) for a in range(0x2002c080, 0x2002c090, 4)])
print("distinct LCD-reg values written (addr -> last 6 vals):")
byaddr = collections.defaultdict(list)
for pc, a, v in lcd:
    byaddr[a].append(v)
for a, vs in sorted(byaddr.items()):
    print(f"  0x{a:08x}: {[hex(v) for v in vs[-6:]]}  n={len(vs)}")
# dump SDRAM start for inspection
open("/home/zzq/code/wanhong/emu/sdram_start.bin", "wb").write(uc.mem_read(BASE, 0x40000))
print("saved emu/sdram_start.bin")
