"""P36 SDHC registers and the SD route through the Anyka L2 DMA controller."""

import os
import struct


SD_BASE = 0x20020000
L2_BASE = 0x2002C000
L2_SRAM = 0x48000000
SDRAM_BASE = 0x30000000
SDRAM_SIZE = 32 * 1024 ** 2
SECTOR_SIZE = 512
SD_L2_DEVICE = 4

COMMAND_SENT = 0x20
RESPONSE_END = 0x10
RESPONSE_TIMEOUT = 0x04
DATA_TIMEOUT = 0x08
DATA_BUSY = 0x800
RX_FIFO_READY = 0x1000


class SdCard:
    def __init__(self, image, trace=False, saved_sectors=None):
        self.image = image
        size = os.fstat(image.fileno()).st_size
        self.sectors = size // SECTOR_SIZE
        if not 0 < self.sectors <= 2 ** 32 or size % (1024 * SECTOR_SIZE):
            raise ValueError("SDHC image size must be a positive multiple of 512 KiB")
        self.trace = trace
        self.argument = 0
        self.length = 0
        self.status = 0
        self.response = (0, 0, 0, 0)
        self.app_command = False
        self.selected = False
        self.buffer = None
        self.read_data = b""
        self.position = 0
        self.write_sector = None
        self.single_write = False
        self.overlay = {}
        self.saved_sectors = saved_sectors

    def read_register(self, address):
        if SD_BASE + 0x14 <= address <= SD_BASE + 0x20:
            return self.response[(address - SD_BASE - 0x14) // 4]
        if address == SD_BASE + 0x34:
            if self.read_data and self.buffer is None:
                return self.status | DATA_BUSY | RX_FIFO_READY
            return self.status
        if address == SD_BASE + 0x40:
            return int.from_bytes(self.take_read_data(4), "little")

    def write_register(self, uc, address, value):
        if address == SD_BASE + 0x08:
            self.argument = value
        elif address == SD_BASE + 0x0C and value & 1:
            self.send_command((value >> 1) & 0x3F)
        elif address == SD_BASE + 0x28:
            self.length = value
        elif address == SD_BASE + 0x2C and value & 3 == 1 and self.write_sector is not None:
            self.enable_write(uc)
        elif address == L2_BASE + 0x90:
            self.buffer = (value >> (SD_L2_DEVICE * 3)) & 7
        elif address == L2_BASE + 0x80:
            return self.transfer_dma(uc, value)

    def send_command(self, command):
        argument = self.argument
        application = self.app_command
        self.app_command = command == 55
        self.status = COMMAND_SENT | RESPONSE_END
        if self.trace:
            prefix = "ACMD" if application else "CMD"
            print(f"sd {prefix}{command} arg=0x{argument:08x}")
        response = (0x900 if self.selected else 0x100, 0, 0, 0)
        if command == 0:
            self.selected = False
            self.read_data = b""
            self.write_sector = None
        elif command == 8:
            response = (argument & 0xFFF, 0, 0, 0)
        elif command == 41 and application:
            response = (0xC0FF8000, 0, 0, 0)
        elif command == 2:
            response = (0x03503350, 0x33365344, 0x01000000, 0x01011001)
        elif command == 3:
            response = (0x00010700, 0, 0, 0)
        elif command == 9:
            capacity = self.sectors // 1024 - 1
            response = (0x400E0032, 0x5B590000 | (capacity >> 16),
                        ((capacity & 0xFFFF) << 16) | 0x7F80, 0x0A400001)
        elif command == 7:
            self.selected = bool(argument)
        elif command in (17, 18):
            self.start_read(argument)
        elif command in (24, 25):
            self.write_sector = argument
            self.single_write = command == 24
            self.position = 0
        elif command == 12:
            if self.write_sector is not None:
                self.status |= DATA_TIMEOUT
            self.write_sector = None
            self.read_data = b""
        elif command not in (6, 13, 16, 55):
            self.status = RESPONSE_TIMEOUT
        self.response = response

    def valid_transfer(self, sector):
        return (self.selected and 0 < self.length <= 32 * 1024
                and self.length % SECTOR_SIZE == 0
                and sector + self.length // SECTOR_SIZE <= self.sectors)

    def start_read(self, sector):
        self.read_data = b""
        if not self.valid_transfer(sector):
            self.status |= DATA_TIMEOUT
            return
        self.position = 0
        blocks = self.length // SECTOR_SIZE
        if self.trace:
            print(f"sd read LBA={sector} blocks={blocks}")
        data = bytearray(os.pread(self.image.fileno(), self.length, sector * SECTOR_SIZE))
        if len(data) != self.length:
            self.status |= DATA_TIMEOUT
            return
        if self.saved_sectors is not None:
            changes = self.saved_sectors.read(sector, blocks)
        else:
            changes = ((lba, self.overlay[lba]) for lba in range(sector, sector + blocks)
                       if lba in self.overlay)
        for lba, content in changes:
            begin = (lba - sector) * SECTOR_SIZE
            data[begin:begin + SECTOR_SIZE] = content
        self.read_data = bytes(data)

    def enable_write(self, uc):
        if not self.valid_transfer(self.write_sector):
            self.status |= DATA_TIMEOUT
            self.write_sector = None
            return
        if self.trace:
            print(f"sd write LBA={self.write_sector} blocks={self.length // SECTOR_SIZE}")
        if self.single_write:
            if self.buffer is None or self.length != SECTOR_SIZE:
                self.status |= DATA_TIMEOUT
                return
            data = uc.mem_read(L2_SRAM + self.buffer * SECTOR_SIZE, SECTOR_SIZE)
            self.write_blocks(data)

    def write_blocks(self, data):
        if not data or len(data) % SECTOR_SIZE or self.position + len(data) > self.length:
            self.status |= DATA_TIMEOUT
            return
        sector = self.write_sector + self.position // SECTOR_SIZE
        changes = ((sector + offset // SECTOR_SIZE, bytes(data[offset:offset + SECTOR_SIZE]))
                   for offset in range(0, len(data), SECTOR_SIZE))
        if self.saved_sectors is not None:
            self.saved_sectors.write(changes)
        else:
            self.overlay.update(changes)
        self.position += len(data)
        if self.position == self.length:
            self.write_sector = None

    def take_read_data(self, length):
        end = self.position + length
        data = self.read_data[self.position:end]
        self.position = end
        if self.position >= len(self.read_data):
            self.read_data = b""
        return data

    def transfer_dma(self, uc, control):
        requests = control >> 24
        if not requests or requests & (requests - 1):
            return
        buffer = requests.bit_length() - 1
        request = 1 << (24 + buffer)

        address = SDRAM_BASE | struct.unpack("<I", uc.mem_read(L2_BASE + buffer * 4, 4))[0]
        length = 64 * uc.mem_read(L2_BASE + 0x40 + buffer * 4, 1)[0]
        direction = struct.unpack("<I", uc.mem_read(L2_BASE + 0x88, 4))[0]
        if not length or not SDRAM_BASE <= address <= SDRAM_BASE + SDRAM_SIZE - length:
            return
        if direction & (1 << (8 + buffer)):
            data = bytes(uc.mem_read(address, length))
            if buffer == self.buffer and self.write_sector is not None:
                self.write_blocks(data)
            elif length <= SECTOR_SIZE:
                uc.mem_write(L2_SRAM + buffer * SECTOR_SIZE, data)
            else:
                return
        elif buffer == self.buffer and self.read_data:
            data = self.take_read_data(length)
            if len(data) == length:
                uc.mem_write(address, data)
            else:
                self.status |= DATA_TIMEOUT
        else:
            return
        if self.trace:
            print(f"sd L2 DMA buffer={buffer} addr=0x{address:08x} bytes={length}")
        return control & ~request
