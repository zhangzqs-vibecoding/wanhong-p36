#!/usr/bin/env python3
"""Find framebuffer base/stride by maximising horizontal edge sharpness in the text area."""
import struct, zlib

mem = open("/home/zzq/code/wanhong/emu/sdram_full.bin", "rb").read()
BASE = 0x30000000

def sharpness(off, w, h):
    n = w * h
    if off < 0 or off + n * 2 > len(mem):
        return -1.0
    px = struct.unpack_from('<%dH' % n, mem, off)
    total = 0; cnt = 0
    for y in range(0, h, 2):
        row = y * w
        for x in range(1, w, 2):
            a = px[row + x]; b = px[row + x - 1]
            if a or b:
                total += abs(a - b); cnt += 1
    return (total / cnt) if cnt else 0.0

res = []
for base in (0x30280000, 0x30280002, 0x30280004, 0x30280008, 0x30280800, 0x30280000 - 0x800):
    for w in (800, 480, 400, 320, 240):
        for h in (480, 272, 240):
            s = sharpness(base - BASE, w, h)
            if s > 0:
                res.append((s, base, w, h))
res.sort(reverse=True)
print("top by edge sharpness:")
for s, base, w, h in res[:12]:
    print(f"  {s:8.1f}  base=0x{base:08x} {w}x{h}")

def png(path, w, h, rgb):
    raw = b''.join(b'\x00' + b''.join(struct.pack('BBB', *p) for p in rgb[y*w:(y+1)*w]) for y in range(h))
    def ch(t, d):
        return struct.pack('>I', len(d)) + t + d + struct.pack('>I', zlib.crc32(t + d) & 0xFFFFFFFF)
    open(path, 'wb').write(b'\x89PNG\r\n\x1a\n' +
                           ch(b'IHDR', struct.pack('>IIBBBBB', w, h, 8, 2, 0, 0, 0)) +
                           ch(b'IDAT', zlib.compress(raw, 6)) + ch(b'IEND', b''))

for i, (s, base, w, h) in enumerate(res[:4]):
    px = struct.unpack_from('<%dH' % (w * h), mem, base - BASE)
    rgb = []
    for v in px:
        r = ((v >> 11) & 0x1f) * 255 // 31
        g = ((v >> 5) & 0x3f) * 255 // 63
        b = (v & 0x1f) * 255 // 31
        # boost contrast
        rgb.append((min(255, r * 3), min(255, g * 3), min(255, b * 3)))
    png(f"/home/zzq/code/wanhong/emu/sharp{i}_{base:08x}_{w}x{h}.png", w, h, rgb)
    print(f"rendered sharp{i}_0x{base:08x}_{w}x{h}.png (score {s:.1f})")
