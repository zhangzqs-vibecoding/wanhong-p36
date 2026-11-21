use crate::ram;

pub(crate) const RGB_CONTROL: u32 = 0x2002_2100;
pub(crate) const RGB24_TO_RGB565: u32 = 0x21;
const LOADED: u32 = 0x31e1_75ec;
const KEYMAP: u32 = 0x305a_df7a;

pub(crate) fn active(ram: &[u8]) -> bool {
    let start = (LOADED - ram::BASE) as usize;
    ram[start..start + 4] != [0; 4]
}

pub(crate) fn keys(ram: &[u8]) -> Option<[u8; 16]> {
    let start = (KEYMAP - ram::BASE) as usize;
    active(ram).then(|| ram[start..start + 16].try_into().unwrap())
}

pub(crate) fn convert_rgb24(ram: &mut [u8], parameters: [u32; 9]) -> bool {
    let [source, _, _, source_size, _, destination, output_size, padding, position] = parameters;
    let (width, height) = dimensions(source_size);
    let (output_width, output_height) = dimensions(output_size);
    let (x, y) = dimensions(position);
    if [width, height, output_width, output_height]
        .into_iter()
        .any(|size| !(1..=2048).contains(&size))
        || x * 2 > padding as usize
    {
        return false;
    }
    let row_bytes = output_width * 2;
    let Some(stride) = row_bytes.checked_add(padding as usize) else {
        return false;
    };
    let Some(first_pixel) = y
        .checked_mul(stride)
        .and_then(|offset| offset.checked_add(x * 2))
        .and_then(|offset| u32::try_from(offset).ok())
        .and_then(|offset| destination.checked_add(offset))
    else {
        return false;
    };
    let Some(output_bytes) = (output_height - 1)
        .checked_mul(stride)
        .and_then(|offset| offset.checked_add(row_bytes))
    else {
        return false;
    };
    let source_bytes = width * height * 3;
    let (Some(source), Some(first_pixel)) = (
        ram::offset(source, source_bytes),
        ram::offset(first_pixel, output_bytes),
    ) else {
        return false;
    };
    let pixels = ram[source..source + source_bytes].to_vec();
    let columns: Vec<_> = (0..output_width)
        .map(|column| sample_span(column, width, output_width))
        .collect();
    for row in 0..output_height {
        let (top, bottom, vertical) = sample_span(row, height, output_height);
        let start = first_pixel + row * stride;
        let (output, _) = ram[start..start + row_bytes].as_chunks_mut::<2>();
        for (pixel, &(left, right, horizontal)) in output.iter_mut().zip(&columns) {
            let channel = |index| {
                let at = |row, column| u32::from(pixels[(row * width + column) * 3 + index]);
                let above = interpolate(at(top, left), at(top, right), horizontal);
                let below = interpolate(at(bottom, left), at(bottom, right), horizontal);
                ((interpolate(above, below, vertical) + 0x8000) >> 16) as u16
            };
            let rgb565 = (channel(0) & 0xf8) << 8 | (channel(1) & 0xfc) << 3 | channel(2) >> 3;
            *pixel = rgb565.to_le_bytes();
        }
    }
    true
}

fn dimensions(word: u32) -> (usize, usize) {
    ((word & 0xffff) as usize, (word >> 16) as usize)
}

fn sample_span(index: usize, source: usize, output: usize) -> (usize, usize, u32) {
    // Map the output pixel center to source space with eight fractional bits.
    let position = (((2 * index + 1) * source * 128) / output).saturating_sub(128);
    let first = (position / 256).min(source - 1);
    (first, (first + 1).min(source - 1), (position & 255) as u32)
}

fn interpolate(first: u32, second: u32, weight: u32) -> u32 {
    first * (256 - weight) + second * weight
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn rgb_conversion_scales_into_a_positioned_scanout() {
        let mut ram = [0; 48];
        ram[..6].copy_from_slice(&[255, 0, 0, 0, 0, 255]);
        let parameters = [
            ram::BASE,
            0,
            0,
            1 << 16 | 2,
            0,
            ram::BASE + 8,
            2 << 16 | 4,
            6,
            1 << 16 | 2,
        ];
        assert!(convert_rgb24(&mut ram, parameters));
        let row = [0xf800u16, 0xb808, 0x4017, 0x001f].map(u16::to_le_bytes);
        let mut expected = [0; 48];
        expected[..6].copy_from_slice(&ram[..6]);
        for start in [26, 40] {
            expected[start..start + 8].copy_from_slice(row.as_flattened());
        }
        assert_eq!(ram, expected);
    }
}
