#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."
wasm-pack build crates/p36-web --target web --release --no-pack --out-dir ../../web/pkg -- --locked
cp emu/microphone.js web/pkg/p36_microphone.js
