use tetanes_core::{
    apu::{Apu, Channel::*},
    common::NesRegion,
};

use crate::{audio::pcm_wave, ram};

const PRG_BANKS: usize = (0x31e1_8ebc - ram::BASE) as usize;
const RATE: u32 = 32_000;
const CPU_CLOCK: u64 = 1_789_773;
const CHUNK_SAMPLES: usize = 2048;

pub(crate) struct NesAudio {
    apu: Apu,
    last_us: Option<u64>,
    fractional_cycles: u64,
    paused: bool,
}

impl NesAudio {
    pub(crate) fn new(now: Option<u64>) -> Self {
        let mut apu = Apu::new(NesRegion::Ntsc);
        apu.set_sample_rate(RATE as f32);
        apu.clock_sync();
        Self {
            apu,
            last_us: now,
            fractional_cycles: 0,
            paused: false,
        }
    }

    pub(crate) fn set_paused(&mut self, paused: bool) {
        self.paused = paused;
        if paused {
            self.apu.audio_samples.clear();
        }
    }

    pub(crate) fn advance(&mut self, now: u64, ram: &[u8]) {
        let Some(previous) = self.last_us.replace(now) else {
            return;
        };
        if self.paused {
            return;
        }
        // Keep playback responsive after a background tab is resumed.
        let elapsed = now.saturating_sub(previous).min(128_000);
        let cycles = elapsed * CPU_CLOCK + self.fractional_cycles;
        self.fractional_cycles = cycles % 1_000_000;
        for _ in 0..cycles / 1_000_000 {
            self.apu.clock_lazy();
            if self.apu.dma_pending() {
                let address = self.apu.dmc.dma_addr();
                self.apu.clear_dma_pending();
                self.apu.dmc.load_buffer(read_prg(ram, address));
            }
        }
    }

    pub(crate) fn write(&mut self, register: u32, value: u8) {
        let apu = &mut self.apu;
        match register {
            0x4000 => apu.write_ctrl(Pulse1, value),
            0x4001 => apu.write_sweep(Pulse1, value),
            0x4002 => apu.write_timer_lo(Pulse1, value),
            0x4003 => apu.write_timer_hi(Pulse1, value),
            0x4004 => apu.write_ctrl(Pulse2, value),
            0x4005 => apu.write_sweep(Pulse2, value),
            0x4006 => apu.write_timer_lo(Pulse2, value),
            0x4007 => apu.write_timer_hi(Pulse2, value),
            0x4008 => apu.write_linear_counter(value),
            0x400a => apu.write_timer_lo(Triangle, value),
            0x400b => apu.write_timer_hi(Triangle, value),
            0x400c => apu.write_ctrl(Noise, value),
            0x400e => apu.write_timer_lo(Noise, value),
            0x400f => apu.write_length(Noise, value),
            0x4010 => apu.write_timer_lo(Dmc, value),
            0x4011 => apu.write_dmc_output(value),
            0x4012 => apu.write_dmc_addr(value),
            0x4013 => apu.write_length(Dmc, value),
            0x4015 => apu.write_status(value),
            0x4017 => apu.write_frame_counter(value),
            _ => {}
        }
    }

    pub(crate) fn take(&mut self) -> Option<Vec<u8>> {
        if self.apu.audio_samples.len() < CHUNK_SAMPLES {
            return None;
        }
        let pcm: Vec<_> = self
            .apu
            .audio_samples
            .drain(..CHUNK_SAMPLES)
            .flat_map(|sample| ((sample * 32768.0) as i16).to_le_bytes())
            .collect();
        Some(pcm_wave(&pcm, RATE, 1, 2))
    }
}

fn read_prg(ram: &[u8], address: u16) -> u8 {
    let offset = PRG_BANKS + usize::from(address >> 11) * 4;
    let bank = u32::from_le_bytes(ram[offset..offset + 4].try_into().unwrap());
    bank.checked_add(u32::from(address & 0x7ff))
        .and_then(|location| ram::offset(location, 1))
        .map_or(0, |offset| ram[offset])
}
