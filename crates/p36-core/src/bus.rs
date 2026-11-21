use armv4t_emu::Memory;
use std::collections::BTreeMap;

use crate::audio::Dsp;
use crate::input::{Keypad, Touch, GPIO_ENABLE, GPIO_INPUT, GPIO_STATUS};
use crate::lcd::{self, Lcd, VideoPosition};
use crate::nes;
use crate::ram;
use crate::sd::{BlockDevice, SdCard, L2_BASE, L2_SRAM, SD_BASE};
use crate::timers::{Timers, IRQ_PENDING, TIMER_IRQ};
use crate::video::MovieDecoder;

const VECTORS: [u32; 8] = [
    0x3000_0020,
    0x3021_76c0,
    0x3000_0100,
    0x3021_76fc,
    0x3021_7738,
    0x3000_0020,
    0x3021_7618,
    0x3021_766c,
];

pub(crate) struct Bus<D> {
    pub ram: Box<[u8]>,
    low: Box<[u8]>,
    l2: Box<[u8]>,
    system: Box<[u8]>,
    peripherals: Box<[u8]>,
    unmapped: BTreeMap<u32, u32>,
    pub(crate) lcd: Lcd,
    pub(crate) movie: MovieDecoder,
    pub(crate) timers: Timers,
    pub(crate) sd: SdCard<D>,
    pub(crate) keypad: Keypad,
    pub(crate) touch: Touch,
    pub(crate) dsp: Dsp,
    pub(crate) pc: u32,
}

impl<D: BlockDevice> Bus<D> {
    pub(crate) fn new(firmware: &[u8], image: D) -> Result<Self, String> {
        if firmware.len() < 0x217920
            || firmware.len() > ram::SIZE
            || !firmware.starts_with(&0xea00_0006u32.to_le_bytes())
        {
            return Err("Expected a P36 Sword ARM firmware image".into());
        }
        let mut ram = vec![0; ram::SIZE].into_boxed_slice();
        ram[..firmware.len()].copy_from_slice(firmware);
        for (offset, value) in [
            (0x217848, 0xe1a0_0000u32),
            (0x217910, 0xe12f_ff1e),
            (0x21791c, 0xe12f_ff1e),
        ] {
            ram[offset..offset + 4].copy_from_slice(&value.to_le_bytes());
        }
        let mut low = vec![0; 0x1000].into_boxed_slice();
        for (index, vector) in VECTORS.into_iter().enumerate() {
            let offset = index * 4;
            low[offset..offset + 4].copy_from_slice(&0xe59f_f018u32.to_le_bytes());
            low[32 + offset..36 + offset].copy_from_slice(&vector.to_le_bytes());
        }
        let mut system = vec![0; 0x10000].into_boxed_slice();
        system[..4].copy_from_slice(&0x3332_3236u32.to_le_bytes());
        Ok(Self {
            ram,
            low,
            system,
            l2: vec![0; 0x10000].into_boxed_slice(),
            peripherals: vec![0; 0x100000].into_boxed_slice(),
            unmapped: BTreeMap::new(),
            lcd: Lcd::default(),
            movie: MovieDecoder::default(),
            timers: Timers::default(),
            sd: SdCard::new(image)?,
            keypad: Keypad::new(firmware).ok_or("Invalid firmware keypad matrix")?,
            touch: Touch::default(),
            dsp: Dsp::default(),
            pc: ram::BASE,
        })
    }

    pub(crate) fn bytes_at(&self, address: u32, len: usize) -> Option<&[u8]> {
        let start = ram::offset(address, len)?;
        Some(&self.ram[start..start + len])
    }

    pub(crate) fn pending_irqs(&self) -> u32 {
        self.timers.read(IRQ_PENDING).unwrap_or_default()
            | if self.dsp.pending { 1 << 5 } else { 0 }
            | if self.gpio_pending() { TIMER_IRQ } else { 0 }
    }

    fn gpio_pending(&self) -> bool {
        (self.touch.edge && self.raw_word(GPIO_ENABLE) & 1 != 0)
            || self.keypad.pending(|address| self.raw_word(address))
    }

    fn register(&mut self, address: u32) -> u32 {
        match address {
            IRQ_PENDING => self.pending_irqs(),
            0x0800_0070 => (self.raw_word(address) & !0x3ff) | 640,
            GPIO_INPUT..=0x0800_00c8 => {
                let bank = ((address - GPIO_INPUT) / 4) as usize;
                let levels = self.keypad.levels(bank, |addr| self.raw_word(addr));
                let value = (self.raw_word(address) & !self.keypad.input_mask[bank]) | levels;
                if bank == 0 {
                    (value & !1) | u32::from(!self.touch.down)
                } else {
                    value
                }
            }
            0x0800_0068 => self.touch.y,
            0x0800_006c => self.touch.x,
            0x0800_004c => {
                self.timers.read(address).unwrap_or_default()
                    | if self.gpio_pending() { GPIO_STATUS } else { 0 }
            }
            0x0800_0004 | 0x0800_000c => 0,
            0x0800_0000..=0x0800_00fc => self
                .timers
                .read(address)
                .unwrap_or_else(|| self.raw_word(address)),
            SD_BASE..=0x2002_0ffc | 0x2002_c080 => self
                .sd
                .read_register(address)
                .unwrap_or_else(|| self.raw_word(address)),
            0x2005_0024 => u32::from(self.dsp.read()),
            _ => self.raw_word(address),
        }
    }

    fn write_register(&mut self, address: u32, mut value: u32) {
        if address == GPIO_ENABLE && value & 1 == 0 {
            self.touch.edge = false;
        }
        match address {
            0x0800_0000..=0x0800_00fc => self.timers.write(address, value),
            lcd::BASE..=0x2001_0ffc => {
                let movie = self
                    .dsp
                    .file_mp3
                    .movie_track()
                    .zip(self.movie.position_ms)
                    .map(|(track, milliseconds)| VideoPosition {
                        track,
                        milliseconds,
                    });
                self.lcd.write(address, value, &self.ram, movie);
            }
            nes::RGB_CONTROL if value == nes::RGB24_TO_RGB565 => {
                let parameters = std::array::from_fn(|index| {
                    self.raw_word(nes::RGB_CONTROL + 8 + index as u32 * 4)
                });
                if nes::convert_rgb24(&mut self.ram, parameters) {
                    value &= !1;
                }
            }
            SD_BASE..=0x2002_0ffc | L2_BASE..=0x2002_c0fc => {
                self.sd
                    .write_register(address, value, &mut self.ram, &mut self.l2)
            }
            0x2005_0020 => self.dsp.select(value as u16),
            0x2005_0024 => self.dsp.write(value as u16, self.pc),
            _ => {}
        }
        if let Some(bytes) = self.region_mut(address) {
            bytes.copy_from_slice(&value.to_le_bytes());
        } else {
            self.unmapped.insert(address, value);
        }
    }

    fn raw_word(&self, address: u32) -> u32 {
        let region = match address {
            0..=0x0ffc => (&self.low, 0),
            0x0800_0000..=0x0800_fffc => (&self.system, 0x0800_0000),
            0x2000_0000..=0x200f_fffc => (&self.peripherals, 0x2000_0000),
            L2_SRAM..=0x4800_fffc => (&self.l2, L2_SRAM),
            _ => return self.unmapped.get(&address).copied().unwrap_or_default(),
        };
        let start = (address - region.1) as usize;
        u32::from_le_bytes(region.0[start..start + 4].try_into().unwrap())
    }

    fn region_mut(&mut self, address: u32) -> Option<&mut [u8]> {
        let (region, base) = match address {
            0..=0x0ffc => (&mut self.low, 0),
            0x0800_0000..=0x0800_fffc => (&mut self.system, 0x0800_0000),
            0x2000_0000..=0x200f_fffc => (&mut self.peripherals, 0x2000_0000),
            L2_SRAM..=0x4800_fffc => (&mut self.l2, L2_SRAM),
            _ => return None,
        };
        let start = (address - base) as usize;
        Some(&mut region[start..start + 4])
    }

    fn read<const N: usize>(&mut self, address: u32) -> [u8; N] {
        if let Some(data) = self.bytes_at(address, N) {
            data.try_into().unwrap()
        } else {
            let shift = (address & 3) * 8;
            (self.register(address & !3) >> shift).to_le_bytes()[..N]
                .try_into()
                .unwrap()
        }
    }

    fn write<const N: usize>(&mut self, address: u32, bytes: [u8; N]) {
        if let Some(start) = ram::offset(address, N) {
            self.ram[start..start + N].copy_from_slice(&bytes);
        } else {
            let register = address & !3;
            let shift = (address & 3) as usize;
            let mut word = if N == 4 {
                [0; 4]
            } else {
                self.raw_word(register).to_le_bytes()
            };
            word[shift..shift + N].copy_from_slice(&bytes);
            self.write_register(register, u32::from_le_bytes(word));
        }
    }
}

impl<D: BlockDevice> Memory for Bus<D> {
    fn r8(&mut self, addr: u32) -> u8 {
        self.read::<1>(addr)[0]
    }
    fn r16(&mut self, addr: u32) -> u16 {
        u16::from_le_bytes(self.read(addr))
    }
    fn r32(&mut self, addr: u32) -> u32 {
        u32::from_le_bytes(self.read(addr))
    }
    fn w8(&mut self, addr: u32, val: u8) {
        self.write(addr, [val]);
    }
    fn w16(&mut self, addr: u32, val: u16) {
        self.write(addr, val.to_le_bytes());
    }
    fn w32(&mut self, addr: u32, val: u32) {
        self.write(addr, val.to_le_bytes());
    }
}
