import init, { P36 } from "./pkg/p36_web.js";
import { openCardState } from "./card-state.js";

const wasm = init();
const batchInstructions = 120_000;
const transitionInstructions = 4_800_000;
const gameTransitionInstructions = 600_000;
const textKeyIdleTimeout = 1500;
const cacheLine = 256 * 1024;
const cacheLines = 16;
const sectorBytes = 512;
const volatileSectors = new Map();
let runId = 0;
let selectedImage;
let machine;
let input = [];
let keyDelay = 0;
let textKeyDeadline;
let touchDelay = 0;
let lastFrame = 0;
let nesKeys;
let recording = false;
let stopAt = 0;
let cardState;
let pendingSave;
let pendingLoad = Promise.resolve();

function imageReader(file) {
  const reader = new FileReaderSync();
  const cache = new Map();
  function block(index) {
    if (cache.has(index)) {
      const data = cache.get(index);
      cache.delete(index);
      cache.set(index, data);
      return data;
    }
    const offset = index * cacheLine;
    const data = new Uint8Array(reader.readAsArrayBuffer(file.slice(offset, offset + cacheLine)));
    cache.set(index, data);
    if (cache.size > cacheLines) cache.delete(cache.keys().next().value);
    return data;
  }
  return (offset, length) => {
    if (offset < 0 || offset + length > file.size) throw new Error("SD 卡镜像读出边界");
    const index = Math.floor(offset / cacheLine);
    const start = offset % cacheLine;
    if (start + length <= cacheLine) return block(index).subarray(start, start + length);
    const output = new Uint8Array(length);
    let written = 0;
    while (written < length) {
      const address = offset + written;
      const withinBlock = address % cacheLine;
      const data = block(Math.floor(address / cacheLine)).subarray(withinBlock, withinBlock + length - written);
      output.set(data, written);
      written += data.length;
    }
    return output;
  };
}

function deliverInput() {
  const event = input[0];
  if (!event) return;
  if (event.type === "key" && keyDelay <= 0) {
    const { code, down } = event;
    const typing = code === 0x1f || code === 0x22 ||
      (code >= 0x30 && code <= 0x39) || (code >= 0x41 && code <= 0x5a);
    // File-backed text entry can miss a press while processing the previous character.
    if (down && typing && !machine.text_input_ready()) {
      const now = performance.now();
      textKeyDeadline ??= now + textKeyIdleTimeout;
      if (now < textKeyDeadline) return;
    }
    textKeyDeadline = undefined;
    machine.set_key(code, down);
    keyDelay = nesKeys ? gameTransitionInstructions : transitionInstructions;
  } else if (event.type === "touch" && touchDelay <= 0) {
    machine.touch(event.x, event.y, event.phase);
    touchDelay = transitionInstructions;
  } else {
    return;
  }
  input.shift();
}

async function step(id) {
  if (id !== runId) return;
  try {
    const until = performance.now() + 10;
    do {
      deliverInput();
      machine.set_audio_time(performance.now() * 1000);
      machine.step(batchInstructions);
      keyDelay -= batchInstructions;
      touchDelay -= batchInstructions;
    } while (!machine.powered_off() && performance.now() < until);

    const writes = machine.take_sd_writes();
    if (cardState && writes.length) {
      pendingSave = cardState.write(writes);
      await pendingSave;
      if (id !== runId) return;
      pendingSave = undefined;
    } else if (writes.length) {
      const view = new DataView(writes.buffer, writes.byteOffset, writes.byteLength);
      for (let offset = 0; offset < writes.length; offset += 4 + sectorBytes) {
        const sector = view.getUint32(offset, true);
        volatileSectors.set(sector, writes.slice(offset + 4, offset + 4 + sectorBytes));
      }
    }
    if (machine.powered_off()) {
      input = [];
      recording = false;
      postMessage({ type: "power-off" });
      return;
    }

    let contents;
    let event;
    while ((event = machine.take_file_audio())) {
      const { track, state, movie_seconds: movieSeconds } = event;
      contents = event.take_contents();
      event.free();
      postMessage({ type: "mp3", track, state, contents, movieSeconds, volume: machine.volume() }, [contents.buffer]);
    }
    const keys = machine.nes_keys();
    if (keys?.join(",") !== nesKeys?.join(",")) {
      nesKeys = keys;
      postMessage({ type: "nes-keys", keys });
    }
    const active = machine.recording();
    if (active !== recording) {
      recording = active;
      postMessage({ type: "recording", active });
    }
    sendFrames();
    while ((contents = machine.take_audio())) {
      postMessage({ type: "audio", contents, volume: machine.volume() }, [contents.buffer]);
    }
    stopAt = setTimeout(() => step(id), 0);
  } catch (error) {
    if (id === runId) fail(error);
  }
}

function sendFrames() {
  let frame;
  while ((frame = machine.take_movie_startup_frame())) {
    const { sequence, width, height, track, milliseconds } = frame;
    const pixels = frame.take_pixels();
    frame.free();
    lastFrame = sequence;
    postMessage({ type: "frame", width, height, pixels, track, milliseconds }, [pixels.buffer]);
  }
  const sequence = machine.frame_number();
  if (sequence !== lastFrame) {
    lastFrame = sequence;
    const pixels = machine.frame_rgb565();
    const track = machine.movie_track();
    const milliseconds = machine.movie_milliseconds();
    postMessage({
      type: "frame", width: machine.frame_width(), height: machine.frame_height(), pixels, track, milliseconds,
    }, [pixels.buffer]);
  }
}

function fail(error) {
  postMessage({ type: "error", message: error instanceof Error ? error.message : String(error) });
}

async function load(id, file, reboot) {
  if (id !== runId) return;
  try {
    await pendingSave?.catch(() => {});
    pendingSave = undefined;
    if (!reboot) {
      await cardState?.close();
      cardState = undefined;
    }
    machine?.free();
    machine = undefined;
    await wasm;
    if (id !== runId) return;
    if (!reboot) cardState = await openCardState(file);
    const savedSectors = await cardState?.read();
    if (id !== runId) return;
    machine = new P36(new Uint8Array(), file.size, imageReader(file));
    for (const { sector, bytes } of savedSectors ?? []) {
      machine.restore_sd_sector(sector, bytes);
    }
    for (const [sector, bytes] of volatileSectors) machine.restore_sd_sector(sector, bytes);
    postMessage({ type: "started", persistent: Boolean(cardState) });
    step(id);
  } catch (error) {
    await cardState?.close();
    cardState = undefined;
    if (id === runId) fail(error);
  }
}

function start(file, reboot = false) {
  const id = ++runId;
  clearTimeout(stopAt);
  input = [];
  keyDelay = 0;
  textKeyDeadline = undefined;
  touchDelay = 0;
  lastFrame = 0;
  nesKeys = undefined;
  recording = false;
  pendingLoad = pendingLoad.then(() => load(id, file, reboot));
}

onmessage = ({ data }) => {
  if (data.type === "load") {
    selectedImage = data.file;
    volatileSectors.clear();
    start(selectedImage);
  } else if (machine?.powered_off()) {
    if (data.type === "key" && data.code === 0x40 && data.down) start(selectedImage, true);
  } else if (data.type === "microphone") {
    if (recording) machine.submit_microphone(new Int16Array(data.samples));
  } else if (machine) {
    const last = input.at(-1);
    if (data.type === "touch" && data.phase === 5 && last?.type === "touch" && last.phase === 5) {
      input[input.length - 1] = data;
    } else {
      input.push(data);
    }
  }
};
