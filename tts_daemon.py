"""TTS queueing daemon.

Owns a single Kokoro model in memory, accepts text snippets over a Unix
socket, and plays them back in order. Synthesis runs in parallel with
playback so a paragraph fed one sentence at a time streams smoothly.

Protocol (one JSON object per line on the socket, newline-terminated):
  {"cmd": "enqueue", "text": "...", "voice": "...", "speed": 1.0}
  {"cmd": "pause", "seconds": 0.2}  -> insert a silent gap (ordered with text)
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

try:
    import sounddevice as sd
except Exception:  # missing wheel / no PortAudio — fall back to afplay
    sd = None

# Reuse the resolver/segment logic from the standalone script so behavior
# (dict lookups, compound splits, miss logging) stays identical.
SKILL_DIR = Path(__file__).parent
sys.path.insert(0, str(SKILL_DIR))
from kokoro_speak import build_segments, phonemizer_for, synthesize  # noqa: E402
from tts_config import resolve  # noqa: E402

from kokoro_mlx.model import KokoroModel  # noqa: E402
from kokoro_mlx.phonemize import Phonemizer, language_from_voice  # noqa: E402
from kokoro_mlx.voices import VoiceManager  # noqa: E402

SOCKET_PATH = "/tmp/tts-daemon.sock"
PID_PATH = "/tmp/tts-daemon.pid"
LOG_PATH = "/tmp/tts-daemon.log"
IDLE_TIMEOUT = 300  # seconds with no work before the daemon shuts itself down

# Interpreter used for the out-of-process output-device query. sys.executable
# is wrong here: this venv's bin/python is a symlink to Homebrew's framework
# build, which rewrites sys.executable to the *base* interpreter. That base
# python has no sounddevice, so the query would fail every time, return None,
# and silently disable device-change detection (audio then plays into a stale
# stream after AirPods disconnect). Point at the venv binary explicitly.
_VENV_PYTHON = Path.home() / ".claude" / "tts-venv" / "bin" / "python"
DEVICE_QUERY_PYTHON = str(_VENV_PYTHON if _VENV_PYTHON.exists() else sys.executable)


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
        self.playing = False
        self.stop_current = threading.Event()
        self.shutdown = threading.Event()

    def touch(self) -> None:
        with self.lock:
            self.last_active = time.time()

    def idle_seconds(self) -> float:
        with self.lock:
            return time.time() - self.last_active


def load_model() -> tuple[KokoroModel, VoiceManager, dict[str, tuple[Phonemizer, str]]]:
    cache = os.path.expanduser(
        "~/.cache/huggingface/hub/models--mlx-community--Kokoro-82M-bf16/snapshots"
    )
    dirs = sorted(glob.glob(os.path.join(cache, "*")))
    model_path = dirs[-1] if dirs else "mlx-community/Kokoro-82M-bf16"
    model = KokoroModel.from_pretrained(model_path)
    vm = VoiceManager(model_path)
    # Phonemizers are per-language and cost a couple of seconds to build, so
    # they are cached across jobs and warmed for the configured voice here —
    # a job arriving later in another language just adds an entry.
    phonemizers: dict[str, tuple[Phonemizer, str]] = {}
    phonemizer_for(model.config.vocab, resolve()[0].get("voice", "af_heart"), phonemizers)
    return model, vm, phonemizers


def synth_worker(
    state: State,
    model: KokoroModel,
    vm: VoiceManager,
    phonemizers: dict[str, tuple[Phonemizer, str]],
) -> None:
    while not state.shutdown.is_set():
        try:
            job = state.synth_q.get(timeout=0.5)
        except queue.Empty:
            continue
        if job is None:
            break
        state.touch()
        try:
            if job.get("kind") == "pause":
                seconds = max(0.0, float(job.get("seconds", 0.0)))
                state.play_q.put(("pause", seconds))
                log(f"pause {seconds:.2f}s queued")
            else:
                text, voice, speed = job["text"], job["voice"], job["speed"]
                phon, language = phonemizer_for(model.config.vocab, voice, phonemizers)
                if language != language_from_voice(voice):
                    log(f"no {language_from_voice(voice)} phonemizer; "
                        f"speaking {voice} as {language}")
                segments = build_segments(text, phon)
                audio = synthesize(segments, model, vm, voice, speed, phon)
                state.play_q.put(("audio", np.asarray(audio, dtype=np.float32)))
                log(f"synth OK ({len(text)} chars, {len(audio) / 24000:.2f}s)")
        except Exception as e:  # one bad sentence shouldn't kill the daemon
            log(f"synth ERROR: {e!r} for job={job!r}")
        finally:
            state.touch()


PLAY_BLOCK = 4800  # 0.2s at 24kHz — stop-check granularity


def play_worker(state: State) -> None:
    """Play queued clips through one persistent output stream.

    A fresh afplay process per clip costs ~1.5-2s of dead air each (device
    open/close), which turned the 0.1-0.3s inter-sentence pauses into
    multi-second gaps. Writing samples into a single long-lived stream is
    gapless; pauses become zero-fill of exactly the requested length.
    Falls back to the old afplay-per-clip path if sounddevice is missing.
    """
    stream = None
    stream_device = None

    def current_output_device():
        """Name of the current default output, or None if unknown.

        AirPods (and any Bluetooth output) disconnecting or reconnecting
        changes the default device underneath us. A stream opened against the
        old device does not necessarily raise on write — it can silently
        swallow audio — so compare devices rather than waiting for an error.

        Asked via a short-lived subprocess: refreshing PortAudio's device list
        in-process requires _terminate()/_initialize(), which invalidates the
        pointer of any stream we already hold ("Invalid stream pointer").
        """
        try:
            out = subprocess.run(
                [
                    DEVICE_QUERY_PYTHON, "-c",
                    "import sounddevice as sd;"
                    "d = sd.query_devices(kind='output');"
                    "print(d['name'])",
                ],
                capture_output=True, text=True, timeout=5,
            )
            name = out.stdout.strip()
            return name or None
        except Exception:
            return None

    def ensure_stream(check_device: bool):
        nonlocal stream, stream_device
        # Querying the device costs ~160ms, which would stall the 0.1-0.3s
        # inter-sentence gaps this stream exists to keep tight. A device can
        # only change while nothing is playing, so the caller only asks for a
        # check when starting a fresh utterance after an idle gap.
        device = current_output_device() if check_device else stream_device
        # Only act on a *known* change: a failed lookup (None) must not force a
        # needless reopen, and must not be recorded as the stream's device.
        if stream is not None and device is not None and device != stream_device:
            log(f"output device changed {stream_device} -> {device}, reopening")
            try:
                stream.close()
            except Exception:
                pass
            stream = None
        if stream is None:
            stream = sd.OutputStream(
                samplerate=24000, channels=1, dtype="float32"
            )
            stream.start()
            stream_device = device
        return stream

    # An empty queue means playback has stopped, which is the only window in
    # which the output device can change. Re-check the device on the next clip
    # after any such gap; mid-utterance clips skip the check and stay gapless.
    was_idle = True

    while not state.shutdown.is_set():
        try:
            item = state.play_q.get(timeout=0.5)
        except queue.Empty:
            was_idle = True
            continue
        if item is None:
            break
        state.touch()
        kind, payload = item
        if kind == "pause":
            audio = np.zeros(int(payload * 24000), dtype=np.float32)
        else:
            audio = payload
        if not len(audio):
            continue
        state.stop_current.clear()
        with state.lock:
            state.playing = True
        try:
            if sd is None:
                _play_afplay(state, audio)
            else:
                s = ensure_stream(check_device=was_idle)
                was_idle = False
                for i in range(0, len(audio), PLAY_BLOCK):
                    if state.stop_current.is_set():
                        break
                    s.write(np.ascontiguousarray(audio[i:i + PLAY_BLOCK]))
        except Exception as e:
            log(f"play ERROR: {e!r}")
            if stream is not None:
                try:
                    stream.close()
                except Exception:
                    pass
                stream = None
                stream_device = None
        finally:
            with state.lock:
                state.playing = False
            state.touch()

    if stream is not None:
        try:
            stream.close()
        except Exception:
            pass


def _play_afplay(state: State, audio: np.ndarray) -> None:
    """Legacy playback: write a temp wav and shell out to afplay."""
    f = tempfile.NamedTemporaryFile(
        suffix=".wav", delete=False, prefix="tts_daemon_"
    )
    sf.write(f.name, audio, 24000)
    f.close()
    try:
        proc = subprocess.Popen(["afplay", f.name])
        with state.lock:
            state.current_player = proc
        proc.wait()
    finally:
        with state.lock:
            state.current_player = None
        try:
            os.unlink(f.name)
        except OSError:
            pass


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
    drain_queue(state.play_q)


def handle_stop(state: State) -> None:
    """Flush, plus kill whatever's playing right now."""
    handle_flush(state)
    with state.lock:
        playing = state.playing
        proc = state.current_player
    if playing:
        state.stop_current.set()
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
            elif cmd == "pause":
                self.state.synth_q.put({
                    "kind": "pause",
                    "seconds": float(msg.get("seconds", 0.0)),
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
            or state.playing
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
    model, vm, phonemizers = load_model()
    log("model loaded")

    t_synth = threading.Thread(
        target=synth_worker, args=(state, model, vm, phonemizers), daemon=True
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

        # Let the play worker close its OutputStream before the interpreter
        # exits. Both the worker's stream.close() and interpreter teardown
        # reach into PortAudio's C state; racing them dereferences a freed
        # pointer and segfaults, which macOS reports as "Python quit
        # unexpectedly" seconds after a clean shutdown. Unblock the worker
        # with a sentinel rather than waiting out its 0.5s queue timeout.
        try:
            state.play_q.put(None)
        except Exception:
            pass
        t_play.join(timeout=5)
        if t_play.is_alive():
            log("play worker did not exit within 5s")

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
