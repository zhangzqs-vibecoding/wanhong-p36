use crate::{BlockDevice, SECTOR_SIZE};
use std::io::{self, Read, Seek, SeekFrom, Write};

/// Find the main firmware on a FAT card image as used by the P36.
pub fn read_card_firmware(card: &mut impl BlockDevice) -> Result<Vec<u8>, String> {
    let length = card.sector_count().saturating_mul(SECTOR_SIZE as u64);
    let disk = ReadOnlyDisk {
        card,
        offset: 0,
        length,
    };
    let fs =
        fatfs::FileSystem::new(disk, fatfs::FsOptions::new()).map_err(|error| error.to_string())?;
    let mut firmware = Vec::new();
    fs.root_dir()
        .open_file("sword_elfd.bin")
        .map_err(|error| format!("sword_elfd.bin: {error}"))?
        .take(32 * 1024 * 1024 + 1)
        .read_to_end(&mut firmware)
        .map_err(|error| error.to_string())?;
    Ok(firmware)
}

struct ReadOnlyDisk<'a, D> {
    card: &'a mut D,
    offset: u64,
    length: u64,
}

impl<D: BlockDevice> Read for ReadOnlyDisk<'_, D> {
    fn read(&mut self, output: &mut [u8]) -> io::Result<usize> {
        let len = self
            .length
            .saturating_sub(self.offset)
            .min(output.len() as u64) as usize;
        let mut output = &mut output[..len];
        while !output.is_empty() {
            let sector = (self.offset / SECTOR_SIZE as u64) as u32;
            let skip = (self.offset % SECTOR_SIZE as u64) as usize;
            let count = if skip == 0 && output.len() >= SECTOR_SIZE {
                let count = output.len() / SECTOR_SIZE * SECTOR_SIZE;
                self.card
                    .read_sectors(sector, &mut output[..count])
                    .map_err(io::Error::other)?;
                count
            } else {
                let mut bytes = [0; SECTOR_SIZE];
                self.card
                    .read_sectors(sector, &mut bytes)
                    .map_err(io::Error::other)?;
                let count = output.len().min(SECTOR_SIZE - skip);
                output[..count].copy_from_slice(&bytes[skip..skip + count]);
                count
            };
            self.offset += count as u64;
            output = &mut output[count..];
        }
        Ok(len)
    }
}

impl<D> Seek for ReadOnlyDisk<'_, D> {
    fn seek(&mut self, seek: SeekFrom) -> io::Result<u64> {
        self.offset = match seek {
            SeekFrom::Start(offset) => Some(offset),
            SeekFrom::End(delta) => self.length.checked_add_signed(delta),
            SeekFrom::Current(delta) => self.offset.checked_add_signed(delta),
        }
        .ok_or(io::ErrorKind::InvalidInput)?;
        Ok(self.offset)
    }
}

impl<D> Write for ReadOnlyDisk<'_, D> {
    fn write(&mut self, _: &[u8]) -> io::Result<usize> {
        Err(io::ErrorKind::Unsupported.into())
    }
    fn flush(&mut self) -> io::Result<()> {
        Ok(())
    }
}
