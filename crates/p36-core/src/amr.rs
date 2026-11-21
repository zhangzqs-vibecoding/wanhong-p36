use std::collections::VecDeque;

use amr_nb::{rfc4867, Decoder, Dtx, Encoder, Mode};

use crate::{audio::pcm_wave, ram};

const COMMAND: u16 = 0x410f;
pub(crate) const PAUSE_REGISTER: u16 = 0x43cc;
pub(crate) const FLUSH_ON_STOP: u32 = 0x31d5_3cf8;
const RING: usize = (0x31d5_3cfc - ram::BASE) as usize;
const RING_SIZE: usize = 0x10000;
const ENCODER_MODE: usize = (0x31d6_a432 - ram::BASE) as usize;
const RECORD_WINDOWS: [u16; 2] = [0x4802, 0x4822];
const FRAME_LENGTHS: [usize; 16] = [13, 14, 16, 18, 20, 21, 27, 32, 6, 0, 0, 0, 0, 0, 0, 1];
const SPEECH_MODES: [Mode; 8] = [
    Mode::MR475,
    Mode::MR515,
    Mode::MR59,
    Mode::MR67,
    Mode::MR74,
    Mode::MR795,
    Mode::MR102,
    Mode::MR122,
];
const FRAME_SAMPLES: usize = 160;
const BLOCK_SAMPLES: usize = FRAME_SAMPLES * 2;
const MAGIC: &[u8; 6] = b"#!AMR\n";

enum Codec {
    Recording(Box<Encoder>),
    Playback(Box<Decoder>),
}

#[derive(Default)]
pub(crate) struct Amr {
    codec: Option<Codec>,
    time: u64,
    deadline: u64,
    paused: bool,
    input: VecDeque<[i16; BLOCK_SAMPLES]>,
    encoded: [VecDeque<u8>; 2],
    pcm: Vec<u8>,
}

impl Amr {
    pub(crate) fn start(&mut self, record: bool, now: u64) {
        self.codec = Some(if record {
            Codec::Recording(Box::new(Encoder::new(Dtx::Off)))
        } else {
            Codec::Playback(Box::new(Decoder::new()))
        });
        self.time = now;
        self.deadline = now;
        self.paused = false;
    }

    pub(crate) fn stop(&mut self) -> Option<Vec<u8>> {
        self.codec = None;
        self.input.clear();
        for window in &mut self.encoded {
            window.clear();
        }
        self.take_wave()
    }

    pub(crate) fn recording(&self) -> bool {
        matches!(self.codec, Some(Codec::Recording(_)))
    }

    pub(crate) fn submit(&mut self, samples: &[i16]) -> bool {
        let Ok(block) = samples.try_into() else {
            return false;
        };
        if !self.recording() || self.input.len() == 8 {
            return false;
        }
        self.input.push_back(block);
        true
    }

    pub(crate) fn pause(&mut self, paused: bool) -> bool {
        if matches!(self.codec, Some(Codec::Playback(_))) {
            self.paused = paused;
            self.deadline = self.time;
            true
        } else {
            false
        }
    }

    pub(crate) fn advance(
        &mut self,
        now: Option<u64>,
        elapsed: u64,
        pending: bool,
        words: &mut [u16],
        ram: &[u8],
    ) -> bool {
        self.time = now.unwrap_or(self.time + elapsed);
        if pending || self.paused || self.time < self.deadline {
            return pending;
        }
        let interval = match self.codec.as_mut() {
            Some(Codec::Recording(encoder)) => {
                let samples = self.input.pop_front().unwrap_or([0; BLOCK_SAMPLES]);
                let mode = ram[ENCODER_MODE].min(7);
                for (window, frame) in self
                    .encoded
                    .iter_mut()
                    .zip(samples.as_chunks::<FRAME_SAMPLES>().0)
                {
                    let frame = encoder.encode(SPEECH_MODES[mode as usize], frame);
                    window.clear();
                    if let Ok(packet) = rfc4867::write_storage(&[frame]) {
                        window.extend(&packet[MAGIC.len()..]);
                    }
                }
                words[0x4aaf] = u16::from(mode);
                words[0x4ab0] = 0;
                words[0x4801] = 1;
                40_000
            }
            Some(Codec::Playback(_)) => 20_000,
            None => return false,
        };
        self.deadline = (self.deadline + interval).max(self.time);
        true
    }

    pub(crate) fn read(&mut self, selection: u16, pending: &mut bool) -> Option<u16> {
        if selection == COMMAND && *pending {
            *pending = false;
            return Some(if self.recording() { 6 } else { 1 });
        }
        if self.recording() {
            let window = RECORD_WINDOWS
                .iter()
                .position(|&address| address == selection)?;
            return Some(u16::from(self.encoded[window].pop_front().unwrap_or(0)));
        }
        None
    }

    pub(crate) fn decode_frame(&mut self, ram: &[u8]) -> Option<Vec<u8>> {
        let Some(Codec::Playback(decoder)) = &mut self.codec else {
            return None;
        };
        let offset = u32::from_le_bytes(ram[RING + RING_SIZE..][..4].try_into().unwrap()) as usize;
        if offset >= RING_SIZE {
            return None;
        }
        let header = ram[RING + offset];
        let length = FRAME_LENGTHS[((header >> 3) & 15) as usize];
        if length == 0 || header & 0x83 != 0 || header & 4 == 0 {
            return None;
        }
        let mut packet = [0; MAGIC.len() + 32];
        packet[..MAGIC.len()].copy_from_slice(MAGIC);
        let first = length.min(RING_SIZE - offset);
        packet[MAGIC.len()..][..first].copy_from_slice(&ram[RING + offset..][..first]);
        packet[MAGIC.len() + first..][..length - first]
            .copy_from_slice(&ram[RING..][..length - first]);
        let frame = rfc4867::read_storage(&packet[..MAGIC.len() + length])
            .ok()?
            .next()?
            .ok()?;
        let mut samples = [0; FRAME_SAMPLES];
        decoder.decode(frame.frame_type, frame.mode, &frame.bits, &mut samples);
        self.pcm
            .extend(samples.iter().flat_map(|sample| sample.to_le_bytes()));
        if self.pcm.len() >= 4 * FRAME_SAMPLES * 2 {
            self.take_wave()
        } else {
            None
        }
    }

    fn take_wave(&mut self) -> Option<Vec<u8>> {
        if self.pcm.is_empty() {
            return None;
        }
        let wave = pcm_wave(&self.pcm, 8000, 1, 2);
        self.pcm.clear();
        Some(wave)
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn records_mailbox_frames_and_decodes_across_the_ring_boundary() {
        let mut amr = Amr::default();
        let mut words = vec![0; 0x10000];
        let mut memory = vec![0; ENCODER_MODE + 1];
        memory[ENCODER_MODE] = 7;
        let samples = std::array::from_fn::<i16, BLOCK_SAMPLES, _>(|index| {
            (8000.0 * (index as f64 * 440.0 * std::f64::consts::TAU / 8000.0).sin()) as i16
        });

        amr.start(true, 0);
        assert!(amr.submit(&samples));
        let mut pending = amr.advance(Some(0), 0, false, &mut words, &memory);
        assert!(pending);
        assert_eq!(amr.read(COMMAND, &mut pending), Some(6));
        assert!(!pending);
        let frame = RECORD_WINDOWS.map(|window| {
            (0..32)
                .map(|_| amr.read(window, &mut pending).unwrap() as u8)
                .collect::<Vec<_>>()
        });
        assert!(frame.iter().all(|packet| packet[0] == 0x3c));
        amr.stop();

        amr.start(false, 0);
        let offset = RING_SIZE - 5;
        memory[RING + RING_SIZE..][..4].copy_from_slice(&(offset as u32).to_le_bytes());
        for packet in &frame {
            memory[RING + offset..][..5].copy_from_slice(&packet[..5]);
            memory[RING..][..27].copy_from_slice(&packet[5..]);
            assert!(amr.decode_frame(&memory).is_none());
        }
        let wave = amr.stop().unwrap();
        assert_eq!(wave.len(), 44 + BLOCK_SAMPLES * 2);
        assert!(wave[44..].iter().any(|&byte| byte != 0));
    }
}
