use std::collections::VecDeque;

use crate::ram;

const RING: u32 = 0x31d5_3cfc;
const RING_SIZE: usize = 65_536;
const BATCH_FRAMES: usize = 8;
const QUEUE_LIMIT: usize = 128;

#[derive(Clone, Copy, Debug, Default, PartialEq, Eq)]
pub enum Mp3State {
    Playing,
    Paused,
    Ended,
    #[default]
    Stopped,
}

pub struct Mp3Event {
    pub track: u32,
    pub state: Mp3State,
    pub bytes: Vec<u8>,
    pub movie_seconds: Option<f64>,
}

#[derive(Default)]
pub(crate) struct FileMp3 {
    track: u32,
    pub(crate) state: Mp3State,
    frames: usize,
    batch: Vec<u8>,
    output: VecDeque<Mp3Event>,
    movie: bool,
    movie_prefix: Vec<u8>,
    movie_seconds: Option<f64>,
}

impl FileMp3 {
    pub(crate) fn active(&self) -> bool {
        matches!(self.state, Mp3State::Playing | Mp3State::Paused)
    }

    pub(crate) fn movie_track(&self) -> Option<u32> {
        (self.movie && self.state != Mp3State::Stopped).then_some(self.track)
    }

    pub(crate) fn start(&mut self, movie: bool, prefix: Vec<u8>) {
        self.track = self.track.wrapping_add(1);
        self.frames = 0;
        self.batch.clear();
        self.movie = movie;
        self.movie_prefix = prefix;
        self.movie_seconds = movie.then_some(0.0);
        self.set_state(Mp3State::Playing);
    }

    pub(crate) fn pause(&mut self) {
        self.flush();
        self.set_state(Mp3State::Paused);
    }

    pub(crate) fn resume(&mut self) {
        self.set_state(Mp3State::Playing);
    }

    pub(crate) fn finish(&mut self, completed: bool) {
        if !self.active() {
            return;
        }
        if completed {
            self.flush();
            self.set_state(Mp3State::Ended);
        } else {
            self.batch.clear();
            self.set_state(Mp3State::Stopped);
        }
    }

    pub(crate) fn capture(&mut self, ram: &[u8], header: u32, length: usize, position: u32) {
        if self.state != Mp3State::Playing || !(5..=4096).contains(&length) {
            return;
        }
        let (Some(header), Some(position)) = (ram::offset(header, 4), ram::offset(position, 4))
        else {
            return;
        };
        let header = &ram[header..header + 4];
        if header[0] != 0xff || header[1] & 0xe0 != 0xe0 {
            return;
        }
        let offset = u32::from_le_bytes(ram[position..position + 4].try_into().unwrap()) as usize;
        if offset >= RING_SIZE {
            return;
        }
        let start = (RING - ram::BASE) as usize;
        let ring = &ram[start..start + RING_SIZE];
        let batch_start = self.batch.len();
        // At stream start the firmware may present a probe header while the
        // original header is still at the front of the ring.
        let ring_encoding = ring[(offset + 2) % RING_SIZE];
        let header_in_ring = self.frames == 0
            && ring[offset] == 0xff
            && ring[(offset + 1) % RING_SIZE] & 0xfe == header[1] & 0xfe
            && ring_encoding & 0xfc == header[2] & 0xfc;
        let payload = if header_in_ring {
            // The probe and the real Layer III frame can differ by one padding byte.
            length - usize::from(header[2] & 2 != 0) + usize::from(ring_encoding & 2 != 0)
        } else {
            self.batch.extend_from_slice(header);
            length - 4
        };
        let contiguous = payload.min(RING_SIZE - offset);
        self.batch
            .extend_from_slice(&ring[offset..offset + contiguous]);
        self.batch.extend_from_slice(&ring[..payload - contiguous]);
        if !self.movie_prefix.is_empty() {
            let packet = &self.batch[batch_start..];
            let prefix_end = self
                .movie_prefix
                .windows(packet.len())
                .position(|window| window == packet);
            match prefix_end {
                None if offset == 0 => {
                    self.batch.truncate(batch_start);
                    return;
                }
                Some(end) if end > 0 => {
                    self.batch.splice(
                        batch_start..batch_start,
                        self.movie_prefix[..end].iter().copied(),
                    );
                }
                _ => {}
            }
            self.movie_prefix.clear();
        }
        self.frames += 1;
        if self.frames.is_multiple_of(BATCH_FRAMES) {
            self.flush();
        }
    }

    pub(crate) fn take(&mut self) -> Option<Mp3Event> {
        self.output.pop_front()
    }

    fn flush(&mut self) {
        if !self.batch.is_empty() {
            let bytes = std::mem::take(&mut self.batch);
            self.publish(bytes);
        }
    }

    fn set_state(&mut self, state: Mp3State) {
        self.state = state;
        self.publish(Vec::new());
    }

    fn publish(&mut self, bytes: Vec<u8>) {
        let movie_seconds = self.movie_seconds;
        if !bytes.is_empty() {
            self.movie_seconds = movie_seconds
                .and_then(|position| mp3_duration(&bytes).map(|duration| position + duration));
        }
        if self.output.len() == QUEUE_LIMIT {
            self.output.pop_front();
        }
        self.output.push_back(Mp3Event {
            track: self.track,
            state: self.state,
            bytes,
            movie_seconds,
        });
    }
}

fn mp3_duration(mut bytes: &[u8]) -> Option<f64> {
    const RATES: [u32; 3] = [44_100, 48_000, 32_000];
    const BITRATES_V1: [u32; 15] = [
        0, 32, 40, 48, 56, 64, 80, 96, 112, 128, 160, 192, 224, 256, 320,
    ];
    const BITRATES_V2: [u32; 15] = [0, 8, 16, 24, 32, 40, 48, 56, 64, 80, 96, 112, 128, 144, 160];
    let mut duration = 0.0;
    while !bytes.is_empty() {
        let [0xff, flags, encoding, _] = *bytes.first_chunk::<4>()? else {
            return None;
        };
        if flags & 0xe6 != 0xe2 {
            return None;
        }
        let rate = *RATES.get(((encoding >> 2) & 3) as usize)?;
        let bitrate = (encoding >> 4) as usize;
        let (rate, samples, bitrate) = match (flags >> 3) & 3 {
            3 => (rate, 1152, *BITRATES_V1.get(bitrate)?),
            2 => (rate / 2, 576, *BITRATES_V2.get(bitrate)?),
            0 => (rate / 4, 576, *BITRATES_V2.get(bitrate)?),
            _ => return None,
        };
        if bitrate == 0 {
            return None;
        }
        let length = samples * bitrate * 125 / rate + u32::from(encoding & 2 != 0);
        duration += f64::from(samples) / f64::from(rate);
        bytes = bytes.get(length as usize..)?;
    }
    Some(duration)
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn movie_audio_timestamps_follow_complete_mpeg_frames() {
        let mut data = vec![0; 156 + 157];
        data[..4].copy_from_slice(&[0xff, 0xf3, 0x60, 0x00]);
        data[156..160].copy_from_slice(&[0xff, 0xf3, 0x62, 0x00]);
        let expected = 2.0 * 576.0 / 22_050.0;
        assert_eq!(mp3_duration(&data), Some(expected));
        assert_eq!(mp3_duration(&data[..data.len() - 1]), None);
    }

    #[test]
    fn restores_wrapped_frames_and_flushes_before_pause() {
        let mut memory = vec![0; ram::SIZE];
        let header = [0xff, 0xfb, 0x90, 0x64];
        memory[..4].copy_from_slice(&[0xff, 0xfb, 0x92, 0x64]);
        let start = (RING - ram::BASE) as usize;
        memory[start + RING_SIZE - 2..start + RING_SIZE].copy_from_slice(&header[..2]);
        memory[start..start + 5].copy_from_slice(&[0x90, 0x64, 1, 2, 3]);
        let offset = ram::BASE + 4;
        memory[4..8].copy_from_slice(&((RING_SIZE - 2) as u32).to_le_bytes());

        let mut mp3 = FileMp3::default();
        mp3.start(false, Vec::new());
        assert_eq!(mp3.take().unwrap().state, Mp3State::Playing);
        mp3.capture(&memory, ram::BASE, 8, offset);

        memory[..4].copy_from_slice(&header);
        memory[4..8].copy_from_slice(&((RING_SIZE - 1) as u32).to_le_bytes());
        memory[start + RING_SIZE - 1] = 4;
        memory[start..start + 2].copy_from_slice(&[5, 6]);
        mp3.capture(&memory, ram::BASE, 7, offset);
        mp3.pause();

        let frames = mp3.take().unwrap();
        assert_eq!(frames.track, 1);
        assert_eq!(frames.state, Mp3State::Playing);
        assert_eq!(
            frames.bytes,
            [0xff, 0xfb, 0x90, 0x64, 1, 2, 3, 0xff, 0xfb, 0x90, 0x64, 4, 5, 6]
        );
        assert_eq!(mp3.take().unwrap().state, Mp3State::Paused);
        assert!(mp3.take().is_none());
    }
}
