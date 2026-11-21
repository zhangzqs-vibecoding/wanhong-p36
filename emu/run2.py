#!/usr/bin/env python3
import sys, struct, time, collections
from unicorn import *
from unicorn.arm_const import *

FW = "/home/zzq/code/wanhong/sdcard/sword_elfd.bin"
BASE = 0x30000000
RAM_SIZE = 0x2000000  # 32 MB

TOTAL = int(sys.argv[1]) if len(sys.argv) > 1 else 120_000_000
CHUNK = 2_000_000

data = bytearray(open(FW, "rb").read())
# Patch MMU-enable MCR p15,0,r0,c1,c0,0 (orr #1) at 0x30217848 -> NOP
mmu_off = 0x30217848 - BASE
assert struct.unpack_from('<I', data, mmu_off)[0] == 0xEE010F10, hex(struct.unpack_from('<I',data,mmu_off)[0])
struct.pack_into('<I', data, mmu_off, 0xE1A00000)
print(f"patched MMU-enable @0x30217848 -> NOP")

uc = Uc(UC_ARCH_ARM, UC_MODE_ARM | UC_MODE_LITTLE_ENDIAN)
uc.mem_map(BASE, RAM_SIZE)
uc.mem_write(BASE, bytes(data))
for a in (0x0, 0xFFFF0000):
    try: uc.mem_map(a, 0x1000)
    except UcError: pass
uc.reg_write(UC_ARM_REG_SP, 0x31ff0000)
uc.reg_write(UC_ARM_REG_PC, BASE)
uc.reg_write(UC_ARM_REG_CPSR, 0xD3)

mmio = collections.defaultdict(lambda: [0, None, 0])
def on_unmapped(uc, access, address, size, value, user):
    page = address & ~0xFFF
    e = mmio[('?', page)]
    e[0]+=1
    if e[1] is None:
        e[1]=uc.reg_read(UC_ARM_REG_PC); e[2]=size
        kind={UC_MEM_READ:"R",UC_MEM_WRITE:"W",UC_MEM_FETCH:"X"}.get(access,str(access))
        print(f"  NEW MMIO page 0x{page:08x} {kind} addr=0x{address:08x} sz={size} pc=0x{uc.reg_read(UC_ARM_REG_PC):08x}")
    try: uc.mem_map(page, 0x1000, UC_PROT_ALL)
    except UcError: pass
    return True

intr_count=[0]
last_intr=[None]
def on_intr(uc, intno, user):
    pc=uc.reg_read(UC_ARM_REG_PC)
    intr_count[0]+=1
    if last_intr[0]!=intno or intr_count[0]<5:
        print(f"  INTR {intno} at pc=0x{pc:08x} (#{intr_count[0]})")
    last_intr[0]=intno
    # stop on repeated insane interrupts
    if intr_count[0]>50:
        uc.emu_stop()

uc.hook_add(UC_HOOK_MEM_UNMAPPED, on_unmapped)
uc.hook_add(UC_HOOK_INTR, on_intr)

t0=time.time(); done=0; prev_pc=None; same=0; err=None
while done < TOTAL:
    pc=uc.reg_read(UC_ARM_REG_PC)
    if pc==prev_pc:
        same+=1
    else:
        same=0
    prev_pc=pc
    if same>=6:
        print(f"  [stuck loop at pc=0x{pc:08x}]"); break
    try:
        uc.emu_start(pc, 0, count=CHUNK, timeout=20*1000*1000)
    except UcError as e:
        err=e; print("EMU ERR", e); break
    done+=CHUNK
    if done % 20_000_000 == 0:
        print(f"  ...{done//1_000_000}M ins  PC=0x{uc.reg_read(UC_ARM_REG_PC):08x} SP=0x{uc.reg_read(UC_ARM_REG_SP):08x}")

print(f"\n=== stopped {time.time()-t0:.1f}s after ~{done//1_000_000}M ins err={err} ===")
print(f"PC=0x{uc.reg_read(UC_ARM_REG_PC):08x} SP=0x{uc.reg_read(UC_ARM_REG_SP):08x} LR=0x{uc.reg_read(UC_ARM_REG_LR):08x} CPSR=0x{uc.reg_read(UC_ARM_REG_CPSR):08x}")
print("=== MMIO pages touched ===")
for (op,page),(cnt,pc,sz) in sorted(mmio.items(), key=lambda x:x[0][1]):
    print(f"  0x{page:08x}  n={cnt:7d} firstPC=0x{(pc or 0):08x} size={sz}")
print("distinct MMIO pages:", len(mmio))
