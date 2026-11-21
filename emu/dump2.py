#!/usr/bin/env python3
from capstone import *
FW = "/home/zzq/code/wanhong/sdcard/sword_elfd.bin"; BASE = 0x30000000
data = open(FW, "rb").read()
md = Cs(CS_ARCH_ARM, CS_MODE_ARM | CS_MODE_LITTLE_ENDIAN); md.skipdata = True
out = open("/home/zzq/code/wanhong/emu/dump2.txt", "w")
for a, n, lab in [(0x30020530, 0x80, "calibration 0x30020564"),
                  (0x30091f9c, 0x80, "lcd helper 0x30091f9c")]:
    out.write(f"\n==== {lab} ====\n")
    for ins in md.disasm(data[a-BASE:a-BASE+n], a):
        out.write(f"  0x{ins.address:08x}: {ins.mnemonic:8} {ins.op_str}\n")
out.close(); print("ok")
