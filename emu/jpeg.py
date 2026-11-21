"""Decode JPEGs for the firmware's 24-bit bitmap accelerator entry point."""

import struct
import subprocess

from unicorn import UC_HOOK_CODE
from unicorn.arm_const import (
    UC_ARM_REG_LR, UC_ARM_REG_PC, UC_ARM_REG_R0, UC_ARM_REG_R1,
    UC_ARM_REG_R2, UC_ARM_REG_R3,
)


JPEG_TO_BMP24 = 0x301BABD4


class JpegDecoder:
    def __init__(self, uc, ram_base, ram_size, trace):
        self.ram_base = ram_base
        self.ram_end = ram_base + ram_size
        self.trace = trace
        uc.hook_add(UC_HOOK_CODE, self.decode, begin=JPEG_TO_BMP24, end=JPEG_TO_BMP24)

    def in_ram(self, address, size):
        return size > 0 and self.ram_base <= address < address + size <= self.ram_end

    def decode(self, uc, address, size, data):
        descriptor = uc.reg_read(UC_ARM_REG_R0)
        dest = uc.reg_read(UC_ARM_REG_R1)
        width = uc.reg_read(UC_ARM_REG_R2)
        height = uc.reg_read(UC_ARM_REG_R3)
        pixels = b""
        if self.in_ram(descriptor, 28) and 0 < width <= 4096 and 0 < height <= 4096:
            source, _, length, _, _, original_width, original_height = struct.unpack(
                "<7I", uc.mem_read(descriptor, 28)
            )
            stride = (width * 3 + 3) & ~3
            if self.in_ram(source, length) and self.in_ram(dest, stride * height):
                jpeg = bytes(uc.mem_read(source, length))
                if jpeg.startswith(b"\xff\xd8"):
                    pixels = self.decode_bitmap(jpeg, width, height, stride)
                    if pixels:
                        uc.mem_write(dest, pixels)
                        uc.mem_write(descriptor + 0x14, struct.pack("<II", width, height))
                    if self.trace:
                        print(f"jpeg {original_width}x{original_height} -> {width}x{height} "
                              f"input={length} output={len(pixels)}")
        uc.reg_write(UC_ARM_REG_R0, 0 if pixels else 0xFFFFFFFE)
        uc.reg_write(UC_ARM_REG_PC, uc.reg_read(UC_ARM_REG_LR))

    @staticmethod
    def decode_bitmap(data, width, height, stride):
        try:
            result = subprocess.run(
                ["ffmpeg", "-v", "error", "-threads", "1", "-filter_threads", "1",
                 "-f", "mjpeg", "-i", "pipe:0", "-frames:v", "1",
                 "-vf", f"scale={width}:{height}", "-pix_fmt", "bgr24",
                 "-c:v", "bmp", "-f", "image2pipe", "pipe:1"],
                input=data, capture_output=True, timeout=10, check=False,
            )
        except FileNotFoundError as error:
            raise RuntimeError("ffmpeg is required for P36 JPEG decoding") from error
        except subprocess.TimeoutExpired:
            return b""
        bitmap = result.stdout
        if result.returncode or len(bitmap) < 54 or bitmap[:2] != b"BM":
            return b""
        offset = struct.unpack_from("<I", bitmap, 10)[0]
        pixels = bitmap[offset:]
        return pixels if len(pixels) == stride * height else b""
