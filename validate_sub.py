#!/usr/bin/env python3
"""Validate a proposed phonemizer-dict substitution.

Usage:
    python validate_sub.py <word> <substitution>

    <substitution> may be plain English or an IPA string prefixed with "ipa:".

Exit codes:
    0  — substitution is valid (produces non-empty phonemes, or is a non-empty IPA string)
    1  — substitution is invalid

Examples:
    python validate_sub.py pg "pee gee"
    python validate_sub.py portainer "ipa:pɔːrˈteɪnər"
"""
from __future__ import annotations

import glob
import os
import sys
from pathlib import Path

IPA_PREFIX = "ipa:"


def load_phonemizer():
    cache = os.path.expanduser(
        "~/.cache/huggingface/hub/models--mlx-community--Kokoro-82M-bf16/snapshots"
    )
    dirs = sorted(glob.glob(os.path.join(cache, "*")))
    model_path = dirs[-1] if dirs else "mlx-community/Kokoro-82M-bf16"

    from kokoro_mlx.model import KokoroModel
    from kokoro_mlx.phonemize import Phonemizer

    model = KokoroModel.from_pretrained(model_path)
    return Phonemizer(model.config.vocab)


def phonemizes_ok(phonemizer, text: str) -> bool:
    chunks = phonemizer.phonemize_long(text)
    return bool(chunks) and any(ph.strip() for ph, _ in chunks)


def main() -> None:
    if len(sys.argv) != 3:
        print(f"Usage: {sys.argv[0]} <word> <substitution>", file=sys.stderr)
        sys.exit(2)

    word, sub = sys.argv[1], sys.argv[2]

    if sub.startswith(IPA_PREFIX):
        ipa = sub[len(IPA_PREFIX):]
        ok = bool(ipa.strip())
        method = "ipa (non-empty check)"
    else:
        phonemizer = load_phonemizer()
        ok = phonemizes_ok(phonemizer, sub)
        method = "phonemizer"

    status = "✓ OK" if ok else "✗ FAILED"
    print(f"{status}  {word!r} → {sub!r}  [{method}]")
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
