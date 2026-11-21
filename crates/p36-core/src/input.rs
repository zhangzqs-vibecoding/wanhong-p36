use crate::ram;

pub(crate) const GPIO_INPUT: u32 = 0x0800_00bc;
pub(crate) const GPIO_ENABLE: u32 = 0x0800_00e0;
pub(crate) const GPIO_STATUS: u32 = 1 << 26;
const GPIO_OUTPUT: u32 = 0x0800_0080;
const GPIO_POLARITY: u32 = 0x0800_00f0;
const MATRIX: usize = 0x224b90;

#[derive(Clone, Copy)]
struct KeyPins {
    row: Option<u8>,
    input: u8,
}

pub(crate) struct Keypad {
    pins: [Option<KeyPins>; 256],
    pub(crate) input_mask: [u32; 4],
    pressed: Vec<u8>,
}

impl Keypad {
    pub(crate) fn new(firmware: &[u8]) -> Option<Self> {
        let read = |offset| {
            Some(u32::from_le_bytes(
                firmware.get(offset..offset + 4)?.try_into().ok()?,
            ))
        };
        let rows = read(MATRIX)? as usize;
        let columns = read(MATRIX + 4)? as usize;
        if !(1..=32).contains(&rows) || !(1..=32).contains(&columns) {
            return None;
        }
        let row_start = ram::offset(read(MATRIX + 8)?, rows)?;
        let column_start = ram::offset(read(MATRIX + 12)?, columns)?;
        let codes = ram::offset(read(MATRIX + 16)?, rows * columns * 4)?;
        let row_pins = firmware.get(row_start..row_start + rows)?;
        let column_pins = firmware.get(column_start..column_start + columns)?;
        let mut keypad = Self {
            pins: [None; 256],
            input_mask: [0; 4],
            pressed: Vec::new(),
        };
        for (row, &row_pin) in row_pins.iter().enumerate() {
            for (column, &column_pin) in column_pins.iter().enumerate() {
                let code = read(codes + (row * columns + column) * 4)?;
                if row_pin >= 128 || column_pin >= 128 || code > 255 {
                    return None;
                }
                if code != 0xff {
                    keypad.pins[code as usize] = Some(KeyPins {
                        row: Some(row_pin),
                        input: column_pin,
                    });
                }
                keypad.input_mask[usize::from(column_pin / 32)] |= 1 << (column_pin % 32);
            }
        }
        // The power switch drives an independent GPIO input.
        if read(MATRIX + 0x24)? == 1 {
            let pin = read(MATRIX + 0x1c)?;
            let code = read(MATRIX + 0x20)?;
            if pin >= 128 || code >= 0xff || keypad.pins[code as usize].is_some() {
                return None;
            }
            keypad.pins[code as usize] = Some(KeyPins {
                row: None,
                input: pin as u8,
            });
            keypad.input_mask[(pin / 32) as usize] |= 1 << (pin % 32);
        }
        Some(keypad)
    }

    pub(crate) fn set(&mut self, code: u8, down: bool) -> bool {
        if self.pins[code as usize].is_none() {
            return false;
        }
        match (self.pressed.iter().position(|&value| value == code), down) {
            (None, true) => self.pressed.push(code),
            (Some(index), false) => {
                self.pressed.swap_remove(index);
            }
            _ => {}
        }
        true
    }

    pub(crate) fn levels(&self, bank: usize, read: impl Fn(u32) -> u32) -> u32 {
        let mut bits = 0;
        for &code in &self.pressed {
            let KeyPins { row, input } = self.pins[code as usize].unwrap();
            if usize::from(input / 32) == bank
                && row.is_none_or(|row| {
                    read(GPIO_OUTPUT + 8 * u32::from(row / 32)) & (1 << (row % 32)) != 0
                })
            {
                bits |= 1 << (input % 32);
            }
        }
        bits
    }

    pub(crate) fn pending(&self, read: impl Fn(u32) -> u32) -> bool {
        self.input_mask.iter().enumerate().any(|(bank, &mask)| {
            let offset = bank as u32 * 4;
            let enabled = read(GPIO_ENABLE + offset) & mask;
            enabled != 0 && enabled & (self.levels(bank, &read) ^ read(GPIO_POLARITY + offset)) != 0
        })
    }
}

pub(crate) struct Touch {
    pub(crate) x: u32,
    pub(crate) y: u32,
    pub(crate) down: bool,
    pub(crate) edge: bool,
    pub(crate) calibrating: bool,
}

impl Default for Touch {
    fn default() -> Self {
        Self {
            x: 511,
            y: 240,
            down: false,
            edge: false,
            calibrating: false,
        }
    }
}

impl Touch {
    pub(crate) fn contact(&mut self, x: u16, y: u16, phase: u8) {
        let down = if phase == 5 { self.down } else { phase == 2 };
        if down {
            self.x = 1023 - u32::from(x.clamp(1, 799));
            self.y = u32::from(y.clamp(1, 479));
        }
        self.edge |= self.down != down;
        self.down = down;
    }
}
