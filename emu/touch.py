"""Model the P36 touch panel and route its input to the firmware UI."""

import struct

from unicorn import UC_HOOK_CODE
from unicorn.arm_const import UC_ARM_REG_R0, UC_ARM_REG_R1

from keypad import GPIO_ENABLE, Keypad


TOUCH_ADC_Y = 0x08000068
TOUCH_ADC_X = 0x0800006C
TOUCH_MESSAGE = 0xFFFF000A
POINTER_MESSAGE = 0xD03C
POINTER_MOVE = 5


class TouchPanel:
    def __init__(self, uc, trace):
        self.trace = trace
        self.down = False
        self.edge = False
        self.calibrating = False
        self.adc_x = 511
        self.adc_y = 240
        for address in (0x30076A04, 0x30076A98):
            uc.hook_add(UC_HOOK_CODE, self.translate_pointer, begin=address, end=address)
        for address, active in ((0x301064DC, True), (0x3010663C, False)):
            uc.hook_add(UC_HOOK_CODE, self.set_calibration, active, begin=address, end=address)

    def set_contact(self, x, y, phase):
        down = self.down if phase == POINTER_MOVE else phase == 2
        if down:
            self.adc_x = 1023 - max(1, x)
            self.adc_y = max(1, y)
        if self.down != down:
            self.edge = True
            self.down = down
        if self.trace:
            print(f"touch contact phase={phase} adc_x={self.adc_x} adc_y={self.adc_y}")

    def pending(self, uc):
        return self.edge and bool(Keypad.read_word(uc, GPIO_ENABLE) & 1)

    def set_calibration(self, uc, address, size, active):
        self.calibrating = active
        if self.trace:
            print(f"fw touch calibration active={active}")

    def translate_pointer(self, uc, address, size, data):
        # The calibration screen consumes the firmware's raw touch message.
        if self.calibrating:
            return
        message = uc.reg_read(UC_ARM_REG_R0)
        kind, x, y, phase = struct.unpack("<IHHH", uc.mem_read(message, 10))
        if kind == TOUCH_MESSAGE and phase in (1, 2, POINTER_MOVE):
            uc.mem_write(message, struct.pack("<III", POINTER_MESSAGE, (x << 16) | phase, y))
            uc.reg_write(UC_ARM_REG_R1, 0)
            if self.trace:
                print(f"fw touch phase={phase} x={x} y={y}")
