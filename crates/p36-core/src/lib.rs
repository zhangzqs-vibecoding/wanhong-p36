//! Platform-independent P36 machine and devices.

mod amr;
mod audio;
mod bus;
mod card_firmware;
mod input;
mod jpeg;
mod lcd;
mod mp3;
mod nes;
mod nes_audio;
mod ram;
mod sd;
mod timers;
mod video;

use armv4t_emu::{reg, Cpu, Memory, Mode};
use bus::Bus;
use timers::IRQ_ENABLE;

pub use card_firmware::read_card_firmware;
pub use lcd::{Frame, MovieFrame, VideoPosition};
pub use mp3::{Mp3Event, Mp3State};
pub use sd::{BlockDevice, SECTOR_SIZE};

const IRQ_CHECK_INTERVAL: u32 = 120_000;
const MEDIA_IRQ_INTERVAL: u32 = 30_000;
const TIMER_CYCLES_PER_US: u64 = 96;
const SCHEDULER_IDLE: std::ops::Range<u32> = 0x3021_6de8..0x3021_6e00;
const READY_TASK: u32 = 0x305b_4f8c;
const READY_HISR: u32 = 0x305b_4f68;
// The firmware enters this delay loop after driving the power cutoff GPIO.
const POWER_CUTOFF: u32 = 0x3003_f140;

pub struct Machine<D> {
    cpu: Cpu,
    bus: Bus<D>,
    cpu_control: u32,
    timer_cycles: u32,
    play_buffer: Option<(u32, usize, bool)>,
    nes_audio: Option<nes_audio::NesAudio>,
    host_time_us: Option<u64>,
    media_timer_last_us: Option<u64>,
    trace: bool,
}

impl<D: BlockDevice> Machine<D> {
    pub fn new(firmware: &[u8], image: D) -> Result<Self, String> {
        let mut cpu = Cpu::new();
        cpu.reg_set(Mode::Supervisor, reg::SP, 0x31ff_0000);
        cpu.reg_set(Mode::Supervisor, reg::PC, ram::BASE);
        Ok(Self {
            cpu,
            bus: Bus::new(firmware, image)?,
            cpu_control: 0,
            timer_cycles: 0,
            play_buffer: None,
            nes_audio: None,
            host_time_us: None,
            media_timer_last_us: None,
            trace: false,
        })
    }

    pub fn set_trace(&mut self, trace: bool) {
        self.trace = trace;
    }

    pub fn pc(&self) -> u32 {
        self.cpu.reg_get(Mode::User, reg::PC)
    }

    pub fn powered_off(&self) -> bool {
        self.pc() == POWER_CUTOFF
    }

    pub fn frame(&self) -> Option<Frame<'_>> {
        self.bus.lcd.frame(&self.bus.ram)
    }

    pub fn take_movie_startup_frame(&mut self) -> Option<MovieFrame> {
        self.bus.lcd.take_startup()
    }

    pub fn set_key(&mut self, code: u8, down: bool) -> bool {
        self.bus.keypad.set(code, down)
    }

    pub fn text_input_ready(&self) -> bool {
        self.media_time().is_some() || SCHEDULER_IDLE.contains(&self.pc())
    }

    pub fn nes_keys(&self) -> Option<[u8; 16]> {
        nes::keys(&self.bus.ram)
    }

    pub fn touch(&mut self, x: u16, y: u16, phase: u8) -> bool {
        if x >= 800 || y >= 480 || !matches!(phase, 1 | 2 | 5) {
            return false;
        }
        self.bus.touch.contact(x, y, phase);
        true
    }

    pub fn take_audio(&mut self) -> Option<Vec<u8>> {
        self.bus
            .dsp
            .take()
            .or_else(|| self.nes_audio.as_mut()?.take())
    }

    pub fn take_mp3_event(&mut self) -> Option<Mp3Event> {
        self.bus.dsp.file_mp3.take()
    }

    pub fn recording(&self) -> bool {
        self.bus.dsp.amr.recording()
    }

    pub fn submit_microphone(&mut self, samples: &[i16]) -> bool {
        self.bus.dsp.amr.submit(samples)
    }

    pub fn set_audio_time(&mut self, time_us: u64) {
        self.host_time_us = Some(time_us);
        self.bus.dsp.set_time(time_us);
        if let Some(audio) = &mut self.nes_audio {
            audio.advance(time_us, &self.bus.ram);
        }
    }

    pub fn volume(&self) -> u8 {
        self.bus.dsp.volume()
    }

    /// Reapply a sector from a saved overlay before starting the machine.
    pub fn restore_sd_sector(&mut self, sector: u32, bytes: [u8; SECTOR_SIZE]) -> bool {
        self.bus.sd.restore_sector(sector, bytes)
    }

    /// Drain changed sectors for host persistence while retaining the in-memory overlay.
    pub fn take_sd_writes(&mut self) -> Vec<(u32, [u8; SECTOR_SIZE])> {
        self.bus.sd.take_writes()
    }

    pub fn step(&mut self, mut instructions: u32) -> Result<(), String> {
        while instructions > 0 {
            if self.timer_cycles == 0 {
                if (0x3007_9350..0x3007_9360).contains(&self.pc())
                    || matches!(
                        self.cpu.reg_get(self.cpu.mode(), reg::LR),
                        0x3007_9354 | 0x3007_9358
                    )
                {
                    self.bus.dsp.drain_tts();
                }
                self.deliver_irq();
            }
            let pc = self.pc();
            if pc == POWER_CUTOFF {
                break;
            }
            if SCHEDULER_IDLE.contains(&pc)
                && self.bus.r32(READY_TASK) == 0
                && self.bus.r32(READY_HISR) == 0
            {
                // With both ready queues empty, only the next device interrupt
                // changes what the scheduler sees in this polling loop.
                let skipped = instructions.min(self.irq_interval() - self.timer_cycles);
                self.advance_timing(skipped);
                instructions -= skipped;
                continue;
            }
            if (matches!(
                pc,
                0x3001_a69c
                    | 0x3001_dae0
                    | 0x3001_dea0
                    | 0x3004_bbdc
                    | 0x3004_bf88
                    | 0x3004_c000
                    | 0x3004_d080
                    | 0x3004_d394
                    | 0x3004_fb54
                    | 0x3004_fd94
                    | 0x3005_0334
                    | 0x3005_1570
                    | 0x3005_18f8
                    | 0x3005_22d4
                    | 0x3005_4040
                    | 0x3005_3c68
                    | 0x3006_9d98
                    | 0x3006_a330
                    | 0x3006_a384
                    | 0x3006_a3b4
                    | 0x3009_9910
                    | 0x3003_74bc
                    | 0x301b_abd4
                    | 0x3011_f92c
                    | 0x300b_14e8
                    | 0x3007_a4d0
                    | 0x3007_6a04
                    | 0x3007_6a98
                    | 0x3010_64dc
                    | 0x3010_663c
            ) || self.trace
                && matches!(pc, 0x3001_03ac | 0x3001_0820 | 0x3002_5bd8 | 0x3003_9bf0))
                && self.handle_call(pc)
            {
                self.advance_timing(1);
                instructions -= 1;
                continue;
            }
            let mode = self.cpu.mode();
            let thumb = self.cpu.thumb_mode();
            let handled = if thumb {
                let half = self.bus.r16(pc);
                match half {
                    0xdfab => {
                        self.cpu.reg_set(mode, reg::PC, pc + 2);
                        true
                    }
                    _ if half & 0xf800 == 0xe800 => {
                        let target = self
                            .cpu
                            .reg_get(mode, reg::LR)
                            .wrapping_add(u32::from(half & 0x7ff) * 2);
                        self.branch_exchange(target & !3, Some((pc + 2) | 1));
                        true
                    }
                    _ if half & 0xff87 == 0x4780 => {
                        let target = self.cpu.reg_get(mode, ((half >> 3) & 15) as u8);
                        self.branch_exchange(target, Some((pc + 2) | 1));
                        true
                    }
                    _ => false,
                }
            } else {
                let word = self.bus.r32(pc);
                if word & 0xfe00_0000 == 0xfa00_0000 {
                    let displacement = ((word << 8) as i32 >> 6) as u32;
                    let target = (pc + 8).wrapping_add(displacement) | ((word >> 23) & 2);
                    self.branch_exchange(target | 1, Some(pc + 4));
                    true
                } else if word & 0x0fff_ffd0 == 0x012f_ff10 {
                    let cpsr = self.cpu.reg_get(mode, reg::CPSR);
                    if condition_passes(word >> 28, cpsr) {
                        let target = self.cpu.reg_get(mode, (word & 15) as u8);
                        self.branch_exchange(target, (word & 0x20 != 0).then_some(pc + 4));
                    } else {
                        self.cpu.reg_set(mode, reg::PC, pc + 4);
                    }
                    true
                } else if word & 0x0f00_0f10 == 0x0e00_0f10 {
                    self.access_cp15(pc, word);
                    true
                } else if word & 0x0fff_0ff0 == 0x016f_0f10 || word & 0x0f90_0090 == 0x0100_0080 {
                    self.execute_dsp_instruction(pc, word);
                    true
                } else {
                    false
                }
            };
            self.bus.pc = pc;
            if !handled && !self.cpu.step(&mut self.bus) {
                return Err(format!(
                    "undefined {} instruction at {pc:08x}: {:08x}",
                    if thumb { "Thumb" } else { "ARM" },
                    self.bus.r32(pc & !3),
                ));
            }
            if !thumb && !self.cpu.thumb_mode() && self.pc() & 1 != 0 {
                self.branch_exchange(self.pc(), None);
            }
            if let Some(error) = self.bus.sd.error.take() {
                return Err(error);
            }
            self.advance_timing(1);
            instructions -= 1;
        }
        Ok(())
    }

    fn advance_timing(&mut self, cycles: u32) {
        self.timer_cycles += cycles;
        if self.timer_cycles >= MEDIA_IRQ_INTERVAL && self.timer_cycles >= self.irq_interval() {
            let elapsed = self.timer_cycles;
            let timer_cycles = if let Some(now) = self.media_time() {
                self.media_timer_last_us
                    .replace(now)
                    .map_or(elapsed, |last| {
                        (now.saturating_sub(last).saturating_mul(TIMER_CYCLES_PER_US))
                            .min(u64::from(u32::MAX)) as u32
                    })
            } else {
                self.media_timer_last_us = None;
                elapsed
            };
            self.bus.timers.advance(timer_cycles);
            self.bus.dsp.advance(elapsed, &self.bus.ram);
            self.timer_cycles = 0;
        }
    }

    fn media_time(&self) -> Option<u64> {
        self.host_time_us
            .filter(|_| self.bus.lcd.video_enabled() || nes::active(&self.bus.ram))
    }

    fn irq_interval(&self) -> u32 {
        if self.media_time().is_some() {
            MEDIA_IRQ_INTERVAL
        } else {
            IRQ_CHECK_INTERVAL
        }
    }

    fn deliver_irq(&mut self) {
        if self.cpu.irq_enable() && self.bus.pending_irqs() & self.bus.r32(IRQ_ENABLE) != 0 {
            let cpsr = self.cpu.reg_get(Mode::User, reg::CPSR);
            self.cpu.reg_set(Mode::Irq, reg::LR, self.pc() + 4);
            self.cpu.reg_set(Mode::Irq, reg::SPSR, cpsr);
            self.cpu
                .reg_set(Mode::User, reg::CPSR, (cpsr & !0x3f) | 0x92);
            self.cpu.reg_set(Mode::Irq, reg::PC, 0x18);
        }
    }

    fn access_cp15(&mut self, pc: u32, word: u32) {
        let mode = self.cpu.mode();
        let cpsr = self.cpu.reg_get(mode, reg::CPSR);
        if condition_passes(word >> 28, cpsr) {
            let register = ((word >> 12) & 15) as u8;
            let control = word & 0x00ef_00ef == 0x0001_0000;
            if word & (1 << 20) != 0 {
                let value = if control { self.cpu_control } else { 0 };
                if register == reg::PC {
                    self.cpu.reg_set(
                        mode,
                        reg::CPSR,
                        (cpsr & 0x0fff_ffff) | (value & 0xf000_0000),
                    );
                } else {
                    self.cpu.reg_set(mode, register, value);
                }
            } else if control {
                self.cpu_control = self.cpu.reg_get(mode, register) & !1;
            }
        }
        self.cpu.reg_set(mode, reg::PC, pc + 4);
    }

    fn branch_exchange(&mut self, target: u32, link: Option<u32>) {
        let mode = self.cpu.mode();
        let cpsr = self.cpu.reg_get(mode, reg::CPSR);
        let thumb = target & 1;
        if let Some(link) = link {
            self.cpu.reg_set(mode, reg::LR, link);
        }
        self.cpu
            .reg_set(mode, reg::CPSR, (cpsr & !0x20) | (thumb << 5));
        self.cpu
            .reg_set(mode, reg::PC, target & if thumb != 0 { !1 } else { !3 });
    }

    fn execute_dsp_instruction(&mut self, pc: u32, word: u32) {
        let mode = self.cpu.mode();
        let cpsr = self.cpu.reg_get(mode, reg::CPSR);
        self.cpu.reg_set(mode, reg::PC, pc + 4);
        if !condition_passes(word >> 28, cpsr) {
            return;
        }
        let rm = (word & 15) as u8;
        let rd_low = ((word >> 12) & 15) as u8;
        let left = self.cpu.reg_get(mode, rm);
        if word & 0x0fff_0ff0 == 0x016f_0f10 {
            self.cpu.reg_set(mode, rd_low, left.leading_zeros());
            return;
        }

        let rd_high = ((word >> 16) & 15) as u8;
        let right = self.cpu.reg_get(mode, ((word >> 8) & 15) as u8);
        let op = (word >> 21) & 3;
        let half = |value: u32, high| i32::from((value >> if high { 16 } else { 0 }) as i16);
        let right = half(right, word & 0x40 != 0);
        let product = if op == 1 {
            ((i64::from(left as i32) * i64::from(right)) >> 16) as i32
        } else {
            half(left, word & 0x20 != 0) * right
        };
        match op {
            0 | 1 if op == 0 || word & 0x20 == 0 => {
                let (sum, overflow) =
                    product.overflowing_add(self.cpu.reg_get(mode, rd_low) as i32);
                self.cpu.reg_set(mode, rd_high, sum as u32);
                if overflow {
                    self.cpu.reg_set(mode, reg::CPSR, cpsr | (1 << 27));
                }
            }
            2 => {
                let low = u64::from(self.cpu.reg_get(mode, rd_low));
                let high = u64::from(self.cpu.reg_get(mode, rd_high));
                let sum = (high << 32 | low).wrapping_add(i64::from(product) as u64);
                self.cpu.reg_set(mode, rd_low, sum as u32);
                self.cpu.reg_set(mode, rd_high, (sum >> 32) as u32);
            }
            _ => self.cpu.reg_set(mode, rd_high, product as u32),
        }
    }

    fn handle_call(&mut self, pc: u32) -> bool {
        let mode = self.cpu.mode();
        let r = |register| self.cpu.reg_get(mode, register);
        match pc {
            0x3009_9910 => self.nes_audio = Some(nes_audio::NesAudio::new(self.host_time_us)),
            0x3001_dea0 => {
                if let Some(audio) = &mut self.nes_audio {
                    audio.write(r(0), r(1) as u8);
                }
            }
            0x3006_9d98 => {
                // The NES $4016 strobe resets both controller shift positions.
                // This firmware's $4016 handler only resets the first.
                if r(0) == 0x4016 && r(1) & 1 == 0 && self.bus.r32(0x31db_b3ec) & 1 != 0 {
                    self.bus.w32(0x31db_b3f8, 0);
                }
            }
            0x3006_a330 | 0x3006_a384 => {
                if let Some(audio) = &mut self.nes_audio {
                    audio.set_paused(pc == 0x3006_a330);
                }
            }
            0x3006_a3b4 => self.nes_audio = None,
            0x3001_a69c => {
                self.play_buffer = Some((r(0), r(1) as usize, r(reg::LR) == 0x3011_f814));
            }
            0x3004_bbdc => {
                let buffer = self.play_buffer.take();
                let movie = buffer.is_some_and(|(_, _, movie)| movie);
                let buffered = buffer
                    .and_then(|(address, len, _)| self.bus.bytes_at(address, len))
                    .filter(|data| {
                        data.starts_with(b"ID3")
                            || matches!(data, [0xff, second, ..] if second & 0xe0 == 0xe0)
                    })
                    .map(<[u8]>::to_vec);
                self.bus.dsp.start_mp3(buffered, movie);
            }
            0x3004_bf88 => self.bus.dsp.finish_mp3(r(0) == 0),
            0x3004_c000 => self.bus.dsp.stop_mp3(),
            0x3004_d080 if self.bus.dsp.file_mp3.active() => self.bus.dsp.seek_mp3(),
            0x3004_d080 => {}
            0x3004_d394 => self
                .bus
                .dsp
                .file_mp3
                .capture(&self.bus.ram, r(2), r(3) as usize, r(4)),
            0x3005_18f8 => self.bus.dsp.start_amr(true),
            0x3005_1570 if self.recording() => self.bus.w8(amr::FLUSH_ON_STOP, 1),
            0x3005_1570 => {}
            0x3001_dae0 => self.bus.dsp.stop_amr(true),
            0x3004_fb54 | 0x3005_22d4 => self.bus.dsp.start_amr(false),
            0x3005_0334 => {
                if let Some(wave) = self.bus.dsp.amr.decode_frame(&self.bus.ram) {
                    self.bus.dsp.publish(wave);
                }
            }
            0x3004_fd94 => self.bus.dsp.stop_amr(false),
            0x3005_4040 => self.bus.dsp.start_pcm(),
            0x3005_3c68 => self.bus.dsp.stop_pcm(),
            0x3003_74bc => self.bus.dsp.set_volume(r(0)),
            0x301b_abd4 => {
                let lr = r(reg::LR);
                let success = self.decode_jpeg(r(0), r(1), r(2), r(3));
                self.cpu
                    .reg_set(mode, 0, if success { 0 } else { 0xffff_fffe });
                self.branch_exchange(lr, None);
                return true;
            }
            0x3011_f92c => {
                let (source, length, destination, width_out, sp, lr) =
                    (r(0), r(1), r(2), r(3), r(reg::SP), r(reg::LR));
                let height_out = self
                    .bus
                    .bytes_at(sp, 4)
                    .map(|bytes| u32::from_le_bytes(bytes.try_into().unwrap()));
                let success = height_out.is_some_and(|height_out| {
                    self.bus.movie.decode(
                        &mut self.bus.ram,
                        source,
                        length,
                        destination,
                        width_out,
                        height_out,
                    )
                });
                self.cpu.reg_set(mode, 0, u32::from(success));
                self.branch_exchange(lr, None);
                return true;
            }
            0x300b_14e8 => self.bus.movie.present(&self.bus.ram, r(0)),
            0x3007_a4d0 => self.bus.movie = video::MovieDecoder::default(),
            0x3010_64dc | 0x3010_663c => self.bus.touch.calibrating = pc == 0x3010_64dc,
            0x3007_6a04 | 0x3007_6a98 if !self.bus.touch.calibrating => {
                let message = r(0);
                if self.bus.bytes_at(message, 12).is_some() && self.bus.r32(message) == 0xffff_000a
                {
                    let x = u32::from(self.bus.r16(message + 4));
                    let y = u32::from(self.bus.r16(message + 6));
                    let phase = u32::from(self.bus.r16(message + 8));
                    if matches!(phase, 1 | 2 | 5) {
                        self.bus.w32(message, 0xd03c);
                        self.bus.w32(message + 4, x << 16 | phase);
                        self.bus.w32(message + 8, y);
                        self.cpu.reg_set(mode, 1, 0);
                    }
                }
            }
            0x3007_6a04 | 0x3007_6a98 => {}
            0x3003_9bf0 => {
                if let Some(bytes) = self.bus.bytes_at(r(5), 512) {
                    let units: Vec<_> = bytes
                        .as_chunks::<2>()
                        .0
                        .iter()
                        .copied()
                        .map(u16::from_le_bytes)
                        .take_while(|&unit| unit != 0)
                        .collect();
                    eprintln!(
                        "fw open {:?} -> {:08x}",
                        String::from_utf16_lossy(&units),
                        r(0)
                    );
                }
            }
            _ => {
                let pointer = r(if pc == 0x3001_0820 { 0 } else { 2 });
                if let Some(bytes) = self.bus.bytes_at(pointer, 160) {
                    let end = bytes.iter().position(|&b| b == 0).unwrap_or(bytes.len());
                    if end > 0 {
                        eprintln!(
                            "fw {:08x} {}",
                            r(reg::LR),
                            String::from_utf8_lossy(&bytes[..end])
                        );
                    }
                }
            }
        }
        false
    }

    fn decode_jpeg(&mut self, descriptor: u32, destination: u32, width: u32, height: u32) -> bool {
        if self.bus.bytes_at(descriptor, 28).is_none()
            || !(1..=4096).contains(&width)
            || !(1..=4096).contains(&height)
        {
            return false;
        }
        let source = self.bus.r32(descriptor);
        let len = self.bus.r32(descriptor + 8) as usize;
        let stride = (width as usize * 3 + 3) & !3;
        let Some(output) = ram::offset(destination, stride * height as usize) else {
            return false;
        };
        let Some(data) = self.bus.bytes_at(source, len) else {
            return false;
        };
        let Some(bitmap) = jpeg::bitmap(data, width as usize, height as usize) else {
            return false;
        };
        self.bus.ram[output..output + bitmap.len()].copy_from_slice(&bitmap);
        self.bus.w32(descriptor + 0x14, width);
        self.bus.w32(descriptor + 0x18, height);
        true
    }
}

fn condition_passes(condition: u32, cpsr: u32) -> bool {
    let n = cpsr >> 31 != 0;
    let z = cpsr & (1 << 30) != 0;
    let c = cpsr & (1 << 29) != 0;
    let v = cpsr & (1 << 28) != 0;
    match condition {
        0 => z,
        1 => !z,
        2 => c,
        3 => !c,
        4 => n,
        5 => !n,
        6 => v,
        7 => !v,
        8 => c && !z,
        9 => !c || z,
        10 => n == v,
        11 => n != v,
        12 => !z && n == v,
        13 => z || n != v,
        14 => true,
        _ => false,
    }
}
