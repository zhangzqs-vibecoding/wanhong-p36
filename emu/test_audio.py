import struct
import unittest

from unicorn import UC_ARCH_ARM, UC_MODE_ARM, Uc
from unicorn.arm_const import (
    UC_ARM_REG_LR, UC_ARM_REG_R0, UC_ARM_REG_R1, UC_ARM_REG_R2, UC_ARM_REG_R3, UC_ARM_REG_R4,
)

from audio import AudioDsp, IDMA_DATA, MOVIE_PLAY_BUFFER_RETURN, MP3_PAUSE, MP3_RING, MP3_RING_SIZE


class FileMp3Tests(unittest.TestCase):
    def setUp(self):
        self.uc = Uc(UC_ARCH_ARM, UC_MODE_ARM)
        self.uc.mem_map(0x31D50000, 0x20000)
        self.dsp = AudioDsp(self.uc, 0x30000000, 0x2000000, False)
        self.dsp.start_mp3(self.uc, 0, 0, None)

    def test_ring_wrap_and_natural_finish(self):
        self.dsp.publish("audio/mpeg", b"boot")
        header = b"\xff\xfb\x92\x60"
        self.uc.mem_write(0x31D50000, header)
        self.uc.mem_write(0x31D50004, struct.pack("<I", MP3_RING_SIZE - 2))
        self.uc.mem_write(MP3_RING + MP3_RING_SIZE - 2, b"ab")
        self.uc.mem_write(MP3_RING, b"cdef")
        self.uc.reg_write(UC_ARM_REG_R2, 0x31D50000)
        self.uc.reg_write(UC_ARM_REG_R3, 10)
        self.uc.reg_write(UC_ARM_REG_R4, 0x31D50004)
        self.dsp.capture_mp3_frame(self.uc, 0, 0, None)

        self.uc.reg_write(UC_ARM_REG_R0, 0)
        self.dsp.finish_mp3(self.uc, 0, 0, None)

        self.assertEqual(self.dsp.read_clip(0), ((1, "ended"), None))
        self.assertEqual(self.dsp.read_clip(1), ((1, "ended"), (2, "audio/mpeg", header + b"abcdef", 1, None)))

    def test_pause_halts_requests_and_seek_discards_old_chunks(self):
        self.dsp.publish("audio/mpeg", b"before seek", 1)
        self.dsp.selection = MP3_PAUSE
        self.dsp.request()
        self.dsp.write_register(self.uc, IDMA_DATA, 1)
        self.dsp.advance(self.dsp.period)

        self.assertFalse(self.dsp.pending)
        self.assertEqual(self.dsp.read_clip(0)[0], (1, "paused"))

        self.dsp.seek_mp3(self.uc, 0, 0, None)
        self.assertEqual(self.dsp.read_clip(0), ((2, "playing"), None))

    def test_movie_stream_keeps_prefill_and_ignores_previous_header(self):
        buffer, header_address, offset_address = (0x31D50000 + offset for offset in (0, 32, 36))
        header = b"\xff\xf3\x60\xc4"
        first_packet = header + b"first"
        packet = header + b"second"
        prefill = first_packet + packet
        self.dsp.publish("audio/mpeg", b"boot")
        self.uc.mem_write(buffer, prefill)
        self.uc.reg_write(UC_ARM_REG_R0, buffer)
        self.uc.reg_write(UC_ARM_REG_R1, len(prefill))
        self.uc.reg_write(UC_ARM_REG_LR, MOVIE_PLAY_BUFFER_RETURN)
        self.dsp.play_buffer(self.uc, 0, 0, None)
        self.dsp.start_mp3(self.uc, 0, 0, None)

        self.uc.mem_write(MP3_RING, b"second\0\0\0\0")
        self.uc.reg_write(UC_ARM_REG_R2, header_address)
        self.uc.reg_write(UC_ARM_REG_R3, len(packet))
        self.uc.reg_write(UC_ARM_REG_R4, offset_address)
        self.uc.mem_write(header_address, b"\xff\xfb\x92\x60")
        self.dsp.capture_mp3_frame(self.uc, 0, 0, None)
        self.assertEqual(self.dsp.mp3_frames, 0)

        self.uc.mem_write(header_address, header)
        self.dsp.capture_mp3_frame(self.uc, 0, 0, None)
        self.uc.reg_write(UC_ARM_REG_R0, 0)
        self.dsp.finish_mp3(self.uc, 0, 0, None)
        self.assertEqual(self.dsp.read_clip(1), ((2, "ended"), (2, "audio/mpeg", prefill, 2, None)))

    def test_movie_mp3_batches_report_their_start_position(self):
        # 48 kb/s MPEG-2 Layer III at 22.05 kHz, with and without padding.
        first = b"\xff\xf3\x60\xc4" + bytes(152)
        padded = b"\xff\xf3\x62\xc4" + bytes(153)
        self.dsp.start_file_mp3(22050, movie=True)
        self.dsp.mp3_batch.extend(first + padded)
        self.dsp.publish_mp3()
        self.dsp.mp3_batch.extend(first)
        self.dsp.publish_mp3()

        _, first_clip = self.dsp.read_clip(0)
        _, second_clip = self.dsp.read_clip(first_clip[0])
        self.assertEqual(first_clip[4], 0)
        self.assertAlmostEqual(second_clip[4], 2 * 576 * 1000 / 22050)


if __name__ == "__main__":
    unittest.main()
