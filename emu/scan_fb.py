#!/usr/bin/env python3
"""Run the machine, then scan all 32MB SDRAM for runtime-written / image-like blocks."""
import sys, struct, zlib
from unicorn import *
from unicorn.arm_const import *

FW = "/home/zzq/code/wanhong/sdcard/sword_elfd.bin"
BASE = 0x30000000; RAM = 0x2000000
VECTORS = {0:0x30000020,1:0x302176c0,2:0x30000100,3:0x302176fc,
           4:0x30217738,5:0x30000020,6:0x30217618,7:0x3021766c}
SELF_CLEAR = {0x08000004, 0x0800000C, 0x0800004C}
FORCE = {0x20020034: 0xFFFFFFFF}

fw = bytearray(open(FW, "rb").read())
struct.pack_into('<I', fw, 0x30217848 - BASE, 0xE1A00000)
uc = Uc(UC_ARCH_ARM, UC_MODE_ARM | UC_MODE_LITTLE_ENDIAN)
uc.mem_map(BASE, RAM); uc.mem_write(BASE, bytes(fw))
vec = bytearray(0x40)
for i in range(8):
    struct.pack_into('<I', vec, i*4, 0xE59FF018)
    struct.pack_into('<I', vec, 0x20+i*4, VECTORS[i])
uc.mem_map(0, 0x1000); uc.mem_write(0, bytes(vec))
for pa, psz in [(0x08000000,0x10000),(0x20000000,0x100000),(0x48000000,0x10000)]:
    try: uc.mem_map(pa, psz)
    except UcError: pass
uc.reg_write(UC_ARM_REG_SP, 0x31FF0000)
uc.reg_write(UC_ARM_REG_PC, BASE)
uc.reg_write(UC_ARM_REG_CPSR, 0xD3)

def rd(uc, acc, addr, sz, val, u):
    if BASE <= addr < BASE+RAM: return
    a = addr & ~3
    if a in FORCE: uc.mem_write(addr, struct.pack('<I', FORCE[a])); return
    if a in SELF_CLEAR: uc.mem_write(addr, b'\x00'*sz)
def wr(uc, acc, addr, sz, val, u):
    if BASE <= addr < BASE+RAM: return
    a = addr & ~3; v = (val or 0) & 0xFFFFFFFF
    if a in SELF_CLEAR: v = 0
    uc.mem_write(a, struct.pack('<I', v))
uc.hook_add(UC_HOOK_MEM_READ, rd); uc.hook_add(UC_HOOK_MEM_WRITE, wr)
uc.hook_add(UC_HOOK_INTR, lambda uc,i,u: None)
uc.hook_add(UC_HOOK_MEM_UNMAPPED, lambda uc,acc,ad,sz,v,u:(uc.mem_map(ad&~0xfff,0x1000) or True))

N = int(sys.argv[1]) if len(sys.argv)>1 else 150_000_000
CH = 10_000_000; done = 0
while done < N:
    try: uc.emu_start(uc.reg_read(UC_ARM_REG_PC), 0, count=min(CH, N-done), timeout=45*1000*1000)
    except UcError as e: print("ERR", e, "pc=0x%08x"%uc.reg_read(UC_ARM_REG_PC)); break
    done += CH
print("ran %dM pc=0x%08x" % (done//1_000_000, uc.reg_read(UC_ARM_REG_PC)))

mem = bytes(uc.mem_read(BASE, RAM))
open("/home/zzq/code/wanhong/emu/sdram_full.bin","wb").write(mem)

BLK = 0x40000
print("\nblock  distinct_u16  changed_vs_fw  zero%")
rank = []
for i in range(0, RAM - BLK + 1, BLK):
    blkv = mem[i:i+BLK]
    # sample
    vals = set()
    for k in range(0, BLK - 1, 2*17):
        vals.add(blkv[k] | (blkv[k+1] << 8))
    fwv = bytes(fw[i:i+BLK]) if i < len(fw) else b''
    changed = (blkv != fwv) if fwv else True
    zeros = blkv.count(0) / len(blkv)
    rank.append((len(vals), 0x30000000+i, changed, zeros))
rank.sort(reverse=True)
for d, a, ch, z in rank[:12]:
    print(f"  0x{a:08x}  distinct={d:5d}  changed={ch}  zero={z:.2f}")

def png(path, w, h, px):
    raw = b''.join(b'\x00' + b''.join(struct.pack('BBB', *p) for p in px[y*w:(y+1)*w]) for y in range(h))
    def ch(t, d):
        return struct.pack('>I', len(d)) + t + d + struct.pack('>I', zlib.crc32(t+d) & 0xFFFFFFFF)
    open(path,'wb').write(b'\x89PNG\r\n\x1a\n' + ch(b'IHDR', struct.pack('>IIBBBBB',w,h,8,2,0,0,0))
                          + ch(b'IDAT', zlib.compress(raw,6)) + ch(b'IEND', b''))

def render(addr, w, h, name):
    off = addr - BASE
    if off + w*h*2 > len(mem): return False
    px = []
    for i in range(w*h):
        v = struct.unpack_from('<H', mem, off+i*2)[0]
        px.append((((v>>11)&0x1f)*255//31, ((v>>5)&0x3f)*255//63, (v&0x1f)*255//31))
    png(f"/home/zzq/code/wanhong/emu/{name}.png", w, h, px)
    return True

for d, a, ch, z in rank[:6]:
    for (w,h) in [(480,272),(800,480)]:
        if render(a, w, h, f"blk_{a:08x}_{w}x{h}"):
            print(f"rendered 0x{a:08x} as {w}x{h}")
