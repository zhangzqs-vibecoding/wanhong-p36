use p36_core::{BlockDevice, Machine, SECTOR_SIZE};
use std::{
    env,
    fs::{self, File},
    io::{Read, Seek, SeekFrom},
    time::Instant,
};

struct Card(File, u64);

impl BlockDevice for Card {
    fn sector_count(&self) -> u64 {
        self.1
    }

    fn read_sectors(&mut self, first: u32, output: &mut [u8]) -> Result<(), String> {
        self.0
            .seek(SeekFrom::Start(u64::from(first) * SECTOR_SIZE as u64))
            .and_then(|_| self.0.read_exact(output))
            .map_err(|err| err.to_string())
    }
}

fn main() -> Result<(), Box<dyn std::error::Error>> {
    let mut args = env::args().skip(1);
    let firmware = fs::read(args.next().ok_or("firmware path required")?)?;
    let image = File::open(args.next().ok_or("SD image path required")?)?;
    let total: u32 = args.next().ok_or("instruction budget required")?.parse()?;
    let output = args.next();
    let len = image.metadata()?.len();
    if len % SECTOR_SIZE as u64 != 0 {
        return Err("invalid image length".into());
    }
    let mut machine = Machine::new(&firmware, Card(image, len / SECTOR_SIZE as u64))?;
    machine.set_trace(true);
    let start = Instant::now();
    let mut count = 0;
    while count < total {
        let step = (total - count).min(10_000_000);
        machine.step(step)?;
        count += step;
        let frame = machine.frame();
        eprintln!(
            "{count} pc={:08x} frame={:?} elapsed={:.3}s",
            machine.pc(),
            frame
                .as_ref()
                .map(|frame| (frame.sequence, frame.width, frame.height)),
            start.elapsed().as_secs_f64()
        );
        if let Some((path, frame)) = output.as_ref().zip(frame) {
            fs::write(path, frame.rgb565)?;
        }
        if let Some(audio) = machine.take_audio() {
            eprintln!("audio/mpeg {} bytes", audio.len());
            if let Some(path) = &output {
                fs::write(format!("{path}.mp3"), audio)?;
            }
        }
    }
    Ok(())
}
