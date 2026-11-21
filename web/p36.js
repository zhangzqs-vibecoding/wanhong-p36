import { Mp3Playback } from "./mp3-playback.js";
import { MoviePlayback } from "./movie-playback.js";

const canvas = document.querySelector("#screen");
const screen = canvas.getContext("2d", { alpha: false });
const status = document.querySelector("#status");
const sound = document.querySelector("#sound");
const microphone = document.querySelector("#microphone");
const worker = new Worker(new URL("./worker.js", import.meta.url), { type: "module" });
const mp3 = new Mp3Playback(
  setVolume, message => { status.textContent = message; }, track => movie.displayedSeconds(track),
);
const movie = new MoviePlayback(paint, mp3, () => mp3.pump());
const navKeys = new Map([
  ["Escape", 0x2b], ["Home", 0x23], ["Enter", 0x21], ["Space", 0x20],
  ["ArrowUp", 0x1a], ["ArrowDown", 0x1b], ["ArrowLeft", 0x1c], ["ArrowRight", 0x1d],
  ["PageUp", 0x1a], ["PageDown", 0x1b], ["Backspace", 0x22], ["Delete", 0x1f],
  ["F1", 0x17], ["F2", 0x1e], ["Tab", 0x3a], ["CapsLock", 0x3c],
  ["ShiftLeft", 0x3e], ["ShiftRight", 0x3e],
  ["Minus", 0x2d], ["Equal", 0x3d], ["BracketLeft", 0x5b], ["BracketRight", 0x5d],
  ["Backslash", 0x5c], ["Semicolon", 0x3b], ["Quote", 0x27],
  ["Comma", 0x2c], ["Period", 0x2e], ["Slash", 0x2f],
]);
// Each controller's firmware table is ordered A, B, Select, Start, Up, Down, Left, Right.
const gameKeys = [
  "KeyZ", "KeyX", "ShiftRight", "Enter", "ArrowUp", "ArrowDown", "ArrowLeft", "ArrowRight",
  "KeyF", "KeyG", "KeyQ", "KeyE", "KeyW", "KeyS", "KeyA", "KeyD",
];
const gameControls = document.querySelector("#game-controls");
const pressed = new Map();
const virtualKeys = new Map();
let nesKeys;
let imageReady = false;
let persistent = false;
let pointerId;
let audio;
let gain;
let soundEnabled = false;
let recording = false;
let microphoneStream;
let microphoneContext;
let generation = 0;
let nextAudioTime = 0;
const sources = new Set();
let pendingAudio;

function loadImage(file) {
  if (!file) return;
  releaseKeys();
  nesKeys = undefined;
  gameControls.hidden = true;
  recording = false;
  imageReady = false;
  clearAudio();
  movie.clear();
  pendingAudio = undefined;
  screen.clearRect(0, 0, canvas.width, canvas.height);
  status.textContent = `正在读取 ${file.name}…`;
  worker.postMessage({ type: "load", file });
}

document.querySelector("#image").addEventListener("change", ({ target }) => loadImage(target.files[0]));
document.querySelector("#directory").addEventListener("change", ({ target }) => {
  const images = Array.from(target.files).filter(file => /\.(img|ima)$/i.test(file.name));
  const image = images.find(file => file.name.toLowerCase() === "p36-sd-content.img")
    ?? (images.length === 1 ? images[0] : undefined);
  if (image) loadImage(image);
  else status.textContent = images.length > 1 ? "目录中有多份镜像，请选择要加载的文件。" : "目录中没有找到 .img 或 .ima 镜像。";
});

function paint(width, height, pixels) {
  if (width !== canvas.width) canvas.width = width;
  if (height !== canvas.height) canvas.height = height;
  const image = screen.createImageData(width, height);
  for (let i = 0, j = 0; i < pixels.length; i += 2, j += 4) {
    const word = pixels[i] | pixels[i + 1] << 8;
    const r = (word >> 11) & 31;
    const g = (word >> 5) & 63;
    const b = word & 31;
    image.data[j] = r << 3 | r >> 2;
    image.data[j + 1] = g << 2 | g >> 4;
    image.data[j + 2] = b << 3 | b >> 2;
    image.data[j + 3] = 255;
  }
  screen.putImageData(image, 0, 0);
}

function clearClips() {
  generation += 1;
  nextAudioTime = 0;
  for (const source of sources) source.stop();
  sources.clear();
}

function clearAudio() {
  clearClips();
  mp3.clear();
}

function setVolume(volume) {
  gain.gain.setTargetAtTime(volume === 0 ? 0 : (volume * 9 + 10) / 190, audio.currentTime, 0.015);
}

let decode = Promise.resolve();

function playAudio(bytes, volume) {
  const id = generation;
  decode = decode.then(() => decodeAndSchedule(bytes, volume, id));
}

async function decodeAndSchedule(bytes, volume, id) {
  if (!soundEnabled || id !== generation) return;
  try {
    const wave = bytes[0] === 0x52 && bytes[1] === 0x49;
    const buffer = await audio.decodeAudioData(bytes.buffer);
    if (!soundEnabled || id !== generation) return;
    if (!wave) {
      mp3.clear();
      for (const source of sources) source.stop();
      sources.clear();
      nextAudioTime = 0;
    }
    const playback = audio.createBufferSource();
    playback.buffer = buffer;
    setVolume(volume);
    playback.connect(gain);
    playback.onended = () => sources.delete(playback);
    sources.add(playback);
    const start = Math.max(audio.currentTime + 0.040, nextAudioTime);
    playback.start(start);
    nextAudioTime = start + buffer.duration;
  } catch {
    status.textContent = "浏览器无法播放音频。";
  }
}

sound.addEventListener("click", async () => {
  soundEnabled = !soundEnabled;
  sound.textContent = soundEnabled ? "关闭声音" : "开启声音";
  sound.setAttribute("aria-pressed", String(soundEnabled));
  if (soundEnabled) {
    if (!audio) {
      audio = new AudioContext();
      gain = audio.createGain();
      gain.connect(audio.destination);
    }
    await audio.resume();
    if (!soundEnabled) return;
    mp3.setOutput(audio, gain);
    if (pendingAudio) {
      playAudio(pendingAudio.contents, pendingAudio.volume);
      pendingAudio = undefined;
    }
  } else {
    clearClips();
    mp3.setOutput();
  }
});

function stopMicrophone() {
  microphoneStream?.getTracks().forEach(track => track.stop());
  microphoneStream = undefined;
  if (microphoneContext && microphoneContext.state !== "closed") microphoneContext.close();
  microphoneContext = undefined;
  microphone.setAttribute("aria-pressed", "false");
}

microphone.addEventListener("click", async () => {
  microphone.disabled = true;
  try {
    if (microphoneStream) {
      stopMicrophone();
      microphone.textContent = "开启麦克风";
      return;
    }
    microphoneStream = await navigator.mediaDevices.getUserMedia({ audio: {
      channelCount: 1, echoCancellation: false, noiseSuppression: false, autoGainControl: false,
    } });
    microphoneContext = new AudioContext({ sampleRate: 8000 });
    await microphoneContext.audioWorklet.addModule(new URL("./pkg/p36_microphone.js", import.meta.url));
    const node = new AudioWorkletNode(microphoneContext, "p36-microphone");
    node.port.onmessage = ({ data }) => {
      if (recording) worker.postMessage({ type: "microphone", samples: data }, [data]);
    };
    microphoneContext.createMediaStreamSource(microphoneStream).connect(node);
    node.connect(microphoneContext.destination);
    await microphoneContext.resume();
    microphone.setAttribute("aria-pressed", "true");
    microphone.textContent = "关闭麦克风";
    canvas.focus({ preventScroll: true });
  } catch {
    stopMicrophone();
    microphone.textContent = "重试麦克风";
  } finally {
    microphone.disabled = false;
  }
});
window.addEventListener("pagehide", stopMicrophone);

worker.onmessage = ({ data }) => {
  switch (data.type) {
    case "frame":
      movie.receive(data);
      if (!imageReady) status.textContent = persistent ? "SD 卡已加载" : "SD 卡已加载，存档仅保留在本次运行。";
      imageReady = true;
      break;
    case "audio":
      if (soundEnabled) playAudio(data.contents, data.volume);
      else if (data.contents[0] !== 0x52) pendingAudio = data;
      break;
    case "mp3":
      if (data.state === "playing" && !data.contents.length) {
        clearClips();
        pendingAudio = undefined;
      }
      mp3.receive(data);
      break;
    case "nes-keys":
      releaseKeys();
      if (nesKeys && !data.keys) clearClips();
      nesKeys = data.keys;
      gameControls.hidden = !nesKeys;
      break;
    case "recording":
      recording = data.active;
      break;
    case "power-off":
      releaseKeys();
      nesKeys = undefined;
      gameControls.hidden = true;
      recording = false;
      pendingAudio = undefined;
      clearAudio();
      movie.clear();
      screen.clearRect(0, 0, canvas.width, canvas.height);
      imageReady = true;
      status.textContent = "已关机，按「电源」开机。";
      break;
    case "started":
      imageReady = false;
      persistent = data.persistent;
      status.textContent = "固件正在启动…";
      break;
    case "error":
      status.textContent = data.message;
      imageReady = false;
      recording = false;
      break;
  }
};
worker.onerror = event => { status.textContent = event.message; };

function sendKey(code, down) {
  for (const [button, resolveCode] of virtualKeys) {
    if (resolveCode() === code) button.dataset.pressed = String(down);
  }
  if (!imageReady) return;
  worker.postMessage({ type: "key", code, down });
}

function pressKey(source, code) {
  if (code === undefined || pressed.has(source)) return;
  const alreadyDown = Array.from(pressed.values()).includes(code);
  pressed.set(source, code);
  if (!alreadyDown) sendKey(code, true);
}

function releaseKey(source) {
  if (!pressed.has(source)) return;
  const code = pressed.get(source);
  pressed.delete(source);
  if (!Array.from(pressed.values()).includes(code)) sendKey(code, false);
}

function releaseKeys() {
  for (const source of pressed.keys()) releaseKey(source);
}

function keyCode(code) {
  const gameCode = nesKeys?.[gameKeys.indexOf(code)];
  if (gameCode !== undefined && gameCode !== 0xff) return gameCode;
  if (/^Key[A-Z]$/.test(code)) return code.charCodeAt(3);
  if (/^Digit[0-9]$/.test(code)) return code.charCodeAt(5);
  return navKeys.get(code);
}

canvas.addEventListener("keydown", event => {
  if (!imageReady || event.ctrlKey || event.metaKey || event.altKey) return;
  if (event.code === "Tab" && event.shiftKey) return;
  const code = keyCode(event.code);
  if (code === undefined) return;
  event.preventDefault();
  pressKey(event.code, code);
});
window.addEventListener("keyup", event => releaseKey(event.code));
canvas.addEventListener("blur", () => {
  for (const source of pressed.keys()) {
    if (typeof source === "string") releaseKey(source);
  }
});
window.addEventListener("blur", releaseKeys);

function sendTouch(event, phase) {
  const { left, top, width, height } = canvas.getBoundingClientRect();
  const x = Math.max(0, Math.min(799, Math.floor((event.clientX - left) * 800 / width)));
  const y = Math.max(0, Math.min(479, Math.floor((event.clientY - top) * 480 / height)));
  worker.postMessage({ type: "touch", x, y, phase });
}
canvas.addEventListener("pointerdown", event => {
  if (!imageReady || pointerId !== undefined || event.button !== 0) return;
  canvas.focus({ preventScroll: true });
  pointerId = event.pointerId;
  canvas.setPointerCapture(pointerId);
  sendTouch(event, 2);
});
canvas.addEventListener("pointermove", event => {
  if (event.pointerId === pointerId) sendTouch(event, 5);
});
for (const name of ["pointerup", "pointercancel", "lostpointercapture"]) {
  canvas.addEventListener(name, event => {
    if (event.pointerId !== pointerId) return;
    pointerId = undefined;
    sendTouch(event, 1);
  });
}

function addKeys(container, entries) {
  for (const entry of entries) {
    const [label, code, width] = typeof entry === "string" ? [entry, entry.charCodeAt(0)] : entry;
    const resolveCode = () => typeof code === "string" ? keyCode(code) : code;
    const button = document.createElement("button");
    button.type = "button";
    button.textContent = label;
    if (width) button.style.flexGrow = width;
    let held = false;
    button.addEventListener("pointerdown", event => {
      if (event.button !== 0 || held) return;
      // Keep canvas keyboard focus while a virtual button is held.
      event.preventDefault();
      held = true;
      button.setPointerCapture(event.pointerId);
      pressKey(button, resolveCode());
    });
    for (const name of ["pointerup", "pointercancel", "lostpointercapture"]) {
      button.addEventListener(name, () => {
        if (!held) return;
        held = false;
        releaseKey(button);
      });
    }
    button.addEventListener("click", event => {
      if (event.detail !== 0 || held) return;
      pressKey(button, resolveCode());
      releaseKey(button);
    });
    virtualKeys.set(button, resolveCode);
    container.append(button);
  }
}

addKeys(document.querySelector("#power"), [["电源", 0x40]]);
addKeys(document.querySelector("#shortcuts"), [
  ["课本点读", 0x24], ["同步辅导", 0x25], ["第二课堂", 0x26], ["万虹书苑", 0x29],
  ["工具宝箱", 0x28], ["休闲娱乐", 0x2a],
]);
addKeys(document.querySelector("#side-controls"), [
  ["向上翻页", 0x18], ["向下翻页", 0x19], ["音量＋", 0x13], ["音量−", 0x14],
]);
addKeys(document.querySelector("#directions"), [
  ["↑", "ArrowUp"], ["←", "ArrowLeft"], ["↓", "ArrowDown"], ["→", "ArrowRight"],
]);
addKeys(document.querySelector("#device-controls"), [
  ["主菜单", 0x23], ["返回", 0x2b], ["确定", 0x21],
]);
addKeys(document.querySelector("#mouse-buttons"), [
  ["左长键", 0x11], ["右长键", 0x10],
]);
addKeys(document.querySelector("#game-buttons"), [
  ["A", "KeyZ"], ["B", "KeyX"], ["选择", "ShiftRight"], ["开始", "Enter"],
]);
addKeys(document.querySelector("#game-buttons-two"), [
  ["↑", "KeyW"], ["←", "KeyA"], ["↓", "KeyS"], ["→", "KeyD"],
  ["A", "KeyF"], ["B", "KeyG"], ["选择", "KeyQ"], ["开始", "KeyE"],
]);
const keyboard = document.querySelector("#keyboard");
for (const row of [
  [["Esc", 0x2b], ..."1234567890", "-", "=", ["Backspace", 0x22, 1.8]],
  [["Tab", 0x3a, 1.4], ..."QWERTYUIOP", "[", "]", "\\"],
  [["Caps Lock", 0x3c, 1.8], ..."ASDFGHJKL", ";", "'", ["Enter", 0x21, 1.6]],
  [["Shift", 0x3e, 2.2], ..."ZXCVBNM", ",", ".", "/", ["背景音", 0x12], ["输入法", 0x15]],
  [["菜单", 0x23, 1.8], ["新建", 0x16], ["打开", 0x3f], ["保存", 0x1e],
    ["空格", 0x20, 4.5], ["删除", 0x1f], ["帮助", 0x17], ["PgUp", 0x18], ["↑", "ArrowUp"], ["PgDn", 0x19]],
  [["←", "ArrowLeft"], ["↓", "ArrowDown"], ["→", "ArrowRight"]],
]) {
  const section = document.createElement("div");
  section.className = "key-row";
  addKeys(section, row);
  keyboard.append(section);
}
