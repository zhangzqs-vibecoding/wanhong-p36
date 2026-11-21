#!/usr/bin/env python3
import sys, struct, time, collections
from unicorn import *
from unicorn.arm_const import *
from capstone import *

FW = "/home/zzq/code/wanhong/sdcard/sword_elfd.bin"; BASE=0x30000000
data = bytearray(open(FW,"rb").read())
struct.pack_into('<I', data, 0x30217848-BASE, 0xE1A00000)   # MMU enable -> NOP

# Build a low-memory vector trampoline: each vector = ldr pc,[pc,#0x18]; literals = targets
targets = {0:0x30000020,1:0x302176c0,2:0x30000100,3:0x302176fc,4:0x30217738,5:0x30000020,6:0x30217618,7:0x3021766c}
vec = bytearray(0x40)
for i in range(8):
    struct.pack_into('<I', vec, i*4, 0xE59FF018)          # ldr pc,[pc,#0x18]
    struct.pack_into('<I', vec, 0x20+i*4, targets[i])

uc = Uc(UC_ARCH_ARM, UC_MODE_ARM|UC_MODE_LITTLE_ENDIAN)
uc.mem_map(BASE, 0x2000000); uc.mem_write(BASE, bytes(data))
uc.mem_map(0x0, 0x1000); uc.mem_write(0x0, bytes(vec))
uc.reg_write(UC_ARM_REG_SP, 0x31ff0000)
uc.reg_write(UC_ARM_REG_PC, BASE)
uc.reg_write(UC_ARM_REG_CPSR, 0xD3)

mmio=collections.Counter()
def unmap(uc,acc,addr,sz,val,u):
    page=addr&~0xfff; mmio[page]+=1
    if mmio[page]==1: print(f"  NEW MMIO 0x{page:08x} pc=0x{uc.reg_read(UC_ARM_REG_PC):08x}")
    try: uc.mem_map(page,0x1000,UC_PROT_ALL)
    except: pass
    return True
uc.hook_add(UC_HOOK_MEM_UNMAPPED,unmap)

TARGET = int(sys.argv[1],16) if len(sys.argv)>1 else 0x300204a4
print("running until 0x%08x ..."%TARGET)
t0=time.time(); err=None
try:
    uc.emu_start(BASE, TARGET, count=300_000_000, timeout=120*1000*1000)
except UcError as e:
    err=e
pc=uc.reg_read(UC_ARM_REG_PC)
print(f"stopped {time.time()-t0:.1f}s err={err} PC=0x{pc:08x} SP=0x{uc.reg_read(UC_ARM_REG_SP):08x} LR=0x{uc.reg_read(UC_ARM_REG_LR):08x} CPSR=0x{uc.reg_read(UC_ARM_REG_CPSR):08x}")
print("MMIO pages:", {hex(k):v for k,v in sorted(mmio.items())})
print("mem[0x08000000:0x60]=", uc.mem_read(0x08000000,0x60).hex())
PY