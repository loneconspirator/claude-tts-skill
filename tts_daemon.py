"""TTS queueing daemon.

Owns a single Kokoro model in memory, accepts text snippets over a Unix
socket, and plays them back in order. Synthesis runs in parallel with
playback so a paragraph fed one sentence at a time streams smoothly.

Protocol (one JSON object per line on the socket, newline-terminated):
  {"cmd": "enqueue", "text": "...", "voice": "...", "speed": 1.0}
  {"cmd": "flush"}    -> drop everything pending
  {"cmd": "stop"}     -> stop current playback + flush
  {"cmd": "ping"}     -> reply {"ok": true}
  {"cmd": "shutdown"} -> exit daemon

Daemon exits on its own after IDLE_TIMEOUT seconds with nothing queued
and nothing playing, so it doesn't linger forever.
"""
from __future__ import annotations

import glob
import json
import os
import queue
import signal
import socket
import socketserver
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path

import numpy as np
import soundfile as sf

# Reuse the resolver/segment logic from the standalone script so behavior
# (dict lookups, compound splits, miss logging) stays identical.
SKILL_DIR = Path(__file__).parent
sys.path.insert(0, str(SKILL_DIR))
from kokoro_speak import build_segments, synthesize  # noqa: E402

from kokoro_mlx.model import KokoroModel  # noqa: E402
from kokoro_mlx.phonemize import Phonemizer  # noqa: E402
from kokoro_mlx.voices import VoiceManager  # noqa: E402

SOCKET_PATH = "/tmp/tts-daemon.sock"
PID_PATH = "/tmp/tts-daemon.pid"
LOG_PATH = "/tmp/tts-daemon.log"
IDLE_TIMEOUT = 300  # seconds with no work before the daemon shuts itself down


def log(msg: str) -> None:
    with open(LOG_PATH, "a") as f:
        f.write(f"{time.strftime('%H:%M:%S')} {msg}\n")


class State:
    def __init__(self) -> None:
        self.synth_q: queue.Queue = queue.Queue()
        self.play_q: queue.Queue = queue.Queue()
        # last time we observed real work (enqueue, synth, or playback)
        self.last_active = time.time()
        self.lock = threading.Lock()
        self.current_player: subprocess.Popen | None = None
        self.shutdown = threading.Event()

    def touch(self) -> None:
        with self.lock:
            self.last_active = time.time()

    def idle_seconds(self) -> float:
        with self.lock:
            return time.time() - self.last_active


def load_model() -> tuple[KokoroModel, VoiceManager, Phonemizer]:
    cache = os.path.expanduser(
        "~/.cache/huggingface/hub/models--mlx-community--Kokoro-82M-bf16/snapshots"
    )
    dirs = sorted(glob.glob(os.path.join(cache, "*")))
    model_path = dirs[-1] if dirs else "mlx-community/Kokoro-82M-bf16"
    model = KokoroModel.from_pretrained(model_path)
    vm = VoiceManager(model_path)
    phon = Phonemizer(model.config.vocab)
    return model, vm, phon


def synth_worker(
    state: State,
    model: KokoroModel,
    vm: VoiceManager,
    phon: Phonemizer,
) -> None:
    while not state.shutdown.is_set():
        try:
            job = state.synth_q.get(timeout=0.5)
        except queue.Empty:
            continue
        if job is None:
            break
        text, voice, speed = job["text"], job["voice"], job["speed"]
        state.touch()
        try:
            segments = build_segments(text, phon)
            audio = synthesize(segments, model, vm, voice, speed)
            f = tempfile.NamedTemporaryFile(
                suffix=".wav", delete=False, prefix="tts_daemon_"
            )
            sf.write(f.name, audio, 24000)
            f.close()
            state.play_q.put(f.name)
            log(f"synth OK ({len(text)} chars) -> {f.name}")
        except Exception as e:  # one bad sentence shouldn't kill the daemon
            log(f"synth ERROR: {e!r} for text={text!r}")
        finally:
            state.touch()


def play_worker(state: State) -> None:
    while not state.shutdown.is_set():
        try:
            path = state.play_q.get(timeout=0.5)
        except queue.Empty:
            continue
        if path is None:
            break
        state.touch()
        try:
            proc = subprocess.Popen(["afplay", path])
            with state.lock:
                state.current_player = proc
            proc.wait()
        except Exception as e:
            log(f"play ERROR: {e!r}")
        finally:
            with state.lock:
                state.current_player = None
            try:
                os.unlink(path)
            except OSError:
                pass
            state.touch()


def drain_queue(q: queue.Queue) -> list:
    drained = []
    while True:
        try:
            drained.append(q.get_nowait())
        except queue.Empty:
            break
    return drained


def handle_flush(state: State) -> None:
    """Drop everything pending. Currently-playing audio keeps playing."""
    drain_queue(state.synth_q)
    for path in drain_queue(state.play_q):
        try:
            os.unlink(path)
        except OSError:
            pass


def handle_stop(state: State) -> None:
    """Flush, plus kill whatever's playing right now."""
    handle_flush(state)
    with state.lock:
        proc = state.current_player
    if proc and proc.poll() is None:
        proc.terminate()


class Handler(socketserver.StreamRequestHandler):
    state: State  # set on the server class

    def handle(self) -> None:
        for raw in self.rfile:
            try:
                msg = json.loads(raw.decode("utf-8"))
            except json.JSONDecodeError:
                self.wfile.write(b'{"ok": false, "error": "bad json"}\n')
                continue
            cmd = msg.get("cmd")
            if cmd == "enqueue":
                self.state.synth_q.put({
                    "text": msg.get("text", ""),
                    "voice": msg.get("voice", "af_heart"),
                    "speed": float(msg.get("speed", 1.0)),
                })
                self.state.touch()
                self.wfile.write(b'{"ok": true}\n')
            elif cmd == "flush":
                handle_flush(self.state)
                self.wfile.write(b'{"ok": true}\n')
            elif cmd == "stop":
                handle_stop(self.state)
                self.wfile.write(b'{"ok": true}\n')
            elif cmd == "ping":
                self.wfile.write(b'{"ok": true}\n')
            elif cmd == "shutdown":
                self.wfile.write(b'{"ok": true}\n')
                self.state.shutdown.set()
                # nudge the server out of serve_forever
                threading.Thread(
                    target=self.server.shutdown, daemon=True
                ).start()
            else:
                self.wfile.write(b'{"ok": false, "error": "unknown cmd"}\n')


class Server(socketserver.ThreadingUnixStreamServer):
    daemon_threads = True
    allow_reuse_address = True


def idle_watchdog(state: State, server: Server) -> None:
    while not state.shutdown.is_set():
        time.sleep(5)
        # Don't time out while there's anything in flight
        busy = (
            not state.synth_q.empty()
            or not state.play_q.empty()
            or state.current_player is not None
        )
        if busy:
            state.touch()
            continue
        if state.idle_seconds() > IDLE_TIMEOUT:
            log(f"idle for {IDLE_TIMEOUT}s, shutting down")
            state.shutdown.set()
            threading.Thread(target=server.shutdown, daemon=True).start()
            return


def main() -> None:
    # Single-instance guard via PID file
    if os.path.exists(PID_PATH):
        try:
            with open(PID_PATH) as f:
                old = int(f.read().strip())
            os.kill(old, 0)
            print(f"daemon already running (pid {old})", file=sys.stderr)
            sys.exit(0)
        except (OSError, ValueError):
            pass  # stale pid file
    with open(PID_PATH, "w") as f:
        f.write(str(os.getpid()))

    if os.path.exists(SOCKET_PATH):
        os.unlink(SOCKET_PATH)

    log(f"daemon starting (pid {os.getpid()})")
    state = State()
    log("loading model...")
    model, vm, phon = load_model()
    log("model loaded")

    t_synth = threading.Thread(
        target=synth_worker, args=(state, model, vm, phon), daemon=True
    )
    t_play = threading.Thread(target=play_worker, args=(state,), daemon=True)
    t_synth.start()
    t_play.start()

    Handler.state = state
    server = Server(SOCKET_PATH, Handler)
    os.chmod(SOCKET_PATH, 0o600)

    t_watch = threading.Thread(
        target=idle_watchdog, args=(state, server), daemon=True
    )
    t_watch.start()

    def cleanup(*_):
        state.shutdown.set()
        try:
            server.shutdown()
        except Exception:
            pass

    signal.signal(signal.SIGTERM, cleanup)
    signal.signal(signal.SIGINT, cleanup)

    try:
        server.serve_forever()
    finally:
        log("daemon exiting")
        state.shutdown.set()
        try:
            os.unlink(SOCKET_PATH)
        except OSError:
            pass
        try:
            os.unlink(PID_PATH)
        except OSError:
            pass


if __name__ == "__main__":
    main()
