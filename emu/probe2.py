#!/usr/bin/env python3
import sys, struct, time, collections
from unicorn import *
from unicorn.arm_const import *

FW = "/home/zzq/code/wanhong/sdcard/sword_elfd.bin"
BASE = 0x30000000
RAM_SIZE = 0x2000000  # 32 MB SDRAM

MAX_INS = int(sys.argv[1]) if len(sys.argv) > 1 else 200_000_000
data = open(FW, "rb").read()
uc = Uc(UC_ARCH_ARM, UC_MODE_ARM | UC_MODE_LITTLE_ENDIAN)
uc.mem_map(BASE, RAM_SIZE)
uc.mem_write(BASE, data)
for a in (0x0, 0xFFFF0000):
    try: uc.mem_map(a, 0x1000)
    except UcError: pass
uc.reg_write(UC_ARM_REG_SP, 0x31ff0000)
uc.reg_write(UC_ARM_REG_PC, BASE)
uc.reg_write(UC_ARM_REG_CPSR, 0xD3)

mmio = collections.defaultdict(lambda: [0, None, 0])  # (op,page)->[count, first_pc, first_size]
unmapped = {}
def is_ram(a): return BASE <= a < BASE + RAM_SIZE

def on_unmapped(uc, access, address, size, value, user):
    page = address & ~0xFFF
    if page not in unmapped:
        unmapped[page] = (access, address, size, uc.reg_read(UC_ARM_REG_PC))
        print(f"  UNMAPPED {access} addr=0x{address:08x} size={size} pc=0x{uc.reg_read(UC_ARM_REG_PC):08x}")
    try: uc.mem_map(page, 0x1000, UC_PROT_ALL)
    except UcError: pass
    return True

def on_rw(uc, access, address, size, value, user):
    if is_ram(address): return
    op = "R" if access == UC_MEM_READ else "W"
    k = (op, address & ~0xFFF)
    e = mmio[k]
    e[0] += 1
    if e[1] is None:
        e[1] = uc.reg_read(UC_ARM_REG_PC); e[2] = size

def on_intr(uc, intno, user):
    print(f"  INTR {intno} at pc=0x{uc.reg_read(UC_ARM_REG_PC):08x}")

uc.hook_add(UC_HOOK_MEM_UNMAPPED, on_unmapped)
uc.hook_add(UC_HOOK_MEM_READ | UC_HOOK_MEM_WRITE, on_rw)
uc.hook_add(UC_HOOK_INTR, on_intr)

t0 = time.time(); err = None
try:
    uc.emu_start(BASE, 0, count=MAX_INS, timeout=90*1000*1000)
except UcError as e:
    err = e
dt = time.time() - t0
print(f"\n=== stopped {dt:.2f}s err={err} ===")
print(f"PC=0x{uc.reg_read(UC_ARM_REG_PC):08x} SP=0x{uc.reg_read(UC_ARM_REG_SP):08x} LR=0x{uc.reg_read(UC_ARM_REG_LR):08x} CPSR=0x{uc.reg_read(UC_ARM_REG_CPSR):08x}")
print(f"\n=== MMIO pages (op, page, count, firstPC, size) ===")
for (op,page),(cnt,pc,sz) in sorted(mmio.items(), key=lambda x:(x[0][1],x[0][0])):
    print(f"  {op} 0x{page:08x}  n={cnt:7d}  firstPC=0x{(pc or 0):08x} size={sz}")
print(f"total distinct MMIO pages: {len(mmio)}")
