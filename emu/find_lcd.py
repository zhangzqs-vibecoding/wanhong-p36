#!/usr/bin/env python3
"""Locate LCD probe code: find strings, then literal refs, then disassemble."""
import struct
from capstone import *

FW = "/home/zzq/code/wanhong/sdcard/sword_elfd.bin"
BASE = 0x30000000
data = open(FW, "rb").read()
md = Cs(CS_ARCH_ARM, CS_MODE_ARM | CS_MODE_LITTLE_ENDIAN)
md.skipdata = True

STRINGS = [
    b"lcd probe id = 0x%x, and device id = 0x%x",
    b"Fwl_GetLcdWidth lcd invalid",
    b"brightness error, lcd is not turnon",
    b"set point out of LCD, error!",
]

out = open("/home/zzq/code/wanhong/emu/lcd_find.txt", "w")

def dis_range(a, n):
    s = ""
    for ins in md.disasm(data[a-BASE:a-BASE+n], a):
        s += f"    0x{ins.address:08x}: {ins.mnemonic:8} {ins.op_str}\n"
    return s

for s in STRINGS:
    off = data.find(s)
    if off < 0:
        out.write(f"\n### {s!r}: NOT FOUND\n")
        continue
    va = BASE + off
    out.write(f"\n### {s!r}\n  off 0x{off:x}  VA 0x{va:08x}\n")
    pat = struct.pack('<I', va)
    hits = [i for i in range(0, len(data) - 4, 4) if data[i:i+4] == pat]
    out.write(f"  literal refs: {len(hits)}\n")
    for h in hits[:5]:
        a = BASE + h
        out.write(f"  -- ref to string @0x{a:08x}, code before:\n")
        out.write(dis_range(max(0, a - 0x48), 0x48))
out.close()
print("ok")
