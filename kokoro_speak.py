"""Kokoro TTS speak path with phonemizer-miss fallback.

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
from kokoro_mlx.phonemize import Phonemizer
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


def resolve_word(
    word: str, phonemizer: Phonemizer, sub_dict: dict[str, str]
) -> tuple[str, str]:
    """Find a phonemizable replacement for a word that came back empty.

    Returns (kind, value) where kind is "text" (substituted English to be
    phonemized normally) or "ipa" (raw IPA phonemes to splice directly).
    Tries: dict → compound split → spell out. Logs the resolution.
    """
    lower = word.lower()
    if lower in sub_dict:
        sub = sub_dict[lower]
        if sub.startswith(IPA_PREFIX):
            ipa = sub[len(IPA_PREFIX):]
            log_miss(word, f"ipa:{ipa}")
            return ("ipa", ipa)
        log_miss(word, f"dict:{sub}")
        return ("text", sub)
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
        if is_empty_phonemes(phonemizer, word):
            kind, value = resolve_word(word, phonemizer, sub_dict)
            if kind == "ipa":
                flush_text()
                segments.append(("ipa", value))
            else:
                pending_text += value
        else:
            pending_text += word
        pos = match.end()
    pending_text += text[pos:]
    flush_text()
    return segments


def synthesize(
    segments: list[tuple[str, str]],
    model: KokoroModel,
    voice_manager: VoiceManager,
    voice: str,
    speed: float,
) -> np.ndarray:
    """Build a single phoneme string from segments and run the model once.

    Falls back to the upstream generate() path if there are no IPA segments,
    so chunking on long inputs still works for plain text.
    """
    if not any(kind == "ipa" for kind, _ in segments):
        text = "".join(value for _, value in segments)
        return generate(
            text, model, model.config, voice_manager,
            voice=voice, speed=speed,
        )

    phonemizer = Phonemizer(model.config.vocab)
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
    return np.array(audio.tolist(), dtype=np.float32)


def main() -> None:
    text, voice, speed = sys.argv[1], sys.argv[2], float(sys.argv[3])

    cache = os.path.expanduser(
        "~/.cache/huggingface/hub/models--mlx-community--Kokoro-82M-bf16/snapshots"
    )
    dirs = sorted(glob.glob(os.path.join(cache, "*")))
    model_path = dirs[-1] if dirs else "mlx-community/Kokoro-82M-bf16"

    model = KokoroModel.from_pretrained(model_path)
    vm = VoiceManager(model_path)
    phonemizer = Phonemizer(model.config.vocab)

    segments = build_segments(text, phonemizer)
    audio = synthesize(segments, model, vm, voice, speed)

    with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as f:
        sf.write(f.name, audio, 24000)
        subprocess.run(["afplay", f.name], check=True)


if __name__ == "__main__":
    main()
