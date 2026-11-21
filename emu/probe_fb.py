#!/usr/bin/env python3
"""Probe: log all MMIO writes, find SDRAM-range values (framebuffer / table pointers)."""
import sys, struct, collections
from unicorn import *
from unicorn.arm_const import *

FW = "/home/zzq/code/wanhong/sdcard/sword_elfd.bin"
BASE = 0x30000000; RAM = 0x2000000

data = bytearray(open(FW, "rb").read())
struct.pack_into('<I', data, 0x30217848 - BASE, 0xE1A00000)   # MMU enable -> NOP

uc = Uc(UC_ARCH_ARM, UC_MODE_ARM | UC_MODE_LITTLE_ENDIAN)
uc.mem_map(BASE, RAM); uc.mem_write(BASE, bytes(data)); uc.mem_map(0, 0x1000)
uc.reg_write(UC_ARM_REG_SP, 0x31ff0000)
uc.reg_write(UC_ARM_REG_PC, BASE)
uc.reg_write(UC_ARM_REG_CPSR, 0xD3)

writes = collections.defaultdict(list)

def unmap(uc, acc, addr, sz, val, u):
    try: uc.mem_map(addr & ~0xfff, 0x1000)
    except UcError: pass
    return True

def rd(uc, acc, addr, sz, val, u):
    if addr < BASE:
        try: uc.mem_write(addr, b'\x00' * sz)   # auto-clear status -> polls succeed
        except UcError: pass

def wr(uc, acc, addr, sz, val, u):
    if addr < BASE:
        writes[(addr & ~0xfff, addr & 0xfff)].append(
            ((val or 0) & 0xffffffff, uc.reg_read(UC_ARM_REG_PC)))

def on_intr(uc, i, u): pass

uc.hook_add(UC_HOOK_MEM_UNMAPPED, unmap)
uc.hook_add(UC_HOOK_MEM_READ, rd)
uc.hook_add(UC_HOOK_MEM_WRITE, wr)
uc.hook_add(UC_HOOK_INTR, on_intr)

N = int(sys.argv[1]) if len(sys.argv) > 1 else 60_000_000
try:
    uc.emu_start(BASE, 0, count=N, timeout=110 * 1000 * 1000)
except UcError as e:
    print("err", e)
print("PC=0x%08x" % uc.reg_read(UC_ARM_REG_PC))

print("\n=== writes carrying SDRAM-range values (framebuffer / table pointers) ===")
for (page, off), lst in sorted(writes.items()):
    cands = {v for v, pc in lst if BASE <= v < BASE + RAM}
    if cands:
        print(f"  0x{page+off:08x} <- {sorted(hex(c) for c in cands)} "
              f"pcs={sorted({hex(pc) for v,pc in lst})[:3]}  n={len(lst)}")

print("\n=== peripherals touched ===")
bypage = collections.defaultdict(set)
for (page, off) in writes:
    bypage[page].add(off)
for p, offs in sorted(bypage.items()):
    print(f"  0x{p:08x}: {len(offs)} regs: {sorted(hex(o) for o in offs)[:24]}")
