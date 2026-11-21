"""Decode and convert pixels for the firmware's display drivers."""

from io import BytesIO
import struct

from unicorn import UC_HOOK_CODE
from unicorn.arm_const import (
    UC_ARM_REG_LR, UC_ARM_REG_PC, UC_ARM_REG_R0, UC_ARM_REG_R1,
    UC_ARM_REG_R2, UC_ARM_REG_R3, UC_ARM_REG_SP,
)

from sd import SDRAM_BASE, SDRAM_SIZE


MJPEG_TO_YUV420 = 0x3011F92C
MOVIE_PRESENT = 0x300B14E8
RGB_CONVERT_CONTROL = 0x20022100
RGB24_TO_RGB565 = 0x21


def in_sdram(address, length):
    return length > 0 and SDRAM_BASE <= address <= SDRAM_BASE + SDRAM_SIZE - length


class MjpegDecoder:
    def __init__(self, uc, trace):
        self.trace = trace
        self.frames = 0
        self.previous = None
        self.presentation_ms = None
        self.previous_presentation = None
        uc.hook_add(UC_HOOK_CODE, self.decode, begin=MJPEG_TO_YUV420, end=MJPEG_TO_YUV420)
        uc.hook_add(UC_HOOK_CODE, self.reset, begin=0x3007A4D0, end=0x3007A4D0)
        uc.hook_add(UC_HOOK_CODE, self.present, begin=MOVIE_PRESENT, end=MOVIE_PRESENT)

    def reset(self, uc, address, size, data):
        self.previous = None
        self.presentation_ms = None
        self.previous_presentation = None

    def present(self, uc, address, size, data):
        player = uc.reg_read(UC_ARM_REG_R0)
        if self.previous and in_sdram(player, 0x3C):
            pts, _, index, start_pts = struct.unpack("<4I", uc.mem_read(player + 0x2C, 16))
            current = pts, index, start_pts
            previous = self.previous_presentation
            if previous == current:
                return
            duration = 0
            if previous and start_pts == previous[2] and index > previous[1] and pts > previous[0]:
                # The demuxer advances the frame index and PTS before scanout.
                duration = (pts - previous[0]) // (index - previous[1])
            self.presentation_ms = max(0, pts - start_pts - duration)
            self.previous_presentation = current

    def decode(self, uc, address, size, data):
        source = uc.reg_read(UC_ARM_REG_R0)
        length = uc.reg_read(UC_ARM_REG_R1)
        destination = uc.reg_read(UC_ARM_REG_R2)
        width_out = uc.reg_read(UC_ARM_REG_R3)
        height_out, = struct.unpack("<I", uc.mem_read(uc.reg_read(UC_ARM_REG_SP), 4))
        success = False
        if in_sdram(width_out, 4) and in_sdram(height_out, 4):
            if length == 0 and self.previous and self.previous[0] == destination:
                # AVI can hold the previous image for an empty video packet.
                self.write_size(uc, width_out, height_out, *self.previous[1:])
                success = True
            elif in_sdram(source, length):
                jpeg = uc.mem_read(source, length)
                success = self.decode_frame(uc, jpeg, destination, width_out, height_out)
        if self.trace and not success:
            print(f"mjpeg rejected packet source=0x{source:08x} bytes={length} "
                  f"output=0x{destination:08x}")
        uc.reg_write(UC_ARM_REG_R0, int(success))
        uc.reg_write(UC_ARM_REG_PC, uc.reg_read(UC_ARM_REG_LR))

    def decode_frame(self, uc, jpeg, destination, width_out, height_out):
        from PIL import Image

        if not jpeg.startswith(b"\xff\xd8"):
            return False
        try:
            with Image.open(BytesIO(jpeg), formats=["JPEG"]) as image:
                width, height = image.size
                if (width % 2 or height % 2 or width > 1022 or height > 1022
                        or not in_sdram(destination, width * height * 3 // 2)):
                    return False
                image.draft("YCbCr", image.size)
                luminance, blue, red = image.convert("YCbCr").split()
                chroma_size = width // 2, height // 2
                planes = (luminance, blue.resize(chroma_size, Image.Resampling.BOX),
                          red.resize(chroma_size, Image.Resampling.BOX))
                uc.mem_write(destination, b"".join(plane.tobytes() for plane in planes))
        except OSError as error:
            if self.trace:
                print(f"mjpeg decode failed: {error}")
            return False
        self.write_size(uc, width_out, height_out, width, height)
        self.previous = destination, width, height
        self.frames += 1
        if self.trace and (self.frames == 1 or self.frames % 60 == 0):
            print(f"mjpeg frame={self.frames} size={width}x{height} yuv=0x{destination:08x}")
        return True

    @staticmethod
    def write_size(uc, width_out, height_out, width, height):
        uc.mem_write(width_out, struct.pack("<I", width))
        uc.mem_write(height_out, struct.pack("<I", height))


def scale_yuv420(planes, width, height, output_width, output_height):
    from PIL import Image

    size = width, height
    chroma = width // 2, height // 2
    luminance = Image.frombytes("L", size, planes[0])
    blue = Image.frombytes("L", chroma, planes[1]).resize(size, Image.Resampling.BILINEAR)
    red = Image.frombytes("L", chroma, planes[2]).resize(size, Image.Resampling.BILINEAR)
    picture = Image.merge("YCbCr", (luminance, blue, red)).convert("RGB")
    picture = picture.resize((output_width, output_height), Image.Resampling.BILINEAR)
    return rgb565_bytes(picture)


def rgb565_bytes(picture):
    import numpy as np

    colors = np.asarray(picture, dtype="<u2")
    rgb565 = (colors[..., 0] & 0xF8) << 8 | (colors[..., 1] & 0xFC) << 3 | colors[..., 2] >> 3
    return rgb565.tobytes()


def convert_rgb24(uc):
    """Perform the RGB conversion requested by the NES display driver."""
    from PIL import Image

    (source, _, _, source_size, _, destination, output_size, line_padding,
     position) = struct.unpack("<9I", uc.mem_read(RGB_CONVERT_CONTROL + 8, 36))
    width, height = source_size & 0xFFFF, source_size >> 16
    output_width, output_height = output_size & 0xFFFF, output_size >> 16
    x, y = position & 0xFFFF, position >> 16
    source_bytes = width * height * 3
    output_row = output_width * 2
    stride = output_row + line_padding
    first_pixel = destination + y * stride + x * 2
    output_bytes = (output_height - 1) * stride + output_row
    if (not all(0 < size <= 2048 for size in (width, height, output_width, output_height))
            or x * 2 > line_padding or not in_sdram(source, source_bytes)
            or not in_sdram(first_pixel, output_bytes)):
        return False
    pixels = Image.frombytes("RGB", (width, height), bytes(uc.mem_read(source, source_bytes)))
    if pixels.size != (output_width, output_height):
        pixels = pixels.resize((output_width, output_height), Image.Resampling.BILINEAR)
    rgb565 = rgb565_bytes(pixels)
    for row in range(output_height):
        offset = row * output_row
        uc.mem_write(first_pixel + row * stride, rgb565[offset:offset + output_row])
    return True
