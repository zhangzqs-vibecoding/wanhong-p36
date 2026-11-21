"""Drive the P36 keypad matrix through the firmware's GPIO registers."""

from itertools import product
import struct


MATRIX = 0x30224B90
GPIO_INPUT = 0x080000BC
GPIO_OUTPUT = 0x08000080
GPIO_ENABLE = 0x080000E0
GPIO_POLARITY = 0x080000F0
GPIO_IRQ_STATUS = 1 << 26


class Keypad:
    def __init__(self, firmware, base):
        rows, columns, row_pins, column_pins, codes = struct.unpack_from(
            "<5I", firmware, MATRIX - base
        )
        row_pins = firmware[row_pins - base:row_pins - base + rows]
        column_pins = firmware[column_pins - base:column_pins - base + columns]
        codes = struct.unpack_from(f"<{rows * columns}I", firmware, codes - base)
        self.pins = {
            code: pins
            for code, pins in zip(codes, product(row_pins, column_pins))
            if code != 0xFF
        }
        self.columns = [0] * 4
        for pin in column_pins:
            bank, bit = divmod(pin, 32)
            self.columns[bank] |= 1 << bit
        self.pressed = set()

    def set_key(self, code, down):
        if down:
            self.pressed.add(code)
        else:
            self.pressed.discard(code)

    @staticmethod
    def read_word(uc, address):
        return struct.unpack("<I", uc.mem_read(address, 4))[0]

    def levels(self, uc, bank):
        levels = 0
        for code in self.pressed:
            row, column = self.pins[code]
            column_bank, column_bit = divmod(column, 32)
            if bank != column_bank:
                continue
            row_bank, row_bit = divmod(row, 32)
            outputs = self.read_word(uc, GPIO_OUTPUT + 8 * row_bank)
            if outputs & (1 << row_bit):
                levels |= 1 << column_bit
        return levels

    def input(self, uc, register):
        bank = (register - GPIO_INPUT) // 4
        return (self.read_word(uc, register) & ~self.columns[bank]) | self.levels(uc, bank)

    def pending(self, uc):
        for bank, columns in enumerate(self.columns):
            if columns:
                enabled = self.read_word(uc, GPIO_ENABLE + 4 * bank)
                polarity = self.read_word(uc, GPIO_POLARITY + 4 * bank)
                if enabled & columns & (self.levels(uc, bank) ^ polarity):
                    return True
        return False
