from unicorn import *
from unicorn.arm_const import *
import struct, collections, sys
FW="sdcard/sword_elfd.bin"; BASE=0x30000000
data=bytearray(open(FW,"rb").read()); struct.pack_into('<I',data,0x30217848-BASE,0xE1A00000)
uc=Uc(UC_ARCH_ARM,UC_MODE_ARM|UC_MODE_LITTLE_ENDIAN)
uc.mem_map(BASE,0x2000000); uc.mem_write(BASE,bytes(data)); uc.mem_map(0,0x1000)
uc.reg_write(UC_ARM_REG_SP,0x31ff0000); uc.reg_write(UC_ARM_REG_PC,BASE); uc.reg_write(UC_ARM_REG_CPSR,0xD3)
rec=collections.defaultdict(list)
def unmap(uc,acc,addr,sz,val,u):
    try: uc.mem_map(addr&~0xfff,0x1000)
    except: pass
    return True
def rd(uc,acc,addr,sz,val,u):
    if 0x08000000<=addr<0x08001000:
        cur=struct.unpack('<I',uc.mem_read(addr&~3,4))[0]
        rec[addr&~3].append(('R',uc.reg_read(UC_ARM_REG_PC),cur))
def wr(uc,acc,addr,sz,val,u):
    if 0x08000000<=addr<0x08001000:
        rec[addr&~3].append(('W',uc.reg_read(UC_ARM_REG_PC),val or 0))
intr=collections.Counter()
def on_intr(uc,i,u): intr[i]+=1
uc.hook_add(UC_HOOK_MEM_UNMAPPED,unmap); uc.hook_add(UC_HOOK_MEM_READ,rd); uc.hook_add(UC_HOOK_MEM_WRITE,wr); uc.hook_add(UC_HOOK_INTR,on_intr)
try: uc.emu_start(BASE,0,count=50_000_000,timeout=110*1000*1000)
except UcError as e: print("err",e)
print("PC=0x%08x intr=%s"%(uc.reg_read(UC_ARM_REG_PC),dict(intr)))
for a in sorted(rec):
    lst=rec[a]; ws=[v for o,pc,v in lst if o=='W']; rs=[v for o,pc,v in lst if o=='R']
    pcs=sorted({pc for o,pc,v in lst})
    print(f"0x{a:08x}: n={len(lst):4d} W={len(ws):3d} R={len(rs):3d} lastW={hex(ws[-1]) if ws else '-':>10} Rsample={[hex(v) for v in rs[:3]]} pcs={[hex(p) for p in pcs[:3]]}")
