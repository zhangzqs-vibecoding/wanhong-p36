#!/usr/bin/env python3
"""Boot the P36 firmware and optionally save a snapshot and LCD screenshot."""

import argparse
from pathlib import Path

from unicorn import UcError
from unicorn.arm_const import UC_ARM_REG_LR, UC_ARM_REG_SP

from lcd import encode_png
from machine import Machine, SDRAM_BASE, SDRAM_SIZE


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("instructions", type=int, nargs="?", default=400_000_000)
    parser.add_argument("--sd-image", type=argparse.FileType("rb"))
    parser.add_argument("--trace", action="store_true")
    parser.add_argument("--snapshot", type=Path)
    parser.add_argument("--frame", type=Path, help="save the LCD scanout as a PNG")
    args = parser.parse_args()
    if args.instructions <= 0:
        parser.error("instructions must be positive")

    machine = Machine(sd_image=args.sd_image, trace=args.trace)
    uc = machine.uc
    done = 0
    status = 0
    chunk = 10_000_000
    while done < args.instructions:
        count = min(chunk, args.instructions - done)
        try:
            machine.run(count)
        except (UcError, RuntimeError) as error:
            print(f"ERR {error} pc=0x{machine.pc:08x}")
            status = 1
            break
        done += count
        forced = ", ".join(hex(address) for address in machine.forced)
        print(f"  {done // 1_000_000}M pc=0x{machine.pc:08x} "
              f"sp=0x{uc.reg_read(UC_ARM_REG_SP):08x} forced={{{forced}}}")

    print(f"\nfinal pc=0x{machine.pc:08x} lr=0x{uc.reg_read(UC_ARM_REG_LR):08x}")
    print("forced regs:", {hex(key): hex(value) for key, value in machine.forced.items()})
    print("IRQs delivered:", machine.irqs)
    if args.snapshot:
        args.snapshot.write_bytes(uc.mem_read(SDRAM_BASE, SDRAM_SIZE))
    else:
        snapshot = Path(__file__).with_name("sdram.bin")
        snapshot.write_bytes(uc.mem_read(SDRAM_BASE, 0x200000))
    if machine.lcd.scanout:
        address, width, height = machine.lcd.scanout
        print(f"LCD {width}x{height} RGB565 addr=0x{address:08x} refreshes={machine.lcd.frames}")
    if args.frame:
        frame = machine.lcd.read_frame(uc)
        if frame is None:
            parser.exit(1, "No LCD scanout was programmed.\n")
        args.frame.write_bytes(encode_png(*frame))
    print("top readonly:", [(hex(address), count) for address, count in machine.reads.most_common(8)])
    return status


if __name__ == "__main__":
    raise SystemExit(main())
