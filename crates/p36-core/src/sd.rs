//! SDHC command handling and transfers through the Anyka L2 controller.

use std::collections::{BTreeMap, BTreeSet};

use crate::ram;

pub(crate) const SD_BASE: u32 = 0x2002_0000;
pub(crate) const L2_BASE: u32 = 0x2002_c000;
pub(crate) const L2_SRAM: u32 = 0x4800_0000;
pub const SECTOR_SIZE: usize = 512;
const DATA_TIMEOUT: u32 = 0x08;
const RESPONSE_TIMEOUT: u32 = 0x04;
const MAX_TRANSFER: usize = 32 * 1024;

/// The platform supplies a fixed-size, read-only SD image by sector.
pub trait BlockDevice {
    fn sector_count(&self) -> u64;
    fn read_sectors(&mut self, first: u32, output: &mut [u8]) -> Result<(), String>;
}

pub(crate) struct SdCard<D> {
    image: D,
    sectors: u64,
    argument: u32,
    length: usize,
    status: u32,
    response: [u32; 4],
    app_command: bool,
    selected: bool,
    buffer: Option<usize>,
    remaining_read: usize,
    read_offset: usize,
    read_data: Box<[u8]>,
    write_sector: Option<u32>,
    write_offset: usize,
    single_write: bool,
    sdram_addresses: [u32; 8],
    dma_lengths: [usize; 8],
    dma_direction: u32,
    dma_control: u32,
    pub(crate) error: Option<String>,
    changed: BTreeMap<u32, [u8; SECTOR_SIZE]>,
    dirty: BTreeSet<u32>,
}

impl<D: BlockDevice> SdCard<D> {
    pub(crate) fn new(image: D) -> Result<Self, String> {
        let sectors = image.sector_count();
        if sectors == 0 || sectors > 1 << 32 || !sectors.is_multiple_of(1024) {
            return Err("SDHC image size must be a positive multiple of 512 KiB".into());
        }
        Ok(Self {
            image,
            sectors,
            argument: 0,
            length: 0,
            status: 0,
            response: [0; 4],
            app_command: false,
            selected: false,
            buffer: None,
            remaining_read: 0,
            read_offset: 0,
            read_data: vec![0; MAX_TRANSFER].into_boxed_slice(),
            write_sector: None,
            write_offset: 0,
            single_write: false,
            sdram_addresses: [0; 8],
            dma_lengths: [0; 8],
            dma_direction: 0,
            dma_control: 0,
            error: None,
            changed: BTreeMap::new(),
            dirty: BTreeSet::new(),
        })
    }

    pub(crate) fn restore_sector(&mut self, sector: u32, bytes: [u8; SECTOR_SIZE]) -> bool {
        if u64::from(sector) >= self.sectors {
            return false;
        }
        self.changed.insert(sector, bytes);
        true
    }

    pub(crate) fn take_writes(&mut self) -> Vec<(u32, [u8; SECTOR_SIZE])> {
        std::mem::take(&mut self.dirty)
            .into_iter()
            .map(|sector| (sector, self.changed[&sector]))
            .collect()
    }

    pub(crate) fn read_register(&mut self, address: u32) -> Option<u32> {
        match address {
            0x2002_0014..=0x2002_0020 => {
                Some(self.response[((address - 0x2002_0014) / 4) as usize])
            }
            0x2002_0034 => Some(
                self.status
                    | if self.remaining_read > 0 && self.buffer.is_none() {
                        0x1800
                    } else {
                        0
                    },
            ),
            0x2002_0040 => {
                let mut bytes = [0; 4];
                if let Some(data) = self.take_read(4) {
                    bytes.copy_from_slice(data);
                }
                Some(u32::from_le_bytes(bytes))
            }
            0x2002_c080 => Some(self.dma_control),
            _ => None,
        }
    }

    pub(crate) fn write_register(
        &mut self,
        address: u32,
        value: u32,
        ram: &mut [u8],
        l2: &mut [u8],
    ) {
        match address {
            0x2002_0008 => self.argument = value,
            0x2002_000c if value & 1 != 0 => self.command((value >> 1) & 0x3f),
            0x2002_0028 => self.length = value as usize,
            0x2002_002c if value & 3 == 1 => self.enable_write(l2),
            0x2002_c000..=0x2002_c01c => {
                self.sdram_addresses[((address - L2_BASE) / 4) as usize] = value
            }
            0x2002_c040..=0x2002_c05c => {
                self.dma_lengths[((address - L2_BASE - 0x40) / 4) as usize] =
                    64 * (value as u8 as usize)
            }
            0x2002_c080 => self.transfer(value, ram, l2),
            0x2002_c088 => self.dma_direction = value,
            0x2002_c090 => self.buffer = Some(((value >> 12) & 7) as usize),
            _ => {}
        }
    }

    fn command(&mut self, command: u32) {
        let application = self.app_command;
        self.app_command = command == 55;
        self.status = 0x30;
        self.response = [if self.selected { 0x900 } else { 0x100 }, 0, 0, 0];
        let argument = self.argument;
        match command {
            0 => {
                self.selected = false;
                self.remaining_read = 0;
                self.write_sector = None;
            }
            8 => self.response[0] = argument & 0xfff,
            41 if application => self.response[0] = 0xc0ff_8000,
            2 => self.response = [0x0350_3350, 0x3336_5344, 0x0100_0000, 0x0101_1001],
            3 => self.response[0] = 0x0001_0700,
            9 => {
                let capacity = (self.sectors / 1024 - 1) as u32;
                self.response = [
                    0x400e_0032,
                    0x5b59_0000 | (capacity >> 16),
                    (capacity << 16) | 0x7f80,
                    0x0a40_0001,
                ];
            }
            7 => self.selected = argument != 0,
            17 | 18 => self.start_read(argument),
            24 | 25 => {
                self.write_sector = Some(argument);
                self.single_write = command == 24;
                self.write_offset = 0;
            }
            12 => {
                if self.write_sector.take().is_some() {
                    self.status |= DATA_TIMEOUT;
                }
                self.remaining_read = 0;
            }
            6 | 13 | 16 | 55 => {}
            _ => self.status = RESPONSE_TIMEOUT,
        }
    }

    fn valid_transfer(&self, sector: u32) -> bool {
        self.selected
            && self.length != 0
            && self.length <= MAX_TRANSFER
            && self.length.is_multiple_of(SECTOR_SIZE)
            && u64::from(sector) + (self.length / SECTOR_SIZE) as u64 <= self.sectors
    }

    fn start_read(&mut self, sector: u32) {
        self.remaining_read = 0;
        if !self.valid_transfer(sector) {
            self.status |= DATA_TIMEOUT;
            return;
        }
        let data = &mut self.read_data[..self.length];
        if let Err(error) = self.image.read_sectors(sector, data) {
            self.status |= DATA_TIMEOUT;
            self.error = Some(error);
            return;
        }
        for (offset, bytes) in data.as_chunks_mut::<SECTOR_SIZE>().0.iter_mut().enumerate() {
            if let Some(changed) = self.changed.get(&(sector + offset as u32)) {
                bytes.copy_from_slice(changed);
            }
        }
        self.read_offset = 0;
        self.remaining_read = self.length;
    }

    fn take_read(&mut self, len: usize) -> Option<&[u8]> {
        if len > self.remaining_read {
            return None;
        }
        let start = self.read_offset;
        self.read_offset += len;
        self.remaining_read -= len;
        Some(&self.read_data[start..self.read_offset])
    }

    fn enable_write(&mut self, l2: &[u8]) {
        let Some(sector) = self.write_sector else {
            return;
        };
        if !self.valid_transfer(sector) {
            self.status |= DATA_TIMEOUT;
            self.write_sector = None;
            return;
        }
        if self.single_write {
            if let Some(buffer) = self.buffer.filter(|_| self.length == SECTOR_SIZE) {
                let start = buffer * SECTOR_SIZE;
                self.write_blocks(&l2[start..start + SECTOR_SIZE]);
            } else {
                self.status |= DATA_TIMEOUT;
            }
        }
    }

    fn write_blocks(&mut self, data: &[u8]) {
        let Some(sector) = self.write_sector else {
            return;
        };
        if !self.valid_transfer(sector)
            || data.is_empty()
            || !data.len().is_multiple_of(SECTOR_SIZE)
            || self.write_offset + data.len() > self.length
        {
            self.status |= DATA_TIMEOUT;
            return;
        }
        let first = sector + (self.write_offset / SECTOR_SIZE) as u32;
        for (offset, bytes) in data.as_chunks::<SECTOR_SIZE>().0.iter().enumerate() {
            let sector = first + offset as u32;
            self.changed.insert(sector, *bytes);
            self.dirty.insert(sector);
        }
        self.write_offset += data.len();
        if self.write_offset == self.length {
            self.write_sector = None;
        }
    }

    fn transfer(&mut self, control: u32, ram: &mut [u8], l2: &mut [u8]) {
        self.dma_control = control;
        let requests = control >> 24;
        if !requests.is_power_of_two() {
            return;
        }
        let buffer = requests.trailing_zeros() as usize;
        let len = self.dma_lengths[buffer];
        let Some(address) = ram::offset(ram::BASE | self.sdram_addresses[buffer], len) else {
            return;
        };
        if len == 0 {
            return;
        }
        let data = &mut ram[address..address + len];
        if self.dma_direction & (1 << (8 + buffer)) != 0 {
            if self.buffer == Some(buffer) && self.write_sector.is_some() {
                self.write_blocks(data);
            } else if len <= SECTOR_SIZE {
                let start = buffer * SECTOR_SIZE;
                l2[start..start + len].copy_from_slice(data);
            } else {
                return;
            }
        } else if self.buffer == Some(buffer) && self.remaining_read > 0 {
            if let Some(source) = self.take_read(len) {
                data.copy_from_slice(source);
            } else {
                self.status |= DATA_TIMEOUT;
            }
        } else {
            return;
        }
        self.dma_control &= !(requests << 24);
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    struct BlankCard;

    impl BlockDevice for BlankCard {
        fn sector_count(&self) -> u64 {
            1024
        }

        fn read_sectors(&mut self, _: u32, output: &mut [u8]) -> Result<(), String> {
            output.fill(0);
            Ok(())
        }
    }

    #[test]
    fn sd_overlay_restores_and_reports_only_new_writes() {
        let mut card = SdCard::new(BlankCard).unwrap();
        card.selected = true;
        card.length = SECTOR_SIZE;

        assert!(card.restore_sector(7, [1; SECTOR_SIZE]));
        assert!(!card.restore_sector(1024, [1; SECTOR_SIZE]));
        assert!(card.take_writes().is_empty());
        card.start_read(7);
        assert_eq!(card.take_read(SECTOR_SIZE), Some(&[1; SECTOR_SIZE][..]));

        for value in [2, 3] {
            card.write_sector = Some(7);
            card.write_offset = 0;
            card.write_blocks(&[value; SECTOR_SIZE]);
        }
        assert_eq!(card.take_writes(), vec![(7, [3; SECTOR_SIZE])]);
        assert!(card.take_writes().is_empty());
        card.start_read(7);
        assert_eq!(card.take_read(SECTOR_SIZE), Some(&[3; SECTOR_SIZE][..]));
    }
}
