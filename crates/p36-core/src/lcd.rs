use std::collections::VecDeque;

use crate::{ram, video::VideoLayer};

pub(crate) const BASE: u32 = 0x2001_0000;
const STARTUP_FRAMES: usize = 24;

#[derive(Clone, Copy)]
pub struct VideoPosition {
    pub track: u32,
    pub milliseconds: u32,
}

pub struct Frame<'a> {
    pub sequence: u64,
    pub width: u32,
    pub height: u32,
    pub rgb565: &'a [u8],
    pub movie: Option<VideoPosition>,
}

pub struct MovieFrame {
    pub sequence: u64,
    pub width: u32,
    pub height: u32,
    pub rgb565: Vec<u8>,
    pub position: VideoPosition,
}

#[derive(Default)]
pub(crate) struct Lcd {
    address: u32,
    geometry: u32,
    latched: Option<(usize, usize, u32, u32)>,
    sequence: u64,
    control: u32,
    layer: VideoLayer,
    video_enabled: bool,
    video_valid: bool,
    video_pixels: Vec<u8>,
    movie: Option<VideoPosition>,
    startup: VecDeque<MovieFrame>,
    last_startup: Option<VideoPosition>,
}

impl Lcd {
    pub(crate) fn write(
        &mut self,
        address: u32,
        value: u32,
        ram: &[u8],
        movie: Option<VideoPosition>,
    ) {
        match address - BASE {
            0 => self.control = value,
            0x14 => self.address = ram::BASE + (value & 0x0fff_ffff),
            register @ (0x5c | 0x60 | 0x64) => {
                self.layer.addresses[((register - 0x5c) / 4) as usize] = value
            }
            0x68 => self.layer.horizontal = value,
            0x6c => self.layer.vertical = value,
            0x74 => self.layer.position = value,
            0xac => self.geometry = value,
            0xc8 if value & 0x800 != 0 => {
                let width = (self.geometry >> 10) & 0x7ff;
                let height = self.geometry & 0x3ff;
                let len = (width * height * 2) as usize;
                if let Some(start) = ram::offset(self.address, len).filter(|_| len != 0) {
                    self.latched = Some((start, len, width, height));
                    self.sequence += 1;
                    self.video_enabled = self.control & 4 != 0;
                    self.video_valid = self.video_enabled
                        && self.layer.render(
                            ram,
                            &mut self.video_pixels,
                            width as usize,
                            height as usize,
                        );
                    self.movie = movie.filter(|_| self.video_valid);
                    self.capture_startup(width, height);
                }
            }
            _ => {}
        }
    }

    pub(crate) fn video_enabled(&self) -> bool {
        self.video_enabled
    }

    pub(crate) fn take_startup(&mut self) -> Option<MovieFrame> {
        self.startup.pop_front()
    }

    fn capture_startup(&mut self, width: u32, height: u32) {
        let Some(position) = self.movie.filter(|position| position.milliseconds < 1000) else {
            return;
        };
        if let Some(last) = self.last_startup {
            if last.track == position.track && last.milliseconds >= position.milliseconds {
                return;
            }
        }
        if self.startup.len() < STARTUP_FRAMES {
            self.startup.push_back(MovieFrame {
                sequence: self.sequence,
                width,
                height,
                rgb565: self.video_pixels.clone(),
                position,
            });
            self.last_startup = Some(position);
        }
    }

    pub(crate) fn frame<'a>(&'a self, ram: &'a [u8]) -> Option<Frame<'a>> {
        let (start, len, width, height) = self.latched?;
        Some(Frame {
            sequence: self.sequence,
            width,
            height,
            rgb565: if self.video_valid {
                &self.video_pixels
            } else {
                &ram[start..start + len]
            },
            movie: self.movie,
        })
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn video_scanout_latches_pixels_and_keeps_distinct_startup_frames() {
        let mut lcd = Lcd::default();
        let mut memory = [0; 38];
        memory[32..].copy_from_slice(&[76, 76, 76, 76, 85, 255]);
        for (register, value) in [
            (0, 4),
            (0x14, 0),
            (0x5c, ram::BASE + 32),
            (0x60, ram::BASE + 36),
            (0x64, ram::BASE + 37),
            (0x68, 2 << 10 | 2),
            (0x6c, 2 << 10 | 2),
            (0x74, 1 << 10 | 1),
            (0xac, 4 << 10 | 4),
        ] {
            lcd.write(BASE + register, value, &memory, None);
        }
        let first = VideoPosition {
            track: 1,
            milliseconds: 0,
        };
        for position in [
            first,
            first,
            VideoPosition {
                milliseconds: 66,
                ..first
            },
        ] {
            lcd.write(BASE + 0xc8, 0x800, &memory, Some(position));
        }

        let latest = lcd.frame(&memory).unwrap();
        assert_eq!(latest.sequence, 3);
        assert_eq!(latest.movie.unwrap().milliseconds, 66);
        let (pixels, _) = latest.rgb565.as_chunks::<2>();
        let red = 0xf800u16.to_le_bytes();
        assert_eq!(
            pixels,
            &[
                [0, 0],
                [0, 0],
                [0, 0],
                [0, 0],
                [0, 0],
                red,
                red,
                [0, 0],
                [0, 0],
                red,
                red,
                [0, 0],
                [0, 0],
                [0, 0],
                [0, 0],
                [0, 0]
            ]
        );
        assert_eq!(lcd.take_startup().unwrap().sequence, 1);
        assert_eq!(lcd.take_startup().unwrap().sequence, 3);
        assert!(lcd.take_startup().is_none());
    }
}
