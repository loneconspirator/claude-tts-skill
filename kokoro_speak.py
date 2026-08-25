"""Kokoro TTS speak path with phonemizer-miss fallback.

The phonemizer follows the voice: Kokoro voices carry their language in the
name prefix, and a Spanish voice was trained on Spanish phonemes. When that
language needs an optional misaki pack that isn't installed (Japanese, Chinese)
we fall back to en-us and let the dictionary carry the pronunciation.

Strategy when a word phonemizes to nothing:
  1. Look it up in phonemizer-dict.json (user-curated substitutions).
     Values starting with "ipa:" are spliced in as raw phonemes,
     bypassing the phonemizer (e.g. "vars": "ipa:vˈɑrz").
  2. Try inserting hyphens between common compound morphemes.
  3. Spell it out letter-by-letter (last resort).
  4. Log unresolved misses to phonemizer-misses.log for later curation.
"""
from __future__ import annotations

import glob
import json
import os
import re
import subprocess
import sys
import tempfile
from pathlib import Path

import numpy as np
import soundfile as sf
from kokoro_mlx.generate import generate
from kokoro_mlx.model import KokoroModel
from kokoro_mlx.phonemize import Phonemizer, language_from_voice
from kokoro_mlx.voices import VoiceManager

SKILL_DIR = Path(__file__).parent
DICT_PATH = SKILL_DIR / "phonemizer-dict.json"
MISS_LOG = SKILL_DIR / "phonemizer-misses.log"

# Common morpheme boundaries to try when a compound word fails.
# Order matters — longer prefixes first so "subagent" hits "sub" before nothing.
COMPOUND_PREFIXES = [
    "sub", "super", "multi", "non", "pre", "post", "anti", "auto",
    "inter", "over", "under", "out", "up", "down", "back", "fore",
    "mid", "off", "on", "co", "re", "un", "de", "dis", "mis",
    "work", "play", "data", "meta", "micro", "macro", "mini",
    "main", "side", "cross",
]
COMPOUND_SUFFIXES = [
    "tree", "list", "set", "map", "table", "file", "name", "path",
    "type", "code", "data", "log", "size", "time", "line", "page",
    "agent", "test", "case", "step", "tool", "node", "link",
]


DEFAULT_LANGUAGE = "en-us"


def phonemizer_for(
    vocab: dict[str, int],
    voice: str,
    cache: dict[str, tuple[Phonemizer, str]] | None = None,
) -> tuple[Phonemizer, str]:
    """The phonemizer for a voice's own language, and the language it uses.

    Japanese and Chinese phonemization lives in optional misaki packs
    (`misaki[ja]`, `misaki[zh]`). When the pack is missing there is no
    language phonemizer to build, so this falls back to en-us — the voice
    then reads its text the way an English speaker would, which the
    dictionary and the miss-repair path can still be pointed at.

    The returned language is the one actually in use; compare it against
    `language_from_voice(voice)` to notice a fallback. Building a phonemizer
    costs a couple of seconds, so long-lived callers should pass a `cache`.
    """
    cache = {} if cache is None else cache
    requested = language_from_voice(voice)
    if requested in cache:
        return cache[requested]
    try:
        entry = (Phonemizer(vocab, requested), requested)
    except Exception:
        entry = cache.get(DEFAULT_LANGUAGE) or (
            Phonemizer(vocab, DEFAULT_LANGUAGE), DEFAULT_LANGUAGE
        )
        cache[DEFAULT_LANGUAGE] = entry
    cache[requested] = entry
    return entry


def load_dict() -> dict[str, str]:
    if not DICT_PATH.exists():
        return {}
    with open(DICT_PATH) as f:
        d = json.load(f)
    return {k.lower(): v for k, v in d.items() if not k.startswith("_")}


def log_miss(word: str, resolution: str) -> None:
    """Append unresolved word to miss log for later curation. Skip if already logged."""
    MISS_LOG.parent.mkdir(parents=True, exist_ok=True)
    key = word.lower()
    if MISS_LOG.exists():
        with open(MISS_LOG) as f:
            for line in f:
                logged = line.split("\t", 1)[0].strip().lower()
                if logged == key:
                    return
    with open(MISS_LOG, "a") as f:
        f.write(f"{word}\t{resolution}\n")


def is_empty_phonemes(phonemizer: Phonemizer, text: str) -> bool:
    """True if the text phonemizes to nothing (just BOS/EOS markers)."""
    chunks = phonemizer.phonemize_long(text)
    if not chunks:
        return True
    return all(not phonemes.strip() for phonemes, _ in chunks)


def try_compound_split(word: str, phonemizer: Phonemizer) -> str | None:
    """Try inserting a space at common compound boundaries.

    Returns the split version if it phonemizes to something non-empty,
    else None. Tries longest matches first.
    """
    lower = word.lower()
    # Try prefix splits: "worktree" -> "work tree"
    for prefix in sorted(COMPOUND_PREFIXES, key=len, reverse=True):
        if lower.startswith(prefix) and len(lower) > len(prefix) + 1:
            split = f"{word[:len(prefix)]} {word[len(prefix):]}"
            if not is_empty_phonemes(phonemizer, split):
                return split
    # Try suffix splits as a second pass
    for suffix in sorted(COMPOUND_SUFFIXES, key=len, reverse=True):
        if lower.endswith(suffix) and len(lower) > len(suffix) + 1:
            split = f"{word[:-len(suffix)]} {word[-len(suffix):]}"
            if not is_empty_phonemes(phonemizer, split):
                return split
    return None


def spell_out(word: str) -> str:
    """Last-resort fallback: hyphenate letters so espeak spells them."""
    return "-".join(word)


IPA_PREFIX = "ipa:"


def dict_entry(word: str, sub_dict: dict[str, str]) -> tuple[str, str] | None:
    """The curated substitution for `word` as a segment, or None.

    Returns (kind, value) where kind is "text" (substituted English to be
    phonemized normally) or "ipa" (raw IPA phonemes to splice directly).
    """
    sub = sub_dict.get(word.lower())
    if sub is None:
        return None
    if sub.startswith(IPA_PREFIX):
        return ("ipa", sub[len(IPA_PREFIX):])
    return ("text", sub)


def resolve_word(word: str, phonemizer: Phonemizer) -> tuple[str, str]:
    """Find a phonemizable replacement for a word that came back empty.

    Callers check the dict first, so by the time a word gets here it has no
    curated entry. Tries: compound split → spell out. Logs the resolution.
    """
    split = try_compound_split(word, phonemizer)
    if split is not None:
        log_miss(word, f"split:{split}")
        return ("text", split)
    spelled = spell_out(word)
    log_miss(word, f"spell:{spelled}")
    return ("text", spelled)


# Match a "word" — letters/digits/apostrophes. Punctuation is preserved untouched.
WORD_RE = re.compile(r"[A-Za-z][A-Za-z0-9']*")


def build_segments(
    text: str, phonemizer: Phonemizer
) -> list[tuple[str, str]]:
    """Split text into (kind, value) segments for the IPA-aware generator.

    kind is "text" (gets phonemized) or "ipa" (raw phonemes, spliced in).
    Segments are concatenated to form the final phoneme stream.
    """
    sub_dict = load_dict()
    segments: list[tuple[str, str]] = []
    pos = 0
    pending_text = ""

    def flush_text() -> None:
        nonlocal pending_text
        if pending_text:
            segments.append(("text", pending_text))
            pending_text = ""

    for match in WORD_RE.finditer(text):
        # Append any non-word chars between last position and this word
        pending_text += text[pos:match.start()]
        word = match.group(0)
        # The dict is an override, not just a repair for silent drops. espeak
        # resolves plenty of words to something confidently wrong — "AWS" read
        # as a word rather than three letters — and those never reach the miss
        # path, so a curated entry has to win before the phonemizer is asked.
        entry = dict_entry(word, sub_dict) or (
            resolve_word(word, phonemizer)
            if is_empty_phonemes(phonemizer, word)
            else None
        )
        if entry is None:
            pending_text += word
        elif entry[0] == "ipa":
            flush_text()
            segments.append(("ipa", entry[1]))
        else:
            pending_text += entry[1]
        pos = match.end()
    pending_text += text[pos:]
    flush_text()
    return segments


# Kokoro pads every utterance with near-silence: measured at ~280ms before the
# first phoneme and ~440ms after the last. Fed one sentence per call — which is
# how the clipboard reader and the daemon queue work — that is ~0.7s of dead air
# at every sentence boundary, on top of the gap the caller actually asked for.
# Trim it back to a short lead-in/lead-out so sentences run together.
TRIM_FRAME_MS = 10      # analysis frame
TRIM_THRESHOLD = 0.015  # fraction of peak amplitude that counts as speech
TRIM_KEEP_MS = 20       # silence left on each side after trimming
TRIM_FADE_MS = 5        # ramp at each cut so it doesn't click


def trim_silence(audio: np.ndarray, sample_rate: int = 24000) -> np.ndarray:
    """Drop the model's leading/trailing near-silence, leaving a short margin.

    Threshold is relative to the clip's own peak: Kokoro's "silence" is a low
    noise floor (~0.007 against a 0.35 peak), not true zero, so an absolute
    cutoff would either miss it or bite into quiet speech.
    """
    if not len(audio):
        return audio
    peak = float(np.abs(audio).max())
    if peak <= 0.0:
        return audio
    frame = max(1, sample_rate * TRIM_FRAME_MS // 1000)
    count = len(audio) // frame
    if count < 3:
        return audio
    loud = np.abs(audio[:count * frame].reshape(count, frame)).max(axis=1)
    voiced = loud > peak * TRIM_THRESHOLD
    if not voiced.any():
        return audio
    first = int(np.argmax(voiced))
    last = count - 1 - int(np.argmax(voiced[::-1]))
    keep = sample_rate * TRIM_KEEP_MS // 1000
    start = max(0, first * frame - keep)
    end = min(len(audio), (last + 1) * frame + keep)
    out = np.array(audio[start:end], dtype=np.float32)
    fade = min(sample_rate * TRIM_FADE_MS // 1000, len(out) // 2)
    if fade:
        ramp = np.linspace(0.0, 1.0, fade, dtype=np.float32)
        out[:fade] *= ramp
        out[-fade:] *= ramp[::-1]
    return out


def synthesize(
    segments: list[tuple[str, str]],
    model: KokoroModel,
    voice_manager: VoiceManager,
    voice: str,
    speed: float,
    phonemizer: Phonemizer,
) -> np.ndarray:
    """Build a single phoneme string from segments and run the model once.

    Falls back to the upstream generate() path if there are no IPA segments,
    so chunking on long inputs still works for plain text.
    """
    if not any(kind == "ipa" for kind, _ in segments):
        text = "".join(value for _, value in segments)
        return trim_silence(generate(
            text, model, model.config, voice_manager,
            voice=voice, speed=speed, phonemizer=phonemizer,
        ))

    parts: list[str] = []
    for kind, value in segments:
        if kind == "ipa":
            parts.append(value)
        else:
            chunks = phonemizer.phonemize_long(value)
            parts.extend(ph for ph, _ in chunks)
    phonemes = " ".join(p for p in parts if p)

    voice_array = voice_manager.load_voice(voice)
    token_count = sum(1 for c in phonemes if c in model.config.vocab) + 2
    style = voice_manager.get_style(voice_array, token_count)
    audio = model.forward(phonemes, style, speed)
    return trim_silence(np.array(audio.tolist(), dtype=np.float32))


def main() -> None:
    text, voice, speed = sys.argv[1], sys.argv[2], float(sys.argv[3])

    cache = os.path.expanduser(
        "~/.cache/huggingface/hub/models--mlx-community--Kokoro-82M-bf16/snapshots"
    )
    dirs = sorted(glob.glob(os.path.join(cache, "*")))
    model_path = dirs[-1] if dirs else "mlx-community/Kokoro-82M-bf16"

    model = KokoroModel.from_pretrained(model_path)
    vm = VoiceManager(model_path)
    phonemizer, language = phonemizer_for(model.config.vocab, voice)
    if language != language_from_voice(voice):
        print(
            f"kokoro: no {language_from_voice(voice)} phonemizer installed "
            f"(misaki extra missing); speaking {voice} as {language}",
            file=sys.stderr,
        )

    segments = build_segments(text, phonemizer)
    audio = synthesize(segments, model, vm, voice, speed, phonemizer)

    with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as f:
        sf.write(f.name, audio, 24000)
        subprocess.run(["afplay", f.name], check=True)


if __name__ == "__main__":
    main()
