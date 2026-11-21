"""Capture the P36 LCD controller's RGB565 scanout."""

from io import BytesIO
import struct
import zlib

from sd import SDRAM_BASE, SDRAM_SIZE
from video import in_sdram, scale_yuv420


LCD_BASE = 0x20010000
YUV_ADDRESSES = (0x5C, 0x60, 0x64)
YUV_WIDTH = 0x68
YUV_HEIGHT = 0x6C
YUV_POSITION = 0x74
YUV_REGISTERS = (*YUV_ADDRESSES, YUV_WIDTH, YUV_HEIGHT, YUV_POSITION)


class Lcd:
    def __init__(self):
        self.address = 0
        self.geometry = 0
        self.scanout = None
        self.frames = 0
        self.control = 0
        self.video_registers = {}
        self.video_scanout = None

    def write_register(self, address, value):
        register = address - LCD_BASE
        if register == 0:
            self.control = value
        elif register in YUV_REGISTERS:
            self.video_registers[register] = value
        elif register == 0x14:
            self.address = SDRAM_BASE + (value & 0x0FFFFFFF)
        elif register == 0xAC:
            self.geometry = value
        elif register == 0xC8 and value & 0x800:
            width = (self.geometry >> 10) & 0x7FF
            height = self.geometry & 0x3FF
            length = width * height * 2
            if length and SDRAM_BASE <= self.address <= SDRAM_BASE + SDRAM_SIZE - length:
                self.scanout = (self.address, width, height)
                self.video_scanout = (self.control, self.video_registers.copy())
                self.frames += 1
                return True
        return False

    @property
    def video_enabled(self):
        return self.video_scanout is not None and bool(self.video_scanout[0] & 0x4)

    def read_frame(self, uc):
        if self.scanout is None:
            return None
        address, width, height = self.scanout
        control, registers = self.video_scanout
        if control & 0x4:
            video = self.read_video(uc, registers, width, height)
            if video:
                return width, height, video
        return width, height, uc.mem_read(address, width * height * 2)

    @staticmethod
    def read_video(uc, registers, width, height):
        horizontal = registers.get(YUV_WIDTH, 0)
        vertical = registers.get(YUV_HEIGHT, 0)
        source_width, output_width = horizontal & 0x3FF, horizontal >> 10 & 0x3FF
        source_height, output_height = vertical & 0x3FF, vertical >> 10 & 0x3FF
        position = registers.get(YUV_POSITION, 0)
        x, y = position >> 10 & 0x3FF, position & 0x3FF
        if (not source_width or not source_height or source_width % 2 or source_height % 2
                or not output_width or not output_height or x + output_width > width
                or y + output_height > height):
            return None
        samples = source_width * source_height
        planes = []
        for register, length in zip(YUV_ADDRESSES, (samples, samples // 4, samples // 4)):
            address = registers.get(register, 0)
            if not in_sdram(address, length):
                return None
            planes.append(bytes(uc.mem_read(address, length)))
        picture = scale_yuv420(planes, source_width, source_height, output_width, output_height)
        if output_width == width and output_height == height:
            return picture
        canvas = bytearray(width * height * 2)
        for row in range(output_height):
            source = row * output_width * 2
            destination = ((y + row) * width + x) * 2
            canvas[destination:destination + output_width * 2] = picture[source:source + output_width * 2]
        return canvas


def encode_png(width, height, rgb565, *, fast=False):
    if fast:
        from PIL import Image

        pixels = Image.frombytes("RGB", (width, height), bytes(rgb565), "raw", "BGR;16")
        with BytesIO() as stream:
            pixels.save(stream, format="PNG", compress_level=1)
            return stream.getvalue()

    def chunk(kind, data):
        return (struct.pack(">I", len(data)) + kind + data
                + struct.pack(">I", zlib.crc32(kind + data)))

    pixels = iter(struct.iter_unpack("<H", rgb565))
    stride = width * 3 + 1
    rows = bytearray(height * stride)
    for row in range(height):
        output = row * stride + 1
        for _ in range(width):
            pixel, = next(pixels)
            rows[output] = (pixel >> 11) * 255 // 31
            rows[output + 1] = ((pixel >> 5) & 63) * 255 // 63
            rows[output + 2] = (pixel & 31) * 255 // 31
            output += 3

    header = struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0)
    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", header)
            + chunk(b"IDAT", zlib.compress(rows)) + chunk(b"IEND", b""))
