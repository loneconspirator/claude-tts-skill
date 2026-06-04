#!/usr/bin/env python3
"""Print words from phonemizer-misses.log that are not yet in phonemizer-dict.json.

Usage:
    python diff_misses.py

Output (TSV to stdout):
    word    current_resolution
"""
from __future__ import annotations

import json
from pathlib import Path

SKILL_DIR = Path(__file__).parent
DICT_PATH = SKILL_DIR / "phonemizer-dict.json"
MISS_LOG  = SKILL_DIR / "phonemizer-misses.log"


def main() -> None:
    with open(DICT_PATH) as f:
        existing = {k.lower() for k in json.load(f) if not k.startswith("_")}

    seen: dict[str, str] = {}
    with open(MISS_LOG) as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            word, resolution = line.split("\t", 1)
            key = word.lower()
            if key not in existing:
                seen[key] = resolution  # last resolution wins if duped

    if not seen:
        print("Nothing to curate — all misses are already in the dict.")
        return

    print(f"{'WORD':<24} CURRENT RESOLUTION")
    print("-" * 60)
    for word, resolution in sorted(seen.items()):
        print(f"{word:<24} {resolution}")


if __name__ == "__main__":
    main()
