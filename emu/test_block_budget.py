import unittest

from unicorn import UC_ARCH_ARM, UC_MODE_ARM, Uc
from unicorn.arm_const import UC_ARM_REG_R0, UC_ARM_REG_R1

from block_budget import BlockBudget


class BlockBudgetTests(unittest.TestCase):
    def test_switch_between_block_and_instruction_budgets(self):
        uc = Uc(UC_ARCH_ARM, UC_MODE_ARM)
        budget = BlockBudget.load(uc)
        if budget is None:
            self.skipTest("Build the optional native library with make -C emu/nes_audio")
        uc.mem_map(0, 0x1000)
        # add r0,r0,#1; subs r1,r1,#1; bne loop; bx lr
        uc.mem_write(0x100, bytes.fromhex("010080e2 011051e2 fcffff1a 1eff2fe1"))

        try:
            for _ in range(3):
                uc.reg_write(UC_ARM_REG_R0, 0)
                uc.reg_write(UC_ARM_REG_R1, 1000)
                budget.run(0x100, 30)
                self.assertEqual(uc.reg_read(UC_ARM_REG_R0), 10)

                budget.close()
                uc.emu_start(0x100, 0, count=6)
                self.assertEqual(uc.reg_read(UC_ARM_REG_R0), 12)
        finally:
            budget.close()
