#include <new>

#include "gme/Nes_Apu.h"

struct GameSound {
    Blip_Buffer buffer;
    Nes_Apu apu;
};

extern "C" GameSound* p36_nes_create(int rate, int clock, int (*read_memory)(void*, nes_addr_t)) {
    GameSound* sound = new (std::nothrow) GameSound;
    if (!sound) {
        return nullptr;
    }
    if (sound->buffer.set_sample_rate(rate)) {
        delete sound;
        return nullptr;
    }
    sound->buffer.clock_rate(clock);
    sound->apu.output(&sound->buffer);
    sound->apu.dmc_reader(read_memory);
    return sound;
}

extern "C" void p36_nes_write(GameSound* sound, int cycles, unsigned address, int value) {
    sound->apu.write_register(cycles, address, value);
}

extern "C" int p36_nes_render(GameSound* sound, int cycles, short* samples, int capacity) {
    sound->apu.end_frame(cycles);
    sound->buffer.end_frame(cycles);
    return static_cast<int>(sound->buffer.read_samples(samples, capacity));
}

extern "C" void p36_nes_destroy(GameSound* sound) {
    delete sound;
}
