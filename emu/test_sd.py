import struct
from pathlib import Path
from tempfile import TemporaryDirectory, TemporaryFile
import unittest

from unicorn import UC_ARCH_ARM, UC_MODE_ARM, Uc

from sd import DATA_TIMEOUT, L2_BASE, L2_SRAM, SD_BASE, SDRAM_BASE, SdCard
from sd_state import open_sd_state


class SdCardTests(unittest.TestCase):
    def setUp(self):
        image = TemporaryFile()
        self.addCleanup(image.close)
        image.truncate(512 * 1024)
        self.original = bytes(range(256)) * 8
        image.write(self.original)
        image.flush()
        self.sd = SdCard(image)
        self.uc = Uc(UC_ARCH_ARM, UC_MODE_ARM)
        for address in (SD_BASE, L2_BASE, L2_SRAM, SDRAM_BASE):
            self.uc.mem_map(address, 0x1000)
        self.command(7, 0x10000)

    def write(self, address, value):
        completed = self.sd.write_register(self.uc, address, value)
        stored = value if completed is None else completed
        self.uc.mem_write(address, struct.pack("<I", stored))
        return completed

    def command(self, number, argument):
        self.write(SD_BASE + 0x08, argument)
        self.write(SD_BASE + 0x0C, (number << 1) | 1)

    def dma(self, buffer, length, outbound):
        self.write(L2_BASE + 4 * buffer, 0)
        self.write(L2_BASE + 0x40 + 4 * buffer, length // 64)
        self.write(L2_BASE + 0x88, int(outbound) << (8 + buffer))
        self.assertEqual(self.write(L2_BASE + 0x80, 1 << (24 + buffer)), 0)

    def test_multi_block_write_length_arrives_after_command(self):
        self.write(L2_BASE + 0x90, 3 << 12)
        self.write(SD_BASE + 0x28, 512)
        self.command(25, 0)
        self.write(SD_BASE + 0x28, 2048)
        self.write(SD_BASE + 0x2C, 1)
        payload = self.original[::-1]
        self.uc.mem_write(SDRAM_BASE, payload)
        self.dma(3, 2048, True)
        self.command(12, 0)
        self.assertFalse(self.sd.status & DATA_TIMEOUT)
        self.command(18, 0)
        self.dma(3, 2048, False)
        self.assertEqual(self.uc.mem_read(SDRAM_BASE, 2048), payload)
        self.sd.image.seek(0)
        self.assertEqual(self.sd.image.read(2048), self.original)

    def test_single_block_write_dma_arrives_before_command(self):
        payload = b"P36" * 170 + b"OK"
        self.uc.mem_write(SDRAM_BASE, payload)
        self.dma(4, 512, True)
        self.command(24, 1)
        self.write(L2_BASE + 0x90, 4 << 12)
        self.write(SD_BASE + 0x28, 512)
        self.write(SD_BASE + 0x2C, 1)
        self.assertFalse(self.sd.status & DATA_TIMEOUT)
        self.command(17, 1)
        self.dma(4, 512, False)
        self.assertEqual(self.uc.mem_read(SDRAM_BASE, 512), payload)

    def test_saved_sectors_survive_reopening_and_leave_base_intact(self):
        payload = b"P36" * 340 + b"SAVE"
        image = self.sd.image
        with TemporaryDirectory() as directory:
            path = Path(directory) / "card.sqlite3"
            with open_sd_state(image, path) as saved:
                self.sd = SdCard(image, saved_sectors=saved)
                self.command(7, 0x10000)
                self.write(L2_BASE + 0x90, 4 << 12)
                self.write(SD_BASE + 0x28, 1024)
                self.command(25, 1)
                self.write(SD_BASE + 0x2C, 1)
                self.uc.mem_write(SDRAM_BASE, payload)
                self.dma(4, 1024, True)
                with self.assertRaisesRegex(ValueError, "already in use"):
                    with open_sd_state(image, path):
                        self.fail("opened a second writer")

            with open_sd_state(image, path) as saved:
                self.sd = SdCard(image, saved_sectors=saved)
                self.command(7, 0x10000)
                self.write(L2_BASE + 0x90, 4 << 12)
                self.write(SD_BASE + 0x28, 2048)
                self.command(18, 0)
                self.dma(4, 2048, False)
                expected = self.original[:512] + payload + self.original[1536:]
                self.assertEqual(self.uc.mem_read(SDRAM_BASE, 2048), expected)
        image.seek(0)
        self.assertEqual(image.read(2048), self.original)


if __name__ == "__main__":
    unittest.main()
