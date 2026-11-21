use p36_core::{read_card_firmware, BlockDevice, Machine, Mp3State, SECTOR_SIZE};
use wasm_bindgen::prelude::*;

#[wasm_bindgen]
extern "C" {
    #[wasm_bindgen(typescript_type = "(offset: number, length: number) => Uint8Array")]
    pub type ReadImage;

    #[wasm_bindgen(catch, method, js_name = call)]
    fn read_image(
        this: &ReadImage,
        context: &JsValue,
        offset: f64,
        length: u32,
    ) -> Result<Vec<u8>, JsValue>;
}

struct Card {
    sectors: u64,
    read: ReadImage,
}

impl BlockDevice for Card {
    fn sector_count(&self) -> u64 {
        self.sectors
    }

    fn read_sectors(&mut self, first: u32, output: &mut [u8]) -> Result<(), String> {
        let position = f64::from(first) * SECTOR_SIZE as f64;
        let bytes = self
            .read
            .read_image(&JsValue::UNDEFINED, position, output.len() as u32)
            .map_err(|err| {
                err.as_string()
                    .unwrap_or_else(|| format!("SD read failed at {position}"))
            })?;
        if bytes.len() != output.len() {
            return Err(format!("Short SD read at {position}"));
        }
        output.copy_from_slice(&bytes);
        Ok(())
    }
}

#[wasm_bindgen]
pub struct P36 {
    machine: Machine<Card>,
}

#[wasm_bindgen]
pub struct FileAudio(p36_core::Mp3Event);

#[wasm_bindgen]
impl FileAudio {
    #[wasm_bindgen(getter)]
    pub fn track(&self) -> u32 {
        self.0.track
    }

    #[wasm_bindgen(getter)]
    pub fn state(&self) -> String {
        match self.0.state {
            Mp3State::Playing => "playing",
            Mp3State::Paused => "paused",
            Mp3State::Ended => "ended",
            Mp3State::Stopped => "stopped",
        }
        .into()
    }

    pub fn take_contents(&mut self) -> Vec<u8> {
        std::mem::take(&mut self.0.bytes)
    }

    #[wasm_bindgen(getter)]
    pub fn movie_seconds(&self) -> Option<f64> {
        self.0.movie_seconds
    }
}

#[wasm_bindgen]
pub struct BufferedMovieFrame(p36_core::MovieFrame);

#[wasm_bindgen]
impl BufferedMovieFrame {
    #[wasm_bindgen(getter)]
    pub fn sequence(&self) -> f64 {
        self.0.sequence as f64
    }

    #[wasm_bindgen(getter)]
    pub fn width(&self) -> u32 {
        self.0.width
    }

    #[wasm_bindgen(getter)]
    pub fn height(&self) -> u32 {
        self.0.height
    }

    #[wasm_bindgen(getter)]
    pub fn track(&self) -> u32 {
        self.0.position.track
    }

    #[wasm_bindgen(getter)]
    pub fn milliseconds(&self) -> u32 {
        self.0.position.milliseconds
    }

    pub fn take_pixels(&mut self) -> Vec<u8> {
        std::mem::take(&mut self.0.rgb565)
    }
}

#[wasm_bindgen]
impl P36 {
    #[wasm_bindgen(constructor)]
    pub fn new(firmware: &[u8], image_bytes: f64, read: ReadImage) -> Result<Self, JsValue> {
        if !(0.0..=512.0 * (1u64 << 32) as f64).contains(&image_bytes) || image_bytes % 512.0 != 0.0
        {
            return Err(JsValue::from_str("Invalid SD image size"));
        }
        let mut card = Card {
            sectors: (image_bytes as u64) / SECTOR_SIZE as u64,
            read,
        };
        let detected;
        let firmware = if firmware.is_empty() {
            detected = read_card_firmware(&mut card).map_err(|error| JsValue::from_str(&error))?;
            &detected
        } else {
            firmware
        };
        let machine = Machine::new(firmware, card).map_err(|error| JsValue::from_str(&error))?;
        Ok(Self { machine })
    }

    pub fn step(&mut self, instructions: u32) -> Result<(), JsValue> {
        self.machine
            .step(instructions)
            .map_err(|error| JsValue::from_str(&error))
    }

    pub fn powered_off(&self) -> bool {
        self.machine.powered_off()
    }

    pub fn restore_sd_sector(&mut self, sector: u32, bytes: &[u8]) -> Result<(), JsValue> {
        let bytes = bytes
            .try_into()
            .map_err(|_| JsValue::from_str("Invalid saved sector size"))?;
        if self.machine.restore_sd_sector(sector, bytes) {
            Ok(())
        } else {
            Err(JsValue::from_str("Saved sector is outside the SD image"))
        }
    }

    pub fn take_sd_writes(&mut self) -> Vec<u8> {
        let writes = self.machine.take_sd_writes();
        let mut output = Vec::with_capacity(writes.len() * (4 + SECTOR_SIZE));
        for (sector, bytes) in writes {
            output.extend_from_slice(&sector.to_le_bytes());
            output.extend_from_slice(&bytes);
        }
        output
    }

    pub fn frame_number(&self) -> f64 {
        self.machine
            .frame()
            .map_or(0.0, |frame| frame.sequence as f64)
    }

    pub fn frame_width(&self) -> u32 {
        self.machine.frame().map_or(0, |frame| frame.width)
    }

    pub fn frame_height(&self) -> u32 {
        self.machine.frame().map_or(0, |frame| frame.height)
    }

    pub fn frame_rgb565(&self) -> Vec<u8> {
        self.machine
            .frame()
            .map_or_else(Vec::new, |frame| frame.rgb565.to_vec())
    }

    pub fn movie_track(&self) -> Option<u32> {
        self.machine.frame()?.movie.map(|position| position.track)
    }

    pub fn movie_milliseconds(&self) -> Option<u32> {
        self.machine
            .frame()?
            .movie
            .map(|position| position.milliseconds)
    }

    pub fn take_movie_startup_frame(&mut self) -> Option<BufferedMovieFrame> {
        self.machine
            .take_movie_startup_frame()
            .map(BufferedMovieFrame)
    }

    pub fn take_audio(&mut self) -> Option<Vec<u8>> {
        self.machine.take_audio()
    }

    pub fn recording(&self) -> bool {
        self.machine.recording()
    }

    pub fn submit_microphone(&mut self, samples: &[i16]) -> bool {
        self.machine.submit_microphone(samples)
    }

    pub fn take_file_audio(&mut self) -> Option<FileAudio> {
        self.machine.take_mp3_event().map(FileAudio)
    }

    pub fn set_audio_time(&mut self, time_us: f64) {
        self.machine.set_audio_time(time_us as u64);
    }

    pub fn volume(&self) -> u8 {
        self.machine.volume()
    }

    pub fn set_key(&mut self, code: u8, down: bool) -> bool {
        self.machine.set_key(code, down)
    }

    pub fn text_input_ready(&self) -> bool {
        self.machine.text_input_ready()
    }

    pub fn nes_keys(&self) -> Option<Vec<u8>> {
        self.machine.nes_keys().map(Vec::from)
    }

    pub fn touch(&mut self, x: u16, y: u16, phase: u8) -> bool {
        self.machine.touch(x, y, phase)
    }
}
