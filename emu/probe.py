#!/usr/bin/env python3
import sys, struct, time
from unicorn import *
from unicorn.arm_const import *

FW = "/home/zzq/code/wanhong/sdcard/sword_elfd.bin"
BASE = 0x30000000
RAM_SIZE = 0x2000000  # 32 MB SDRAM

MAX_INS = int(sys.argv[1]) if len(sys.argv) > 1 else 2_000_000

data = open(FW, "rb").read()
print(f"firmware {len(data)} bytes -> 0x{BASE:x}..0x{BASE+len(data):x}")

uc = Uc(UC_ARCH_ARM, UC_MODE_ARM | UC_MODE_LITTLE_ENDIAN)
uc.mem_map(BASE, RAM_SIZE)
uc.mem_write(BASE, data)
# low + high vector pages just in case
for a in (0x0, 0xFFFF0000):
    try:
        uc.mem_map(a, 0x1000)
    except UcError as e:
        print("map fail", hex(a), e)

uc.reg_write(UC_ARM_REG_SP, 0x31ff0000)
uc.reg_write(UC_ARM_REG_PC, BASE)
uc.reg_write(UC_ARM_REG_CPSR, 0xD3)  # SVC, ARM, IRQ+FIQ disabled

seen = {}
order = []
def on_unmapped(uc, access, address, size, value, user):
    page = address & ~0xFFF
    if page not in seen:
        pc = uc.reg_read(UC_ARM_REG_PC)
        lr = uc.reg_read(UC_ARM_REG_LR)
        kind = {UC_MEM_READ:"R", UC_MEM_WRITE:"W", UC_MEM_FETCH:"X", UC_MEM_READ_UNMAPPED:"R?", UC_MEM_WRITE_UNMAPPED:"W?", UC_MEM_FETCH_UNMAPPED:"X?"}.get(access, str(access))
        seen[page] = (kind, address, size, pc, lr)
        order.append(page)
        print(f"  UNMAPPED {kind} addr=0x{address:08x} size={size} pc=0x{pc:08x} lr=0x{lr:08x}")
    try:
        uc.mem_map(page, 0x1000, UC_PROT_ALL)
    except UcError:
        pass
    return True

def on_intr(uc, intno, user):
    pc = uc.reg_read(UC_ARM_REG_PC)
    print(f"  INTR {intno} at pc=0x{pc:08x}")

uc.hook_add(UC_HOOK_MEM_UNMAPPED, on_unmapped)
uc.hook_add(UC_HOOK_INTR, on_intr)

t0 = time.time()
err = None
try:
    uc.emu_start(BASE, 0, count=MAX_INS, timeout=60*1000*1000)
except UcError as e:
    err = e
dt = time.time() - t0

pc = uc.reg_read(UC_ARM_REG_PC)
sp = uc.reg_read(UC_ARM_REG_SP)
lr = uc.reg_read(UC_ARM_REG_LR)
cpsr = uc.reg_read(UC_ARM_REG_CPSR)
print(f"\n=== stopped after {dt:.2f}s  err={err} ===")
print(f"PC=0x{pc:08x} SP=0x{sp:08x} LR=0x{lr:08x} CPSR=0x{cpsr:08x}")
print(f"distinct unmapped pages touched: {len(order)}")
for p in order:
    k,a,s,pcp,lrp = seen[p]
    print(f"  page 0x{p:08x}  first {k} addr=0x{a:08x} size={s} pc=0x{pcp:08x}")
