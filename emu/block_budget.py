"""Bound real-time NES slices without Unicorn's per-instruction hook."""

import ctypes
from pathlib import Path

from unicorn import UC_HOOK_BLOCK, UcError


LIBRARY = Path(__file__).with_name("nes_audio") / "libp36slice.so"


class Budget(ctypes.Structure):
    _fields_ = [("remaining_bytes", ctypes.c_size_t), ("stop", ctypes.c_void_p)]


class BlockBudget:
    @classmethod
    def load(cls, uc):
        try:
            from unicorn.unicorn_py3.unicorn import uclib

            return cls(uc, uclib, ctypes.CDLL(str(LIBRARY)))
        except (AttributeError, ImportError, OSError):
            return None

    def __init__(self, uc, uclib, library):
        self.uc = uc
        self.engine = uc._uch
        self.api = uclib
        self.callback = library.p36_stop_at_block_budget
        self.budget = Budget(0, ctypes.cast(uclib.uc_emu_stop, ctypes.c_void_p))
        self.hook = None

    def run(self, begin, count):
        if self.hook is None:
            handle = ctypes.c_size_t()
            status = self.api.uc_hook_add(
                self.engine, ctypes.byref(handle), UC_HOOK_BLOCK, self.callback,
                ctypes.byref(self.budget), 1, 0,
            )
            if status:
                raise UcError(status)
            self.hook = handle
            self.uc.ctl_flush_tb()
        # ARM instructions are four bytes. Thumb blocks may execute more
        # instructions per slice, so use this only with the wall-clock timer.
        self.budget.remaining_bytes = count * 4
        self.uc.emu_start(begin, 0)

    def close(self):
        if self.hook is not None:
            status = self.api.uc_hook_del(self.engine, self.hook)
            if status:
                raise UcError(status)
            self.hook = None
            self.uc.ctl_flush_tb()
