use crate::{jpeg, ram};

#[derive(Default)]
pub(crate) struct MovieDecoder {
    previous: Option<(u32, usize, usize)>,
    timestamps: Option<(u32, u32, u32)>,
    pub(crate) position_ms: Option<u32>,
}

impl MovieDecoder {
    pub(crate) fn present(&mut self, ram: &[u8], player: u32) {
        let Some(offset) = ram::offset(player, 0x3c).filter(|_| self.previous.is_some()) else {
            return;
        };
        let word = |field| u32::from_le_bytes(ram[offset + field..][..4].try_into().unwrap());
        let (pts, index, start) = (word(0x2c), word(0x34), word(0x38));
        if self.timestamps == Some((pts, index, start)) {
            return;
        }
        let frame_duration = match self.timestamps {
            Some((last_pts, last_index, last_start))
                if start == last_start && index > last_index && pts > last_pts =>
            {
                (pts - last_pts) / (index - last_index)
            }
            _ => 0,
        };
        self.timestamps = Some((pts, index, start));
        self.position_ms = Some(pts.saturating_sub(start).saturating_sub(frame_duration));
    }

    pub(crate) fn decode(
        &mut self,
        ram: &mut [u8],
        source: u32,
        length: u32,
        destination: u32,
        width_out: u32,
        height_out: u32,
    ) -> bool {
        let (Some(width_out), Some(height_out)) =
            (ram::offset(width_out, 4), ram::offset(height_out, 4))
        else {
            return false;
        };
        let (width, height) = if length == 0 {
            let Some((address, width, height)) = self.previous else {
                return false;
            };
            if address != destination {
                return false;
            }
            (width, height)
        } else {
            let length = length as usize;
            let Some(source) = ram::offset(source, length) else {
                return false;
            };
            let Some((width, height, pixels)) = jpeg::yuv420(&ram[source..source + length]) else {
                return false;
            };
            let Some(out) = ram::offset(destination, pixels.len()) else {
                return false;
            };
            ram[out..out + pixels.len()].copy_from_slice(&pixels);
            self.previous = Some((destination, width, height));
            (width, height)
        };
        ram[width_out..width_out + 4].copy_from_slice(&(width as u32).to_le_bytes());
        ram[height_out..height_out + 4].copy_from_slice(&(height as u32).to_le_bytes());
        true
    }
}

#[derive(Clone, Copy, Default)]
pub(crate) struct VideoLayer {
    pub addresses: [u32; 3],
    pub horizontal: u32,
    pub vertical: u32,
    pub position: u32,
}

impl VideoLayer {
    pub(crate) fn render(
        &self,
        ram: &[u8],
        pixels: &mut Vec<u8>,
        width: usize,
        height: usize,
    ) -> bool {
        let (source_width, output_width) = layer_size(self.horizontal);
        let (source_height, output_height) = layer_size(self.vertical);
        let (y, x) = layer_size(self.position);
        if source_width == 0
            || source_height == 0
            || source_width % 2 != 0
            || source_height % 2 != 0
            || output_width == 0
            || output_height == 0
            || x + output_width > width
            || y + output_height > height
        {
            return false;
        }
        let len = source_width * source_height;
        let plane = |address, len| ram::offset(address, len).map(|start| &ram[start..start + len]);
        let [luma, blue, red] = self.addresses;
        let (Some(luma), Some(blue), Some(red)) =
            (plane(luma, len), plane(blue, len / 4), plane(red, len / 4))
        else {
            return false;
        };
        pixels.resize(width * height * 2, 0);
        if output_width != width || output_height != height {
            pixels.fill(0);
        }
        let columns: Vec<_> = (0..output_width)
            .map(|column| column * source_width / output_width)
            .collect();
        for row in 0..output_height {
            let source_row = row * source_height / output_height;
            let y_offset = source_row * source_width;
            let uv_offset = (source_row / 2) * (source_width / 2);
            let out = ((row + y) * width + x) * 2;
            let (output, _) = pixels[out..out + output_width * 2].as_chunks_mut::<2>();
            for (&column, pixel) in columns.iter().zip(output) {
                let y = i32::from(luma[y_offset + column]) << 8;
                let cb = i32::from(blue[uv_offset + column / 2]) - 128;
                let cr = i32::from(red[uv_offset + column / 2]) - 128;
                let r = ((y + 359 * cr + 128) >> 8).clamp(0, 255) as u16;
                let g = ((y - 88 * cb - 183 * cr + 128) >> 8).clamp(0, 255) as u16;
                let b = ((y + 454 * cb + 128) >> 8).clamp(0, 255) as u16;
                let rgb565 = (r & 0xf8) << 8 | (g & 0xfc) << 3 | b >> 3;
                *pixel = rgb565.to_le_bytes();
            }
        }
        true
    }
}

fn layer_size(register: u32) -> (usize, usize) {
    (
        (register & 0x3ff) as usize,
        ((register >> 10) & 0x3ff) as usize,
    )
}
