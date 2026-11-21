#!/usr/bin/env python3
"""Build the original NROM cartridge used to check P36 video, controls, and sound."""

import argparse
from pathlib import Path
import struct

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument("destination", type=Path)
parser.add_argument("--controller", type=int, choices=(1, 2), default=1)
args = parser.parse_args()

# Original NROM cartridge. Any controller button changes the stripes and gates a pulse tone.
program = bytearray()
labels = {}
branches = []

def emit(*code):
    program.extend(code)

def label(name):
    labels[name] = len(program)

def branch(opcode, name):
    branches.append((len(program) + 1, name))
    emit(opcode, 0)

def write(address, value):
    emit(0xa9, value, 0x8d, address & 255, address >> 8)

emit(0x78, 0xd8, 0xa2, 0xff, 0x9a, 0xe8)  # SEI; CLD; LDX #$FF; TXS; INX
for register in (0x2000, 0x2001, 0x4010, 0x4015):
    write(register, 0)
for loop in ('first_vblank', 'second_vblank'):
    label(loop)
    emit(0x2c, 0x02, 0x20)  # BIT PPUSTATUS
    branch(0x10, loop)      # BPL
write(0x2006, 0x20)
write(0x2006, 0)
emit(0xa2, 4, 0xa0, 0)
label('clear')
emit(0x8d, 0x07, 0x20, 0xc8)
branch(0xd0, 'clear')
emit(0xca)
branch(0xd0, 'clear')
write(0x4000, 0x3a)
write(0x4001, 0x08)
write(0x4002, 0xfd)
label('vblank')
emit(0x2c, 0x02, 0x20)
branch(0x10, 'vblank')
write(0x4016, 1)
write(0x4016, 0)
emit(0x85, 0x00, 0xa2, 8)  # clear button latch
label('controller')
emit(0xad, 0x15 + args.controller, 0x40, 0x4a, 0x26, 0x00, 0xca)
branch(0xd0, 'controller')
write(0x2006, 0x3f)
write(0x2006, 0)
write(0x2007, 0x0f)
emit(0xa5, 0x00)
branch(0xf0, 'released')
write(0x2007, 0x2a)
emit(0xa5, 0x01)  # Leave the pulse phase alone while the button stays down.
branch(0xd0, 'display')
emit(0xa9, 1, 0x85, 0x01)
write(0x4015, 1)
write(0x4003, 0x08)
branch(0xd0, 'display')
label('released')
write(0x2007, 0x21)
write(0x4015, 0)
emit(0x85, 0x01)
label('display')
write(0x2005, 0)
write(0x2005, 0)
write(0x2001, 0x0a)
address = 0xc000 + labels['vblank']
emit(0x4c, address & 255, address >> 8)
for origin, name in branches:
    distance = labels[name] - origin - 1
    assert -128 <= distance < 128
    program[origin] = distance & 255
header = b'NES\x1a\x01\x01' + bytes(10)
prg = program.ljust(0x3ffa, b'\xea') + struct.pack('<3H', 0xc000, 0xc000, 0xc000)
chr_rom = (bytes([0xf0]) * 8 + bytes(8)) * 512
with args.destination.open("xb") as cartridge:
    cartridge.write(header + prg + chr_rom)
