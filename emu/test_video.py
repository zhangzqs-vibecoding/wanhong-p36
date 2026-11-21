from io import BytesIO
import struct
from types import SimpleNamespace
import unittest
from unittest.mock import Mock

from PIL import Image
from unicorn import UC_ARCH_ARM, UC_MODE_ARM, Uc
from unicorn.arm_const import UC_ARM_REG_R0

from lcd import LCD_BASE, Lcd, encode_png
from machine import MOVIE_STARTUP_LIMIT, MOVIE_STARTUP_MS, Machine
from sd import SDRAM_BASE
from video import MOVIE_PRESENT, RGB_CONVERT_CONTROL, MjpegDecoder, convert_rgb24
from web import Handler


class VideoTest(unittest.TestCase):
    def test_rgb_scaler_writes_only_the_positioned_rectangle(self):
        uc = Uc(UC_ARCH_ARM, UC_MODE_ARM)
        uc.mem_map(SDRAM_BASE, 0x1000)
        uc.mem_map(RGB_CONVERT_CONTROL & ~0xFFF, 0x1000)
        source, destination = SDRAM_BASE, SDRAM_BASE + 0x100
        uc.mem_write(source, bytes((255, 0, 0, 0, 0, 255)))
        uc.mem_write(destination, b"\xff" * 30)
        uc.mem_write(RGB_CONVERT_CONTROL + 8, struct.pack(
            "<9I", source, 0, 0, (1 << 16) | 2, 0, destination, (2 << 16) | 2, 6, (1 << 16) | 1,
        ))

        self.assertTrue(convert_rgb24(uc))

        untouched = b"\xff\xff"
        row = untouched + struct.pack("<2H", 0xF800, 0x001F) + untouched * 2
        self.assertEqual(uc.mem_read(destination, 30), untouched * 5 + row * 2)

    def test_mjpeg_reaches_lcd_as_scaled_color(self):
        uc = Uc(UC_ARCH_ARM, UC_MODE_ARM)
        uc.mem_map(SDRAM_BASE, 0x1000)
        data = BytesIO()
        Image.new("RGB", (2, 2), (220, 30, 10)).save(data, format="JPEG")
        planes, width_out, height_out = (SDRAM_BASE + offset for offset in (0x100, 0x200, 0x204))
        decoder = MjpegDecoder(uc, False)
        self.assertTrue(decoder.decode_frame(uc, data.getvalue(), planes, width_out, height_out))
        self.assertEqual(struct.unpack("<II", uc.mem_read(width_out, 8)), (2, 2))

        lcd = Lcd()
        for register, value in (
            (0, 4), (0x14, 0), (0x5C, planes), (0x60, planes + 4), (0x64, planes + 5),
            (0x68, 4 << 10 | 2), (0x6C, 4 << 10 | 2), (0xAC, 4 << 10 | 4), (0xC8, 0x800),
        ):
            lcd.write_register(LCD_BASE + register, value)
        width, height, output = lcd.read_frame(uc)
        self.assertEqual((width, height, len(output)), (4, 4, 32))
        colors = set(struct.unpack("<16H", output))
        self.assertEqual(len(colors), 1)
        color = colors.pop()
        self.assertGreater(color >> 11, 24)
        self.assertLess(color & 31, 4)

    def test_movie_timestamp_uses_frame_start_after_skipped_frames(self):
        uc = Uc(UC_ARCH_ARM, UC_MODE_ARM)
        uc.mem_map(SDRAM_BASE, 0x1000)
        decoder = MjpegDecoder(uc, False)
        decoder.previous = SDRAM_BASE + 0x100, 2, 2
        uc.reg_write(UC_ARM_REG_R0, SDRAM_BASE)
        for pts, index, expected in ((0, 1, 0), (133, 3, 67), (333, 6, 267), (333, 6, 267)):
            uc.mem_write(SDRAM_BASE + 0x2C, struct.pack("<4I", pts, 0, index, 0))
            decoder.present(uc, MOVIE_PRESENT, 4, None)
            self.assertEqual(decoder.presentation_ms, expected)

        decoder.reset(uc, 0, 4, None)
        self.assertIsNone(decoder.presentation_ms)

    def test_movie_startup_capture_keeps_unique_timestamps_with_a_fixed_limit(self):
        machine = Machine.__new__(Machine)
        machine.movie_startup_frames = ()
        machine.audio = SimpleNamespace(mp3_stream=(4, "playing"), mp3_movie_track=4)
        machine.mjpeg = SimpleNamespace(presentation_ms=0)
        picture = (1, 1, b"\x00\xf8")
        machine.lcd = SimpleNamespace(frames=0, video_enabled=True, read_frame=Mock(return_value=picture))

        positions = (0, 0, MOVIE_STARTUP_MS, *range(1, MOVIE_STARTUP_LIMIT + 5))
        for sequence, time in enumerate(positions, 1):
            machine.lcd.frames = sequence
            machine.mjpeg.presentation_ms = time
            machine.capture_movie_startup(None)

        self.assertEqual(machine.movie_startup_frames[0], (1, picture, (4, 0)))
        self.assertEqual([movie[1] for _, _, movie in machine.movie_startup_frames],
                         list(range(MOVIE_STARTUP_LIMIT)))
        self.assertEqual(machine.lcd.read_frame.call_count, MOVIE_STARTUP_LIMIT)

        machine.audio.mp3_stream = (5, "playing")
        machine.audio.mp3_movie_track = 5
        machine.mjpeg.presentation_ms = 0
        machine.capture_movie_startup(None)
        self.assertEqual(machine.movie_startup_frames, ((len(positions), picture, (5, 0)),))
        machine.lcd.video_enabled = False
        machine.capture_movie_startup(None)
        self.assertEqual(machine.movie_startup_frames, ())

    def test_frame_request_replays_opening_scanouts_in_order_for_the_current_movie(self):
        audio = SimpleNamespace(amr=SimpleNamespace(recording=False))
        first = (1, 1, b"\x00\xf8")
        second = (1, 1, b"\x1f\x00")
        startup = ((50, first, (4, 0)), (51, second, (4, 67)), (53, second, (4, 266)))
        screen = SimpleNamespace(
            error=None,
            machine=SimpleNamespace(audio=audio, movie_startup_frames=startup),
            frame=(52, b"latest", True, None, (4, 200)),
        )
        handler = Handler.__new__(Handler)
        handler.server = SimpleNamespace(screen=screen)
        handler.send_body = Mock()

        for after, expected, position in (
            (0, (200, "image/png", b"latest", 52), (4, 200)),
            (49, (200, "image/png", encode_png(*first, fast=True), 50), (4, 0)),
            (50, (200, "image/png", encode_png(*second, fast=True), 51), (4, 67)),
            (51, (200, "image/png", b"latest", 52), (4, 200)),
            (52, (204, "image/png", b""), None),
        ):
            with self.subTest(after=after):
                handler.path = f"/frame?after={after}"
                handler.do_GET()
                self.assertEqual(handler.send_body.call_args.args, expected)
                self.assertEqual(handler.send_body.call_args.kwargs.get("movie"), position)

        screen.frame = (57, b"next movie", True, None, (5, 133))
        handler.path = "/frame?after=49"
        handler.do_GET()
        self.assertEqual(handler.send_body.call_args.args, (200, "image/png", b"next movie", 57))


if __name__ == "__main__":
    unittest.main()
