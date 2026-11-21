#!/usr/bin/env python3
import sys, struct, time, collections
from unicorn import *
from unicorn.arm_const import *

FW="/home/zzq/code/wanhong/sdcard/sword_elfd.bin"; BASE=0x30000000; RAM=0x2000000
data=bytearray(open(FW,"rb").read())
struct.pack_into('<I',data,0x30217848-BASE,0xE1A00000)
uc=Uc(UC_ARCH_ARM,UC_MODE_ARM|UC_MODE_LITTLE_ENDIAN)
uc.mem_map(BASE,RAM); uc.mem_write(BASE,bytes(data))
uc.mem_map(0,0x1000)
uc.reg_write(UC_ARM_REG_SP,0x31ff0000); uc.reg_write(UC_ARM_REG_PC,BASE); uc.reg_write(UC_ARM_REG_CPSR,0xD3)

TOTAL=int(sys.argv[1]) if len(sys.argv)>1 else 100_000_000
CHUNK=2_000_000
mmio=collections.Counter(); addrs=collections.Counter()
def unmap(uc,acc,addr,sz,val,u):
    p=addr&~0xfff; mmio[p]+=1
    if mmio[p]==1: print(f"  NEW MMIO page 0x{p:08x} pc=0x{uc.reg_read(UC_ARM_REG_PC):08x}")
    try: uc.mem_map(p,0x1000,UC_PROT_ALL)
    except: pass
    return True
READ_AUTOCLEAR=True
def on_read(uc,acc,addr,sz,val,u):
    if addr < BASE:  # MMIO / low
        addrs[('R',addr&~0xfff)]+=1
        if READ_AUTOCLEAR:
            try: uc.mem_write(addr, b'\x00'*sz)   # auto-clear status so polls succeed next time
            except: pass
def on_write(uc,acc,addr,sz,val,u):
    if addr < BASE:
        addrs[('W',addr&~0xfff)]+=1
intr=collections.Counter()
def on_intr(uc,intno,u):
    intr[intno]+=1
uc.hook_add(UC_HOOK_MEM_UNMAPPED,unmap)
uc.hook_add(UC_HOOK_MEM_READ,on_read)
uc.hook_add(UC_HOOK_MEM_WRITE,on_write)
uc.hook_add(UC_HOOK_INTR,on_intr)

t0=time.time(); done=0; prev=None; same=0
while done<TOTAL:
    pc=uc.reg_read(UC_ARM_REG_PC)
    same = same+1 if pc==prev else 0
    prev=pc
    if same>=10:
        print(f"  STUCK at pc=0x{pc:08x}"); break
    try: uc.emu_start(pc,0,count=CHUNK,timeout=15*1000*1000)
    except UcError as e: print("ERR",e); break
    done+=CHUNK
print(f"\n{done//1_000_000}M ins {time.time()-t0:.1f}s PC=0x{uc.reg_read(UC_ARM_REG_PC):08x} SP=0x{uc.reg_read(UC_ARM_REG_SP):08x} LR=0x{uc.reg_read(UC_ARM_REG_LR):08x} CPSR=0x{uc.reg_read(UC_ARM_REG_CPSR):08x} intr={dict(intr)}")
print("MMIO pages:",{hex(k):v for k,v in sorted(mmio.items())})
print("RW pages:",{f"{o}0x{k:08x}":v for (o,k),v in sorted(addrs.items())})
