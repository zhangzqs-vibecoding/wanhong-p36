#!/usr/bin/env python3
"""Find the real framebuffer geometry and print an ASCII density map of its content."""
import struct, collections, sys

mem = open("/home/zzq/code/wanhong/emu/sdram_full.bin", "rb").read()
BASE = 0x30000000

def analyze(start, w, h, tag):
    off = start - BASE
    n = w * h
    if off < 0 or off + n * 2 > len(mem):
        print(f"{tag}: out of range"); return
    px = struct.unpack_from('<%dH' % n, mem, off)
    hist = collections.Counter(px)
    bg, bgn = hist.most_common(1)[0]
    print(f"\n### {tag}  base=0x{start:08x} {w}x{h}  bg=0x{bg:04x} ({bgn/n:.0%})  distinct={len(hist)}")
    print("    top colors:", [(hex(c), round(k / n, 3)) for c, k in hist.most_common(5)])
    # ascii density map
    CW, CH = 100, 50
    out = []
    for cy in range(CH):
        row = ""
        for cx in range(CW):
            x0 = cx * w // CW; x1 = max(x0 + 1, (cx + 1) * w // CW)
            y0 = cy * h // CH; y1 = max(y0 + 1, (cy + 1) * h // CH)
            cnt = 0; tot = 0
            for y in range(y0, y1):
                base_i = y * w
                for x in range(x0, x1):
                    tot += 1
                    if px[base_i + x] != bg:
                        cnt += 1
            r = cnt / tot if tot else 0
            row += "." if r == 0 else ("-" if r < .08 else ("+" if r < .3 else "#"))
        out.append(row)
    print("\n".join("    " + r for r in out))

cands = [
    (0x30280000, 800, 480, "blk A"),
    (0x30280000, 480, 272, "blk B"),
    (0x30260000, 800, 480, "blk C"),
    (0x30240000, 800, 480, "blk D"),
]
for s, w, h, t in cands:
    analyze(s, w, h, t)
