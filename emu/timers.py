"""Anyka five-channel timer bank and its shared IRQ."""

TIMER_FIRST = 0x08000018
TIMER_LAST = 0x08000028
IRQ_STATUS = 0x0800004C
IRQ_ENABLE = 0x08000034
IRQ_PENDING = 0x080000CC
TIMER_IRQ = 1 << 27

RELOAD_MASK = (1 << 26) - 1
TIMER_ENABLE = 1 << 26
TIMER_ACK = 1 << 28
INSTRUCTIONS_PER_TIMER_CLOCK = 8
TIMER_CYCLES_PER_SECOND = 12_000_000 * INSTRUCTIONS_PER_TIMER_CLOCK


class Timers:
    def __init__(self):
        self.config = [0] * 5
        self.remaining = [0] * 5
        self.pending = 0
        self.control = 0

    def write_register(self, address, value):
        if address == IRQ_STATUS:
            self.control = value & ~0x003E0000
            return True
        if TIMER_FIRST <= address <= TIMER_LAST:
            channel = (address - TIMER_FIRST) // 4
            if value & TIMER_ACK or not value & TIMER_ENABLE:
                self.pending &= ~(1 << channel)
            value &= ~TIMER_ACK
            if value != self.config[channel]:
                self.remaining[channel] = self.period(value)
            self.config[channel] = value
            return True
        return False

    def read_register(self, address):
        if address == IRQ_STATUS:
            flags = sum(1 << (21 - channel) for channel in range(5)
                        if self.pending & (1 << channel))
            return self.control | flags
        if address == IRQ_PENDING:
            return TIMER_IRQ if self.pending else 0
        if TIMER_FIRST <= address <= TIMER_LAST:
            return self.config[(address - TIMER_FIRST) // 4]

    def advance(self, instructions):
        for channel, config in enumerate(self.config):
            if not config & TIMER_ENABLE:
                continue
            remaining = self.remaining[channel] - instructions
            if remaining <= 0:
                self.pending |= 1 << channel
                period = self.period(config)
                remaining = (remaining - 1) % period + 1
            self.remaining[channel] = remaining

    @staticmethod
    def period(config):
        return ((config & RELOAD_MASK) + 1) * INSTRUCTIONS_PER_TIMER_CLOCK
