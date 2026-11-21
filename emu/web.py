#!/usr/bin/env python3
"""Serve the running P36's display, pointer input, and audio on localhost."""

import argparse
from collections import deque
from contextlib import ExitStack
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
from queue import Full, Queue
import sqlite3
from threading import Event, Lock, Thread
from time import monotonic, monotonic_ns
from urllib.parse import parse_qs, urlsplit

from unicorn import UcError

from amr import MIC_BLOCK_BYTES
from lcd import encode_png
from machine import Machine
from sd_state import open_sd_state
from touch import POINTER_MOVE


PAGE = Path(__file__).with_name("p36.html")
MICROPHONE_WORKLET = Path(__file__).with_name("microphone.js")
INSTRUCTION_SLICE = 240_000
KEY_TRANSITION_INTERVAL = 4_800_000
TEXT_KEY_IDLE_TIMEOUT = 1.5
POINTER_TRANSITION_INTERVAL = 4_800_000
POINTER_QUEUE_LIMIT = 64


class Screen:
    def __init__(self, image, trace, saved_sectors=None):
        self.machine = Machine(sd_image=image, trace=trace, saved_sectors=saved_sectors)
        self.machine.audio.mp3_clock = monotonic
        self.machine.media_clock_ns = monotonic_ns
        self.frame = (0, b"", False, None, None)
        self.error = None
        self.stop = Event()
        self.pointer_events = deque()
        self.pointer_lock = Lock()
        self.key_events = Queue(maxsize=64)

    def queue_pointer(self, x, y, phase, touch):
        event = (x, y, phase, touch)
        with self.pointer_lock:
            # Keep a slot available for release during a long drag.
            limit = POINTER_QUEUE_LIMIT - (phase == POINTER_MOVE)
            if len(self.pointer_events) < limit:
                self.pointer_events.append(event)
            elif (phase == POINTER_MOVE
                    and self.pointer_events[-1][2:] == (phase, touch)):
                self.pointer_events[-1] = event
            else:
                raise Full

    def run(self):
        last_scanout = None
        next_capture = 0
        pending_key = None
        key_deadline = 0
        pending_pointer = None
        key_delay = 0
        pointer_delay = 0
        try:
            while not self.stop.is_set():
                if key_delay <= 0 and pending_key is None and not self.key_events.empty():
                    pending_key = self.key_events.get_nowait()
                    key_deadline = monotonic() + TEXT_KEY_IDLE_TIMEOUT
                if pending_key is not None:
                    code, down = pending_key
                    typing = code in (0x1F, 0x22) or 0x30 <= code <= 0x39 or 0x41 <= code <= 0x5A
                    # File-backed text entry can miss a press while processing the previous character.
                    if (not down or not typing or self.machine.realtime_media
                            or self.machine.idle or monotonic() >= key_deadline):
                        self.machine.keypad.set_key(code, down)
                        pending_key = None
                        key_delay = KEY_TRANSITION_INTERVAL
                if pending_pointer is None:
                    with self.pointer_lock:
                        if self.pointer_events:
                            pending_pointer = self.pointer_events.popleft()
                if pending_pointer is not None and pointer_delay <= 0:
                    x, y, phase, touch = pending_pointer
                    if touch:
                        self.machine.touch.set_contact(x, y, phase)
                        pointer_delay = POINTER_TRANSITION_INTERVAL
                        pending_pointer = None
                    elif self.machine.post_pointer(x, y, phase):
                        if phase == 1:
                            # Let the guest consume the release before another contact.
                            pointer_delay = POINTER_TRANSITION_INTERVAL
                        pending_pointer = None
                self.machine.run(INSTRUCTION_SLICE)
                key_delay = max(0, key_delay - INSTRUCTION_SLICE)
                pointer_delay = max(0, pointer_delay - INSTRUCTION_SLICE)
                lcd = self.machine.lcd
                if lcd.frames == self.frame[0] or monotonic() < next_capture:
                    continue
                scanout = lcd.read_frame(self.machine.uc)
                if scanout is None:
                    continue
                video = lcd.video_enabled
                nes_keys = self.machine.nes_keys
                fast = video or nes_keys is not None
                movie = self.machine.movie_position
                if scanout != last_scanout or (video, nes_keys) != self.frame[2:4]:
                    self.frame = (lcd.frames, encode_png(*scanout, fast=fast), video, nes_keys, movie)
                    last_scanout = scanout
                else:
                    self.frame = (lcd.frames, self.frame[1], video, nes_keys, movie)
                next_capture = monotonic() + (0.035 if fast else 0.08)
        except (OSError, RuntimeError, UcError, sqlite3.Error) as error:
            self.error = str(error)
            print(f"ERR {error} pc=0x{self.machine.pc:08x}", flush=True)
        finally:
            if self.machine.block_budget:
                self.machine.block_budget.close()
            self.machine.nes_audio.close()
            self.machine.audio.amr.close()


class Handler(BaseHTTPRequestHandler):
    def send_body(self, status, content_type, body, sequence=None, volume=None, mp3=None,
                  video=False, nes_keys=None, recording=False, movie=None, audio_position=None):
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        if sequence is not None:
            self.send_header("X-Sequence", str(sequence))
        if volume is not None:
            self.send_header("X-Volume-Level", str(volume))
        if mp3 is not None:
            self.send_header("X-MP3-Track", str(mp3[0]))
            self.send_header("X-MP3-State", mp3[1])
        if video:
            self.send_header("X-Video", "1")
        if movie is not None:
            self.send_header("X-Video-Track", str(movie[0]))
            self.send_header("X-Video-PTS", str(movie[1]))
        if audio_position is not None:
            self.send_header("X-MP3-PTS", f"{audio_position:.3f}")
        if nes_keys:
            self.send_header("X-NES-Keys", nes_keys)
        if recording:
            self.send_header("X-Mic-Recording", "1")
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        request = urlsplit(self.path)
        if request.path == "/":
            self.send_body(200, "text/html; charset=utf-8", self.server.page)
        elif request.path == "/microphone.js":
            self.send_body(200, "text/javascript; charset=utf-8", self.server.microphone_worklet)
        elif request.path in ("/frame", "/audio"):
            screen = self.server.screen
            if screen.error:
                self.send_body(503, "text/plain; charset=utf-8", screen.error.encode())
                return
            if request.path == "/frame":
                sequence, data, video, nes_keys, movie = screen.frame
                after = parse_qs(request.query).get("after", ["0"])[0]
                if movie and after.isascii() and after.isdecimal() and len(after) < 20:
                    cursor = int(after)
                    for saved, picture, position in screen.machine.movie_startup_frames:
                        if 0 < cursor < saved <= sequence and position[0] == movie[0]:
                            sequence, movie = saved, position
                            data = encode_png(*picture, fast=True)
                            break
                content_type = "image/png"
            else:
                try:
                    after = int(parse_qs(request.query).get("after", ["0"])[0])
                except ValueError:
                    self.send_error(400)
                    return
                audio = screen.machine.audio
                stream, clip = audio.read_clip(after)
                if clip is None:
                    self.send_body(204, "application/octet-stream", b"", volume=audio.volume, mp3=stream)
                else:
                    sequence, content_type, data, track, position = clip
                    self.send_body(200, content_type, data, sequence, audio.volume,
                                   stream if track else None, audio_position=position)
                return
            recording = screen.machine.audio.amr.recording
            if not data or str(sequence) == after:
                self.send_body(204, content_type, b"", recording=recording)
            else:
                self.send_body(200, content_type, data, sequence, video=video,
                               nes_keys=nes_keys, recording=recording, movie=movie)
        else:
            self.send_error(404)

    def do_POST(self):
        if self.path == "/microphone":
            self.submit_microphone()
            return
        if self.path not in ("/pointer", "/key"):
            self.send_error(404)
            return
        screen = self.server.screen
        if screen.error or not screen.frame[1]:
            self.send_error(503)
            return
        if self.headers.get_content_type() != "application/json":
            self.send_error(415)
            return
        try:
            length = int(self.headers.get("Content-Length", "0"))
            if not 0 < length <= 128:
                raise ValueError
            event = json.loads(self.rfile.read(length))
            if self.path == "/key":
                code, down = event["code"], event["down"]
                if (type(code) is not int or code not in screen.machine.keypad.pins
                        or type(down) is not bool):
                    raise ValueError
                screen.key_events.put_nowait((code, down))
            else:
                x, y, phase = event["x"], event["y"], event["phase"]
                touch = event.get("touch", False)
                if (type(x) is not int or not 0 <= x < 800
                        or type(y) is not int or not 0 <= y < 480 or type(touch) is not bool
                        or type(phase) is not int or not 1 <= phase <= POINTER_MOVE
                        or (touch and phase not in (1, 2, POINTER_MOVE))):
                    raise ValueError
                screen.queue_pointer(x, y, phase, touch)
        except (KeyError, TypeError, ValueError):
            self.send_error(400)
            return
        except Full:
            self.send_error(503)
            return
        self.send_body(204, "text/plain", b"")

    def submit_microphone(self):
        if self.headers.get_content_type() != "application/octet-stream":
            self.send_error(415)
            return
        if self.headers.get("Content-Length") != str(MIC_BLOCK_BYTES):
            self.send_error(400)
            return
        block = self.rfile.read(MIC_BLOCK_BYTES)
        if len(block) != MIC_BLOCK_BYTES:
            self.send_error(400)
            return
        self.server.screen.machine.audio.amr.submit(block)
        self.send_body(204, "text/plain", b"")

    def log_message(self, format, *args):
        pass


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--sd-image", type=Path, required=True)
    parser.add_argument("--port", type=int, default=8036)
    parser.add_argument("--trace", action="store_true")
    saving = parser.add_mutually_exclusive_group()
    saving.add_argument("--sd-state", type=Path, help="save SD writes here (default: next to the image)")
    saving.add_argument("--temporary", action="store_true", help="discard SD writes when the emulator stops")
    args = parser.parse_args()

    with ExitStack() as resources:
        try:
            image = resources.enter_context(args.sd_image.open("rb"))
            server = resources.enter_context(ThreadingHTTPServer(("127.0.0.1", args.port), Handler))
            saved_sectors = None
            if not args.temporary:
                path = args.sd_state or args.sd_image.with_name(args.sd_image.name + ".state.sqlite3")
                saved_sectors = resources.enter_context(open_sd_state(image, path))
        except (OSError, ValueError, sqlite3.Error) as error:
            parser.exit(1, f"{error}\n")
        server.page = PAGE.read_bytes()
        server.microphone_worklet = MICROPHONE_WORKLET.read_bytes()
        server.screen = Screen(image, args.trace, saved_sectors)
        worker = Thread(target=server.screen.run)
        worker.start()
        print(f"P36 http://127.0.0.1:{server.server_port}/", flush=True)
        try:
            server.serve_forever()
        except KeyboardInterrupt:
            pass
        finally:
            server.screen.stop.set()
            worker.join()


if __name__ == "__main__":
    main()
