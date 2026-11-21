#!/usr/bin/env python3
import sys, struct, time, collections
from unicorn import *
from unicorn.arm_const import *
from capstone import *

FW="/home/zzq/code/wanhong/sdcard/sword_elfd.bin"; BASE=0x30000000
data=bytearray(open(FW,"rb").read())
struct.pack_into('<I',data,0x30217848-BASE,0xE1A00000)

uc=Uc(UC_ARCH_ARM,UC_MODE_ARM|UC_MODE_LITTLE_ENDIAN)
uc.mem_map(BASE,0x2000000); uc.mem_write(BASE,bytes(data))
uc.mem_map(0,0x1000)
uc.reg_write(UC_ARM_REG_SP,0x31ff0000); uc.reg_write(UC_ARM_REG_PC,BASE); uc.reg_write(UC_ARM_REG_CPSR,0xD3)

def unmap(uc,acc,addr,sz,val,u):
    try: uc.mem_map(addr&~0xfff,0x1000)
    except: pass
    return True
intr=collections.Counter()
def on_intr(uc,intno,u):
    intr[intno]+=1
    # skip SVC (intno 2): handler is a no-op trampoline; just continue
uc.hook_add(UC_HOOK_MEM_UNMAPPED,unmap)
uc.hook_add(UC_HOOK_INTR,on_intr)

t0=time.time()
try: uc.emu_start(BASE,0x300204a4,count=300_000_000,timeout=120*1000*1000)
except UcError as e: print("err",e)
print(f"reached in {time.time()-t0:.1f}s PC=0x{uc.reg_read(UC_ARM_REG_PC):08x} intr={dict(intr)}")
print("mem[0x08000000:0x60]=",uc.mem_read(0x08000000,0x60).hex())
md=Cs(CS_ARCH_ARM,CS_MODE_ARM|CS_MODE_LITTLE_ENDIAN)
print("--- single step ---")
for s in range(16):
    pc=uc.reg_read(UC_ARM_REG_PC); r0=uc.reg_read(UC_ARM_REG_R0); r2=uc.reg_read(UC_ARM_REG_R2); cpsr=uc.reg_read(UC_ARM_REG_CPSR)
    ins=next(md.disasm(bytes(uc.mem_read(pc,4)),pc),None)
    txt=f"{ins.mnemonic} {ins.op_str}" if ins else "?"
    print(f"  pc=0x{pc:08x} r0=0x{r0:08x} r2=0x{r2:08x} Z={(cpsr>>30)&1} T={(cpsr>>5)&1} | {txt}")
    try: uc.emu_start(pc,0,count=1,timeout=5*1000*1000)
    except UcError as e: print("   step err",e); break
