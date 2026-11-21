pub const BASE: u32 = 0x3000_0000;
pub const SIZE: usize = 32 * 1024 * 1024;

pub fn offset(address: u32, len: usize) -> Option<usize> {
    let start = address.wrapping_sub(BASE) as usize;
    (start <= SIZE && len <= SIZE - start).then_some(start)
}
