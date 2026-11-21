CXXFLAGS ?= -O2
CFLAGS ?= -O2
PYTHON ?= python3

SOURCES = bridge.cpp gme/Nes_Apu.cpp gme/Nes_Oscs.cpp gme/Blip_Buffer.cpp

.PHONY: all
all: libp36nes.so libp36slice.so

libp36nes.so: $(SOURCES) $(wildcard gme/*.h)
	$(CXX) $(CXXFLAGS) -std=c++11 -fPIC -shared $(LDFLAGS) -o $@ $(SOURCES)

libp36slice.so: block_budget.c
	$(CC) $(CFLAGS) -I"$$($(PYTHON) -c 'from pathlib import Path; import unicorn; print(Path(unicorn.__file__).parent / "include")')" \
		-fPIC -shared $(LDFLAGS) -o $@ $<
