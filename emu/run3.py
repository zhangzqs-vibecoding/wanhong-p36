#!/usr/bin/env python3
import sys, struct, time, collections
from unicorn import *
from unicorn.arm_const import *

FW = "/home/zzq/code/wanhong/sdcard/sword_elfd.bin"
BASE = 0x30000000
RAM_SIZE = 0x2000000
TOTAL = int(sys.argv[1]) if len(sys.argv) > 1 else 80_000_000

data = bytearray(open(FW, "rb").read())
# patch MMU enable -> NOP
struct.pack_into('<I', data, 0x30217848-BASE, 0xE1A00000)

uc = Uc(UC_ARCH_ARM, UC_MODE_ARM | UC_MODE_LITTLE_ENDIAN)
uc.mem_map(BASE, RAM_SIZE); uc.mem_write(BASE, bytes(data))
uc.mem_map(0x0, 0x1000); uc.mem_write(0x0, bytes(data[0:0x20]))   # vector table at 0
try: uc.mem_map(0xFFFF0000,0x1000); uc.mem_write(0xFFFF0000, bytes(data[0:0x20]))
except UcError: pass
uc.reg_write(UC_ARM_REG_SP, 0x31ff0000)
uc.reg_write(UC_ARM_REG_PC, BASE)
uc.reg_write(UC_ARM_REG_CPSR, 0xD3)

mmio=collections.Counter()
def on_unmapped(uc, access, address, size, value, user):
    page=address & ~0xFFF; mmio[page]+=1
    if mmio[page]==1:
        print(f"  NEW MMIO 0x{page:08x} first addr=0x{address:08x} pc=0x{uc.reg_read(UC_ARM_REG_PC):08x}")
    try: uc.mem_map(page,0x1000,UC_PROT_ALL)
    except UcError: pass
    return True

log=[]
def on_read(uc, access, address, size, value, user):
    if 0x08000000 <= address < 0x08000100:
        log.append(("R",address,uc.reg_read(UC_ARM_REG_PC),value))
def on_write(uc, access, address, size, value, user):
    if 0x08000000 <= address < 0x08000100:
        log.append(("W",address,uc.reg_read(UC_ARM_REG_PC),value))

intr=collections.Counter()
def on_intr(uc,intno,user):
    intr[intno]+=1
    if intr[intno]<=3: print(f"  INTR {intno} pc=0x{uc.reg_read(UC_ARM_REG_PC):08x}")
    if sum(intr.values())>200: uc.emu_stop()

uc.hook_add(UC_HOOK_MEM_UNMAPPED,on_unmapped)
uc.hook_add(UC_HOOK_MEM_READ,on_read)
uc.hook_add(UC_HOOK_MEM_WRITE,on_write)
uc.hook_add(UC_HOOK_INTR,on_intr)

# pool literal at 0x30020560 used by 0x30020454 block
print("literal @0x30020560 =", hex(struct.unpack_from('<I',data,0x30020560-BASE)[0]))

t0=time.time(); err=None
try: uc.emu_start(BASE,0,count=TOTAL,timeout=120*1000*1000)
except UcError as e: err=e
print(f"\nstopped {time.time()-t0:.1f}s err={err}")
print(f"PC=0x{uc.reg_read(UC_ARM_REG_PC):08x} SP=0x{uc.reg_read(UC_ARM_REG_SP):08x} LR=0x{uc.reg_read(UC_ARM_REG_LR):08x} CPSR=0x{uc.reg_read(UC_ARM_REG_CPSR):08x}")
print("INTR counts:",dict(intr))
print("\n=== first 40 accesses to 0x08000000 block ===")
for op,a,pc,v in log[:40]:
    print(f"  {op} [0x{a:08x}] pc=0x{pc:08x} val=0x{(v or 0)&0xffffffff:08x}")
print("last 6:")
for op,a,pc,v in log[-6:]:
    print(f"  {op} [0x{a:08x}] pc=0x{pc:08x} val=0x{(v or 0)&0xffffffff:08x}")
print("\nMMIO pages:", {hex(k):v for k,v in sorted(mmio.items())})
