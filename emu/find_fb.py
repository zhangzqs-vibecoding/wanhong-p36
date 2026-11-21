#!/usr/bin/env python3
"""Search for the true framebuffer base/stride/pixel-format by maximising bimodality."""
import struct, collections, zlib

mem = open("/home/zzq/code/wanhong/emu/sdram_full.bin", "rb").read()
BASE = 0x30000000

def stats(off, w, h):
    n = w * h
    if off < 0 or off + n * 2 > len(mem):
        return None
    px = struct.unpack_from('<%dH' % n, mem, off)
    return px, collections.Counter(px)

best = []
for base in range(0x30240000, 0x302A0000, 0x2000):
    for w in (800, 480, 400, 320, 240):
        for h in (480, 272, 240):
            r = stats(base - BASE, w, h)
            if not r:
                continue
            px, hist = r
            n = w * h
            top2 = sum(k for _, k in hist.most_common(2)) / n
            best.append((top2, base, w, h, hist))
best.sort(reverse=True, key=lambda x: x[0])
print("top candidates by bimodality (crisp text -> ~1.0):")
for s, base, w, h, hist in best[:10]:
    print(f"  score={s:.3f} base=0x{base:08x} {w}x{h} top=" +
          str([(hex(c), round(k/(w*h), 3)) for c, k in hist.most_common(4)]))

def png(path, w, h, rgb):
    raw = b''.join(b'\x00' + b''.join(struct.pack('BBB', *p) for p in rgb[y*w:(y+1)*w]) for y in range(h))
    def ch(t, d):
        return struct.pack('>I', len(d)) + t + d + struct.pack('>I', zlib.crc32(t + d) & 0xFFFFFFFF)
    open(path, 'wb').write(b'\x89PNG\r\n\x1a\n' +
                           ch(b'IHDR', struct.pack('>IIBBBBB', w, h, 8, 2, 0, 0, 0)) +
                           ch(b'IDAT', zlib.compress(raw, 6)) + ch(b'IEND', b''))

for i, (s, base, w, h, hist) in enumerate(best[:4]):
    px = struct.unpack_from('<%dH' % (w * h), mem, base - BASE)
    low = hist.most_common(1)[0][0]
    rgb = []
    for v in px:
        if v == low:
            rgb.append((0, 0, 0))
        else:
            r = ((v >> 11) & 0x1f) * 255 // 31
            g = ((v >> 5) & 0x3f) * 255 // 63
            b = (v & 0x1f) * 255 // 31
            rgb.append((r, g, b))
    png(f"/home/zzq/code/wanhong/emu/best{i}_{base:08x}_{w}x{h}.png", w, h, rgb)
    print(f"rendered best{i}_ 0x{base:08x} {w}x{h} score={s:.3f}")
