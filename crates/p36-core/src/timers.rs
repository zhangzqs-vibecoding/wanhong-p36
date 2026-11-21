pub(crate) const IRQ_ENABLE: u32 = 0x0800_0034;
pub(crate) const IRQ_PENDING: u32 = 0x0800_00cc;
const IRQ_STATUS: u32 = 0x0800_004c;
const FIRST: u32 = 0x0800_0018;
const LAST: u32 = 0x0800_0028;
pub(crate) const TIMER_IRQ: u32 = 1 << 27;
const RELOAD_MASK: u32 = (1 << 26) - 1;
const ENABLE: u32 = 1 << 26;
const ACK: u32 = 1 << 28;

#[derive(Default)]
pub(crate) struct Timers {
    config: [u32; 5],
    remaining: [u32; 5],
    pending: u32,
    control: u32,
}

impl Timers {
    pub(crate) fn read(&self, address: u32) -> Option<u32> {
        match address {
            IRQ_STATUS => Some(self.control | (self.pending.reverse_bits() >> 10)),
            IRQ_PENDING => Some(if self.pending != 0 { TIMER_IRQ } else { 0 }),
            FIRST..=LAST => Some(self.config[((address - FIRST) / 4) as usize]),
            _ => None,
        }
    }

    pub(crate) fn write(&mut self, address: u32, value: u32) {
        match address {
            IRQ_STATUS => self.control = value & !0x003e_0000,
            FIRST..=LAST => {
                let channel = ((address - FIRST) / 4) as usize;
                if value & ACK != 0 || value & ENABLE == 0 {
                    self.pending &= !(1 << channel);
                }
                let config = value & !ACK;
                if self.config[channel] != config {
                    self.remaining[channel] = Self::period(config);
                    self.config[channel] = config;
                }
            }
            _ => {}
        }
    }

    pub(crate) fn advance(&mut self, cycles: u32) {
        for (channel, &config) in self.config.iter().enumerate() {
            if config & ENABLE != 0 {
                let remaining = &mut self.remaining[channel];
                if cycles >= *remaining {
                    self.pending |= 1 << channel;
                    let period = Self::period(config);
                    *remaining = period - (cycles - *remaining) % period;
                } else {
                    *remaining -= cycles;
                }
            }
        }
    }

    fn period(config: u32) -> u32 {
        ((config & RELOAD_MASK) + 1) * 8
    }
}
