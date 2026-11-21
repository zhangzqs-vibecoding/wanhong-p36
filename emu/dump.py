#!/usr/bin/env python3
"""Dump disassembly of selected regions to emu/dump.txt"""
from capstone import *

FW = "/home/zzq/code/wanhong/sdcard/sword_elfd.bin"
BASE = 0x30000000
data = open(FW, "rb").read()
md = Cs(CS_ARCH_ARM, CS_MODE_ARM | CS_MODE_LITTLE_ENDIAN)
md.skipdata = True

regions = [
    (0x300261b0, 0x60,  "delay fn"),
    (0x300664dc, 0x120, "timer drv 0x2002d000"),
    (0x300673a0, 0x90,  "LCD layer setup"),
    (0x300916dc, 0x60,  "LCD ctrl 0x2002c000"),
    (0x30067040, 0x90,  "drv 0x20026000"),
    (0x30022e80, 0xc0,  "drv 0x20010000"),
]

out = open("/home/zzq/code/wanhong/emu/dump.txt", "w")
for a, n, label in regions:
    out.write(f"\n================ {label} @0x{a:08x} ================\n")
    for ins in md.disasm(data[a-BASE:a-BASE+n], a):
        out.write(f"  0x{ins.address:08x}: {ins.mnemonic:8} {ins.op_str}\n")
out.close()
print("ok")
