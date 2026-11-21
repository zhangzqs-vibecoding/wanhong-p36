use std::collections::VecDeque;

use crate::amr::{Amr, PAUSE_REGISTER};
use crate::mp3::{FileMp3, Mp3State};

const DSP_CLOCK: u32 = 96_000_000;
const WAV_WORDS: u16 = 0x800;
const WAV_COMMAND: u16 = 0x4001;
const WAV_RESULT: u16 = 0x400b;
const WAV_WORD_COUNT: u16 = 0x400c;
const MP3_COMMAND: u16 = 0x4010;
const MP3_PAUSE: u16 = 0x4003;
const QUEUE_LIMIT: usize = 128;

#[derive(Clone, Copy, PartialEq, Eq)]
enum Mode {
    Amr,
    Mp3,
    Pcm,
}

pub(crate) struct Dsp {
    selection: u16,
    words: Box<[u16]>,
    mode: Option<Mode>,
    pub(crate) pending: bool,
    period: u32,
    remaining: u32,
    time_us: Option<u64>,
    deadline_us: u64,
    volume: u8,
    rate: u32,
    channels: u16,
    width: u16,
    pcm: Vec<u8>,
    pcm_end: bool,
    output: VecDeque<Vec<u8>>,
    pub(crate) amr: Amr,
    pub(crate) file_mp3: FileMp3,
}

impl Default for Dsp {
    fn default() -> Self {
        Self {
            selection: 0,
            words: vec![0; 65_536].into_boxed_slice(),
            mode: None,
            pending: false,
            period: 0,
            remaining: 0,
            time_us: None,
            deadline_us: 0,
            volume: 20,
            rate: 0,
            channels: 0,
            width: 0,
            pcm: Vec::new(),
            pcm_end: false,
            output: VecDeque::new(),
            amr: Amr::default(),
            file_mp3: FileMp3::default(),
        }
    }
}

impl Dsp {
    pub(crate) fn select(&mut self, value: u16) {
        self.selection = value;
    }

    pub(crate) fn read(&mut self) -> u16 {
        match (self.mode, self.selection) {
            (Some(Mode::Amr), selection) => self
                .amr
                .read(selection, &mut self.pending)
                .unwrap_or(self.words[selection as usize]),
            (Some(Mode::Pcm), WAV_WORD_COUNT) => WAV_WORDS,
            (Some(Mode::Pcm), WAV_COMMAND) if self.pending => {
                self.pending = false;
                if self.pcm_end {
                    0x0f
                } else {
                    0x0e
                }
            }
            (Some(Mode::Mp3), MP3_COMMAND) if self.pending => {
                self.pending = false;
                5
            }
            _ => self.words[self.selection as usize],
        }
    }

    pub(crate) fn write(&mut self, value: u16, pc: u32) {
        self.words[self.selection as usize] = value;
        if self.mode == Some(Mode::Amr)
            && self.selection == PAUSE_REGISTER
            && self.amr.pause(value != 0)
        {
            self.pending = false;
        }
        if self.mode == Some(Mode::Mp3) && self.selection == MP3_PAUSE {
            match (value, self.file_mp3.state) {
                (1, Mp3State::Playing) => {
                    self.pending = false;
                    self.file_mp3.pause();
                }
                (0, Mp3State::Paused) => {
                    self.file_mp3.resume();
                    self.deadline_us = self.time_us.unwrap_or_default();
                }
                _ => {}
            }
        }
        if self.mode == Some(Mode::Pcm) {
            if matches!(pc, 0x3004_26a4 | 0x3005_4fa8) {
                self.pcm.extend_from_slice(&value.to_le_bytes());
            } else if self.selection == WAV_RESULT {
                if value == 0xffff {
                    self.pcm_end = true;
                } else if !self.pcm.is_empty() {
                    let wave = pcm_wave(&self.pcm, self.rate, self.channels, self.width);
                    self.pcm.clear();
                    self.publish(wave);
                }
            }
        }
    }

    pub(crate) fn start_mp3(&mut self, buffered: Option<Vec<u8>>, movie: bool) {
        let rate = u32::from(self.words[0x4036]);
        let samples = if rate >= 32_000 { 1152 } else { 576 };
        let period = if rate == 0 {
            2_508_000
        } else {
            (u64::from(DSP_CLOCK) * samples / u64::from(rate)) as u32
        };
        self.start(Mode::Mp3, period);
        if movie {
            self.start_file_mp3(true, buffered.unwrap_or_default());
        } else if let Some(bytes) = buffered {
            self.file_mp3.finish(false);
            self.publish(bytes);
        } else {
            self.start_file_mp3(false, Vec::new());
        }
    }

    pub(crate) fn seek_mp3(&mut self) {
        self.start_file_mp3(self.file_mp3.movie_track().is_some(), Vec::new());
    }

    fn start_file_mp3(&mut self, movie: bool, prefix: Vec<u8>) {
        self.file_mp3.start(movie, prefix);
        self.pending = false;
        self.deadline_us = self.time_us.unwrap_or_default();
    }

    pub(crate) fn finish_mp3(&mut self, completed: bool) {
        self.file_mp3.finish(completed);
        self.stop(Mode::Mp3);
    }

    pub(crate) fn start_pcm(&mut self) {
        let channels = self.words[0x4007];
        let rate = u32::from(self.words[0x4008]);
        let bits = self.words[0x4009];
        if self.words[0x4006] != 1
            || rate == 0
            || !matches!(channels, 1 | 2)
            || !matches!(bits, 8 | 16)
        {
            return;
        }
        self.channels = channels;
        self.rate = rate;
        self.width = bits / 8;
        self.pcm.clear();
        self.pcm_end = false;
        let bytes_per_second = rate * u32::from(channels * self.width);
        let period =
            (u64::from(DSP_CLOCK) * u64::from(WAV_WORDS) * 2 / u64::from(bytes_per_second)) as u32;
        self.start(Mode::Pcm, period);
        self.deadline_us = self.time_us.unwrap_or_default();
    }

    fn start(&mut self, mode: Mode, period: u32) {
        self.stop_amr(self.amr.recording());
        self.mode = Some(mode);
        self.pending = false;
        self.period = period;
        self.remaining = period;
    }

    pub(crate) fn stop_pcm(&mut self) {
        self.stop(Mode::Pcm);
    }

    pub(crate) fn stop_mp3(&mut self) {
        self.file_mp3.finish(false);
    }

    fn stop(&mut self, mode: Mode) {
        if self.mode == Some(mode) {
            self.mode = None;
            self.pending = false;
        }
    }

    pub(crate) fn start_amr(&mut self, record: bool) {
        self.stop_amr(self.amr.recording());
        self.amr.start(record, self.time_us.unwrap_or_default());
        self.mode = Some(Mode::Amr);
        self.pending = false;
    }

    pub(crate) fn stop_amr(&mut self, record: bool) {
        if self.mode == Some(Mode::Amr) && self.amr.recording() == record {
            if let Some(wave) = self.amr.stop() {
                self.publish(wave);
            }
            self.stop(Mode::Amr);
        }
    }

    pub(crate) fn advance(&mut self, cycles: u32, ram: &[u8]) {
        if self.mode == Some(Mode::Amr) {
            self.pending = self.amr.advance(
                self.time_us,
                u64::from(cycles) * 1_000_000 / u64::from(DSP_CLOCK),
                self.pending,
                &mut self.words,
                ram,
            );
            return;
        }
        if self.mode.is_none()
            || (self.mode == Some(Mode::Mp3) && self.file_mp3.state == Mp3State::Paused)
        {
            return;
        }
        let realtime_mp3 = self.mode == Some(Mode::Mp3) && self.file_mp3.state == Mp3State::Playing;
        if let Some(now) = self.time_us.filter(|_| realtime_mp3 || self.realtime_pcm()) {
            if !self.pending && now >= self.deadline_us {
                self.pending = true;
                let duration = u64::from(self.period) * 1_000_000 / u64::from(DSP_CLOCK);
                self.deadline_us = self.deadline_us.saturating_add(duration);
            }
        } else if cycles >= self.remaining {
            self.pending = true;
            self.remaining = self.period - (cycles - self.remaining) % self.period;
        } else {
            self.remaining -= cycles;
        }
    }

    fn realtime_pcm(&self) -> bool {
        self.mode == Some(Mode::Pcm) && self.rate >= 32_000
    }

    pub(crate) fn set_time(&mut self, time_us: u64) {
        self.time_us = Some(time_us);
    }

    pub(crate) fn drain_tts(&mut self) {
        if self.mode == Some(Mode::Pcm) && !self.realtime_pcm() {
            self.pending = true;
            self.remaining = self.period;
        }
    }

    pub(crate) fn set_volume(&mut self, level: u32) {
        if level <= 20 {
            self.volume = level as u8;
        }
    }

    pub(crate) fn volume(&self) -> u8 {
        self.volume
    }

    pub(crate) fn publish(&mut self, clip: Vec<u8>) {
        if self.output.len() == QUEUE_LIMIT {
            self.output.pop_front();
        }
        self.output.push_back(clip);
    }

    pub(crate) fn take(&mut self) -> Option<Vec<u8>> {
        self.output.pop_front()
    }
}

pub(crate) fn pcm_wave(pcm: &[u8], rate: u32, channels: u16, width: u16) -> Vec<u8> {
    let length = pcm.len() as u32;
    let alignment = channels * width;
    let mut wave = Vec::with_capacity(44 + pcm.len());
    wave.extend_from_slice(b"RIFF");
    wave.extend_from_slice(&(36 + length).to_le_bytes());
    wave.extend_from_slice(b"WAVEfmt \x10\0\0\0\x01\0");
    wave.extend_from_slice(&channels.to_le_bytes());
    wave.extend_from_slice(&rate.to_le_bytes());
    wave.extend_from_slice(&(rate * u32::from(alignment)).to_le_bytes());
    wave.extend_from_slice(&alignment.to_le_bytes());
    wave.extend_from_slice(&(8 * width).to_le_bytes());
    wave.extend_from_slice(b"data");
    wave.extend_from_slice(&length.to_le_bytes());
    wave.extend_from_slice(pcm);
    wave
}
