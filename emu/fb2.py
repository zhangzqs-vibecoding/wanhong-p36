#!/usr/bin/env python3
"""Run the machine, then render the LCD framebuffer candidate to PNG."""
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
    if a in FORCE:
        uc.mem_write(addr, struct.pack('<I', FORCE[a])); return
    if a in SELF_CLEAR:
        uc.mem_write(addr, b'\x00'*sz)
def wr(uc, acc, addr, sz, val, u):
    if BASE <= addr < BASE+RAM: return
    a = addr & ~3; v = (val or 0) & 0xFFFFFFFF
    if a in SELF_CLEAR: v = 0
    uc.mem_write(a, struct.pack('<I', v))
uc.hook_add(UC_HOOK_MEM_READ, rd); uc.hook_add(UC_HOOK_MEM_WRITE, wr)
uc.hook_add(UC_HOOK_INTR, lambda uc,i,u: None)
uc.hook_add(UC_HOOK_MEM_UNMAPPED, lambda uc,acc,ad,sz,v,u:(uc.mem_map(ad&~0xfff,0x1000) or True))

N = int(sys.argv[1]) if len(sys.argv)>1 else 120_000_000
CH = 10_000_000; done = 0
while done < N:
    try: uc.emu_start(uc.reg_read(UC_ARM_REG_PC), 0, count=min(CH, N-done), timeout=45*1000*1000)
    except UcError as e: print("ERR", e); break
    done += CH
print("ran %dM pc=0x%08x" % (done//1_000_000, uc.reg_read(UC_ARM_REG_PC)))

lcd = uc.mem_read(0x2002c000, 0x100)
print("LCD regs 0x2002c000..0x2002c0fc:")
for off in range(0, 0x100, 4):
    v = struct.unpack_from('<I', lcd, off)[0]
    if v: print("   0x%02x = 0x%08x" % (off, v))
mem = bytes(uc.mem_read(BASE, 0x200000))
print("framebuffer@0x30000000 differs from firmware:", mem[:64] != bytes(fw[:64]))
open("/home/zzq/code/wanhong/emu/sdram.bin","wb").write(mem)

def png(path, w, h, px):
    raw = b''.join(b'\x00' + b''.join(struct.pack('BBB', *p) for p in px[y*w:(y+1)*w]) for y in range(h))
    def ch(t, d):
        return struct.pack('>I', len(d)) + t + d + struct.pack('>I', zlib.crc32(t+d) & 0xFFFFFFFF)
    open(path,'wb').write(b'\x89PNG\r\n\x1a\n' + ch(b'IHDR', struct.pack('>IIBBBBB',w,h,8,2,0,0,0))
                          + ch(b'IDAT', zlib.compress(raw,6)) + ch(b'IEND', b''))

def rgb565(buf, w, h, off=0):
    px=[]
    for i in range(w*h):
        v = struct.unpack_from('<H', buf, off+i*2)[0]
        r = (v>>11)&0x1f; g=(v>>5)&0x3f; b=v&0x1f
        px.append((r*255//31, g*255//63, b*255//31))
    return px

for (w,h) in [(480,272),(800,480),(320,240),(240,320)]:
    need = w*h*2
    if need <= len(mem):
        png(f"/home/zzq/code/wanhong/emu/fb_{w}x{h}.png", w, h, rgb565(mem,w,h))
        print("wrote fb_%dx%d.png" % (w,h))
