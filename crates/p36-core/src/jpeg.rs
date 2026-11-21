use jpeg_decoder::{Decoder, PixelFormat};

pub(crate) fn yuv420(data: &[u8]) -> Option<(usize, usize, Vec<u8>)> {
    if !data.starts_with(&[0xff, 0xd8]) {
        return None;
    }
    let mut decoder = Decoder::new(data);
    decoder.set_max_decoding_buffer_size(1022 * 1022 * 3);
    decoder.read_info().ok()?;
    let info = decoder.info()?;
    let (width, height) = (usize::from(info.width), usize::from(info.height));
    if !(2..=1022).contains(&width)
        || !(2..=1022).contains(&height)
        || width % 2 != 0
        || height % 2 != 0
    {
        return None;
    }
    let pixels = decoder.decode().ok()?;
    let samples = width * height;
    let mut output = vec![128; samples * 3 / 2];
    let (y, chroma) = output.split_at_mut(samples);
    let (cb, cr) = chroma.split_at_mut(samples / 4);
    match info.pixel_format {
        PixelFormat::L8 => y.copy_from_slice(&pixels),
        PixelFormat::RGB24 => {
            let (pixels, _) = pixels.as_chunks::<3>();
            for row in (0..height).step_by(2) {
                for column in (0..width).step_by(2) {
                    let first = row * width + column;
                    let mut cb_sum = 0;
                    let mut cr_sum = 0;
                    for index in [first, first + 1, first + width, first + width + 1] {
                        let [r, g, b] = pixels[index].map(i32::from);
                        y[index] = ((77 * r + 150 * g + 29 * b + 128) >> 8) as u8;
                        cb_sum += -43 * r - 85 * g + 128 * b;
                        cr_sum += 128 * r - 107 * g - 21 * b;
                    }
                    let chroma = (row / 2) * (width / 2) + column / 2;
                    cb[chroma] = (128 + ((cb_sum + 512) >> 10)).clamp(0, 255) as u8;
                    cr[chroma] = (128 + ((cr_sum + 512) >> 10)).clamp(0, 255) as u8;
                }
            }
        }
        _ => return None,
    }
    Some((width, height, output))
}

pub(crate) fn bitmap(data: &[u8], width: usize, height: usize) -> Option<Vec<u8>> {
    if !(1..=4096).contains(&width)
        || !(1..=4096).contains(&height)
        || !data.starts_with(&[0xff, 0xd8])
    {
        return None;
    }
    let mut decoder = Decoder::new(data);
    decoder.set_max_decoding_buffer_size(64 * 1024 * 1024);
    let pixels = decoder.decode().ok()?;
    let info = decoder.info()?;
    let source_width = usize::from(info.width);
    let source_height = usize::from(info.height);
    let stride = (width * 3 + 3) & !3;
    let mut bitmap = vec![0; stride * height];
    for (row, output) in bitmap.chunks_exact_mut(stride).rev().enumerate() {
        let y = row * source_height / height;
        for (column, bgr) in output[..width * 3]
            .as_chunks_mut::<3>()
            .0
            .iter_mut()
            .enumerate()
        {
            let source = y * source_width + column * source_width / width;
            match info.pixel_format {
                PixelFormat::L8 => bgr.fill(pixels[source]),
                PixelFormat::RGB24 => {
                    let rgb = &pixels[source * 3..source * 3 + 3];
                    bgr.copy_from_slice(&[rgb[2], rgb[1], rgb[0]]);
                }
                _ => return None,
            }
        }
    }
    Some(bitmap)
}
