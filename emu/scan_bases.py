import struct
from capstone import *
data=open("sdcard/sword_elfd.bin","rb").read(); BASE=0x30000000
md=Cs(CS_ARCH_ARM,CS_MODE_ARM|CS_MODE_LITTLE_ENDIAN); md.skipdata=True
targets={0x08000000:"SYS/INTC",0x20010000:"PER10000",0x20020000:"PER20000",
         0x20026000:"PER26000",0x2002c000:"PER2C000",0x2002d000:"PER2D000",
         0x48000000:"DSP/IDMA"}
out=open("emu/bases.txt","w")
for t,name in targets.items():
    pat=struct.pack('<I',t); hits=[]; i=data.find(pat)
    while i!=-1:
        if i%4==0: hits.append(i)
        i=data.find(pat,i+1)
    out.write(f"\n##### 0x{t:08x} {name}: {len(hits)} literals #####\n")
    for h in hits[:5]:
        a=BASE+h; st=max(0,h-32)
        out.write(f"-- literal @0x{a:08x}\n")
        for ins in md.disasm(data[st:h+4], BASE+st):
            m=" <<<" if ins.address==a else ""
            out.write(f"   0x{ins.address:08x}: {ins.mnemonic:8} {ins.op_str}{m}\n")
out.close()
print("wrote emu/bases.txt")
