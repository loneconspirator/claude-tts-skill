#!/usr/bin/env python3
"""browse_voices.py — interactive Kokoro voice browser.

Arrow through the voice list and hear each one as it is selected. The model
loads once in a background thread and every rendered sample is cached, so
re-selecting a voice replays instantly.

Previews go through the same phonemizer choice as the real speak path
(`kokoro_speak.phonemizer_for`): each voice's own language when that language
is installed, en-us otherwise. So what you hear here is what you will get.

Every voice reads the same English sample, since English is what the speak
path will hand it; `t` swaps in your own text.

Keys:
    ↑/↓, j/k, PgUp/PgDn, g/G   move
    ⏎ / space                  replay the selected voice
    a                          toggle auto-play on move
    s                          save selection as the global default voice
    t                          edit the sample text
    + / -                      speed up / down by 0.1
    /                          filter by name or language
    .                          stop playback
    q / Esc                    quit
"""
from __future__ import annotations

import curses
import glob
import json
import locale
import os
import shutil
import subprocess
import tempfile
import threading
from pathlib import Path

from kokoro_mlx.phonemize import language_from_voice
from kokoro_speak import phonemizer_for

GLOBAL_CFG = Path.home() / ".claude" / "tts-config.json"
MODEL_REPO = "mlx-community/Kokoro-82M-bf16"
CACHE_GLOB = os.path.expanduser(
    "~/.cache/huggingface/hub/models--mlx-community--Kokoro-82M-bf16/snapshots/*"
)

LANGUAGE_NAMES = {
    "a": "American English",
    "b": "British English",
    "e": "Spanish",
    "f": "French",
    "h": "Hindi",
    "i": "Italian",
    "j": "Japanese",
    "p": "Portuguese",
    "z": "Chinese",
}

# One English sample for every voice. The speak path reads Claude's English
# replies, so what a non-English voice does with English is the thing being
# auditioned — a Spanish line read by a Spanish voice tells you nothing about
# how it will sound in use.
SAMPLE = "Finished the refactor — three tests were failing, they all pass now."


# Japanese and Chinese need optional misaki extras; the rest ship with it.
MISAKI_EXTRAS = {"ja": "ja", "zh": "zh"}


def load_config() -> dict:
    try:
        with open(GLOBAL_CFG) as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def save_voice(voice: str) -> None:
    """Write `voice` into the global config, leaving every other key alone."""
    config = load_config()
    config["voice"] = voice
    GLOBAL_CFG.parent.mkdir(parents=True, exist_ok=True)
    with open(GLOBAL_CFG, "w") as f:
        json.dump(config, f, indent=2, ensure_ascii=False)
        f.write("\n")


def model_path() -> str:
    dirs = sorted(glob.glob(CACHE_GLOB))
    return dirs[-1] if dirs else MODEL_REPO


class Engine:
    """Loads Kokoro once, then renders and plays the most recent request.

    Requests supersede each other: moving down the list while a sample is
    still rendering abandons the old one rather than queueing a backlog you
    then have to sit through.
    """

    def __init__(self) -> None:
        self.lock = threading.Condition()
        self.pending: tuple[str, str, float] | None = None
        self.status = "loading model…"
        self.ready = False
        self.quitting = False
        self.proc: subprocess.Popen | None = None
        self.cache: dict[tuple[str, str, float], str] = {}
        self.tmpdir = tempfile.mkdtemp(prefix="kokoro-voices-")
        self.voices: list[str] = []
        self.unavailable: dict[str, str] = {}
        threading.Thread(target=self._run, daemon=True).start()

    def request(self, voice: str, text: str, speed: float) -> None:
        self._kill_playback()
        with self.lock:
            self.pending = (voice, text, speed)
            self.status = f"rendering {voice}…"
            self.lock.notify()

    def stop(self) -> None:
        self._kill_playback()
        with self.lock:
            self.pending = None
            self.status = "stopped"

    def shutdown(self) -> None:
        self._kill_playback()
        with self.lock:
            self.quitting = True
            self.lock.notify()
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def _kill_playback(self) -> None:
        proc = self.proc
        if proc is not None and proc.poll() is None:
            proc.terminate()

    def set_status(self, text: str) -> None:
        with self.lock:
            self.status = text

    def _run(self) -> None:
        import numpy as np
        import soundfile as sf
        from kokoro_mlx.generate import generate
        from kokoro_mlx.model import KokoroModel
        from kokoro_mlx.phonemize import Phonemizer
        from kokoro_mlx.voices import VoiceManager

        path = model_path()
        model = KokoroModel.from_pretrained(path)
        voice_manager = VoiceManager(path)
        phonemizers: dict[str, tuple[Phonemizer, str]] = {}

        with self.lock:
            self.voices = sorted(voice_manager.list_voices())
            self.ready = True
            self.status = "ready"

        while True:
            with self.lock:
                while self.pending is None and not self.quitting:
                    self.lock.wait()
                if self.quitting:
                    return
                voice, text, speed = self.pending
                self.pending = None

            key = (voice, text, speed)
            wav = self.cache.get(key)
            if wav is None:
                wanted = language_from_voice(voice)
                try:
                    if wanted not in phonemizers:
                        self.set_status(f"loading {wanted} phonemizer…")
                    phonemizer, language = phonemizer_for(
                        model.config.vocab, voice, phonemizers
                    )
                    if language != wanted:
                        # No misaki pack for this language — the speak path
                        # falls back to en-us too, so preview it that way.
                        self.unavailable[wanted] = MISAKI_EXTRAS.get(wanted, wanted)
                    self.set_status(f"rendering {voice}…")
                    audio = generate(
                        text, model, model.config, voice_manager,
                        voice=voice, speed=speed, phonemizer=phonemizer,
                    )
                    if len(audio) == 0:
                        self.set_status(f"{voice}: nothing to speak")
                        continue
                    wav = os.path.join(self.tmpdir, f"{voice}-{abs(hash(key)):x}.wav")
                    sf.write(wav, np.asarray(audio), 24000)
                    self.cache[key] = wav
                except Exception as exc:
                    self.set_status(f"{voice}: {type(exc).__name__}: {exc}"[:200])
                    continue

            with self.lock:
                # A newer request landed while this one rendered — drop it.
                if self.pending is not None:
                    continue
                self.status = f"playing {voice}"
            self.proc = subprocess.Popen(
                ["afplay", wav],
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            )
            self.proc.wait()
            with self.lock:
                if self.pending is None and self.status == f"playing {voice}":
                    self.status = "ready"


def describe(voice: str) -> tuple[str, str]:
    language = LANGUAGE_NAMES.get(voice[:1], "—")
    gender = {"f": "female", "m": "male"}.get(voice[1:2], "")
    return language, gender


def prompt(stdscr, label: str, initial: str = "") -> str | None:
    """Read a line at the bottom of the screen. Esc cancels, Enter accepts."""
    buffer = initial
    curses.curs_set(1)
    try:
        while True:
            height, width = stdscr.getmaxyx()
            stdscr.move(height - 1, 0)
            stdscr.clrtoeol()
            stdscr.addstr(height - 1, 0, f"{label}{buffer}"[: width - 1], curses.A_BOLD)
            stdscr.refresh()
            try:
                ch = stdscr.get_wch()
            except curses.error:  # timeout tick, nothing typed
                continue
            if ch == "\x1b":
                return None
            if ch in ("\n", "\r", curses.KEY_ENTER):
                return buffer
            if ch in ("\x7f", "\b", curses.KEY_BACKSPACE):
                buffer = buffer[:-1]
            elif isinstance(ch, str) and ch.isprintable():
                buffer += ch
    finally:
        curses.curs_set(0)


def run(stdscr) -> str | None:
    curses.curs_set(0)
    curses.use_default_colors()
    stdscr.timeout(120)

    config = load_config()
    speed = float(config.get("speed", 1.0))
    default_voice = config.get("voice", "af_heart")
    saved_voice: str | None = None
    custom_text: str | None = None
    autoplay = True
    filter_text = ""
    selected = 0
    top = 0
    playing: str | None = None

    engine = Engine()

    def visible() -> list[str]:
        needle = filter_text.lower()
        if not needle:
            return engine.voices
        return [
            v for v in engine.voices
            if needle in v.lower() or needle in describe(v)[0].lower()
        ]

    def sample_for() -> str:
        return custom_text or SAMPLE

    def play(voice: str) -> None:
        nonlocal playing
        playing = voice
        engine.request(voice, sample_for(), speed)

    initialized = False

    while True:
        voices = visible()
        if voices and not initialized:
            # Land on the voice currently in use so the first thing you hear
            # is what you already have.
            if default_voice in voices:
                selected = voices.index(default_voice)
            play(voices[selected])
            initialized = True
        if voices:
            selected = max(0, min(selected, len(voices) - 1))
        height, width = stdscr.getmaxyx()
        body_height = max(1, height - 4)
        if selected < top:
            top = selected
        elif selected >= top + body_height:
            top = selected - body_height + 1

        stdscr.erase()
        header = f" Kokoro voices ({len(voices)})"
        right = f"speed {speed:.1f}   default: {saved_voice or default_voice} "
        bar = header.ljust(max(0, width - 1 - len(right))) + right
        stdscr.addstr(0, 0, bar[: width - 1], curses.A_BOLD | curses.A_REVERSE)

        for row, index in enumerate(range(top, min(top + body_height, len(voices)))):
            voice = voices[index]
            language, gender = describe(voice)
            marker = "*" if voice == (saved_voice or default_voice) else " "
            extra = engine.unavailable.get(language_from_voice(voice))
            note = f"   en-us fallback — needs misaki[{extra}]" if extra else ""
            line = f" {marker} {voice:<16} {language:<18} {gender:<8}{note}"
            attr = curses.A_REVERSE if index == selected else curses.A_NORMAL
            stdscr.addstr(1 + row, 0, line[: width - 1].ljust(width - 1), attr)

        if not voices:
            stdscr.addstr(1, 0, "  no voices match that filter"[: width - 1])

        with engine.lock:
            status = engine.status
        if filter_text:
            status = f"{status}   [filter: {filter_text}]"
        sample = sample_for()
        keys = (
            " arrows move . enter replay . a autoplay:%s . s set default . "
            "t text . +/- speed . / filter . q quit" % ("on" if autoplay else "off")
        )
        stdscr.addstr(height - 3, 0, f' sample: "{sample}"'[: width - 1], curses.A_DIM)
        stdscr.addstr(height - 2, 0, f" {status}"[: width - 1], curses.A_BOLD)
        stdscr.addstr(height - 1, 0, keys[: width - 1], curses.A_DIM)
        stdscr.refresh()

        ch = stdscr.getch()
        if ch == -1:
            continue

        previous = selected
        if ch in (curses.KEY_DOWN, ord("j")):
            selected += 1
        elif ch in (curses.KEY_UP, ord("k")):
            selected -= 1
        elif ch == curses.KEY_NPAGE:
            selected += body_height
        elif ch == curses.KEY_PPAGE:
            selected -= body_height
        elif ch == ord("g"):
            selected = 0
        elif ch == ord("G"):
            selected = len(voices) - 1
        elif ch in (curses.KEY_ENTER, 10, 13, ord(" ")):
            if voices:
                play(voices[selected])
        elif ch == ord("a"):
            autoplay = not autoplay
        elif ch == ord("s"):
            if voices:
                saved_voice = voices[selected]
                save_voice(saved_voice)
                engine.set_status(f"saved {saved_voice} as the default voice")
        elif ch in (ord("+"), ord("="), ord("-"), ord("_")):
            step = 0.1 if ch in (ord("+"), ord("=")) else -0.1
            speed = max(0.5, min(2.0, round(speed + step, 1)))
            if voices:
                play(voices[selected])
        elif ch == ord("t"):
            answer = prompt(stdscr, "sample text: ", custom_text or "")
            if answer is not None:
                custom_text = answer.strip() or None
                if voices:
                    play(voices[selected])
        elif ch == ord("/"):
            answer = prompt(stdscr, "filter: ", filter_text)
            if answer is not None:
                filter_text = answer.strip()
                selected = 0
                top = 0
        elif ch == ord("."):
            engine.stop()
        elif ch in (ord("q"), 27):
            engine.shutdown()
            return saved_voice
        elif ch == curses.KEY_RESIZE:
            continue

        # The filter may have changed the list out from under `selected`, so
        # resolve it again before deciding what to play.
        voices = visible()
        if voices:
            selected = max(0, min(selected, len(voices) - 1))
            if autoplay and (selected != previous or voices[selected] != playing):
                play(voices[selected])


def main() -> None:
    locale.setlocale(locale.LC_ALL, "")
    saved = curses.wrapper(run)
    if saved:
        print(f"Default voice set to {saved} in {GLOBAL_CFG}")
    else:
        print("No change — default voice left as is.")


if __name__ == "__main__":
    main()
