import struct
import unittest

from unicorn import UC_ARCH_ARM, UC_MODE_ARM, Uc

from keypad import GPIO_ENABLE, GPIO_INPUT, GPIO_OUTPUT, GPIO_POLARITY, Keypad
from machine import FIRMWARE, SDRAM_BASE


class KeypadTests(unittest.TestCase):
    def test_release_asserts_gpio_interrupt_after_polarity_change(self):
        keypad = Keypad(FIRMWARE.read_bytes(), SDRAM_BASE)
        uc = Uc(UC_ARCH_ARM, UC_MODE_ARM)
        uc.mem_map(0x08000000, 0x1000)
        code = ord("Q")
        row, column = keypad.pins[code]
        row_bank, row_bit = divmod(row, 32)
        column_bank, column_bit = divmod(column, 32)
        column_mask = 1 << column_bit
        uc.mem_write(GPIO_OUTPUT + 8 * row_bank, struct.pack("<I", 1 << row_bit))
        uc.mem_write(GPIO_ENABLE + 4 * column_bank, struct.pack("<I", column_mask))

        self.assertFalse(keypad.pending(uc))
        keypad.set_key(code, True)
        self.assertEqual(keypad.input(uc, GPIO_INPUT + 4 * column_bank), column_mask)
        self.assertTrue(keypad.pending(uc))

        uc.mem_write(GPIO_POLARITY + 4 * column_bank, struct.pack("<I", column_mask))
        self.assertFalse(keypad.pending(uc))
        keypad.set_key(code, False)
        self.assertEqual(keypad.input(uc, GPIO_INPUT + 4 * column_bank), 0)
        self.assertTrue(keypad.pending(uc))
