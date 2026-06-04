#!/usr/bin/env python3
"""Add validated substitutions to phonemizer-dict.json.

Usage:
    python update_dict.py word1=sub1 "word2=ipa:..." ...

    Each argument is a word=substitution pair. Keys are lowercased.
    The dict is kept alphabetically sorted (with _comment preserved at top).

Example:
    python update_dict.py pg="pee gee" yml="why em el" portainer="ipa:pɔːrˈteɪnər"
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

SKILL_DIR = Path(__file__).parent
DICT_PATH = SKILL_DIR / "phonemizer-dict.json"


def main() -> None:
    if len(sys.argv) < 2:
        print(f"Usage: {sys.argv[0]} word1=sub1 word2=sub2 ...", file=sys.stderr)
        sys.exit(2)

    new_entries: dict[str, str] = {}
    for arg in sys.argv[1:]:
        if "=" not in arg:
            print(f"Skipping malformed argument (expected word=sub): {arg!r}", file=sys.stderr)
            continue
        word, sub = arg.split("=", 1)
        new_entries[word.strip().lower()] = sub.strip()

    if not new_entries:
        print("No valid entries provided.", file=sys.stderr)
        sys.exit(2)

    with open(DICT_PATH) as f:
        raw: dict[str, str] = json.load(f)

    comment = raw.pop("_comment", None)

    added, skipped = [], []
    for word, sub in new_entries.items():
        if word in raw:
            skipped.append((word, raw[word], sub))
        else:
            raw[word] = sub
            added.append((word, sub))

    sorted_dict = dict(sorted(raw.items()))
    if comment is not None:
        sorted_dict = {"_comment": comment, **sorted_dict}

    with open(DICT_PATH, "w") as f:
        json.dump(sorted_dict, f, indent=2, ensure_ascii=False)
        f.write("\n")

    if added:
        print(f"Added {len(added)} entr{'y' if len(added) == 1 else 'ies'}:")
        for word, sub in added:
            print(f"  {word!r} → {sub!r}")

    if skipped:
        print(f"\nSkipped {len(skipped)} already-present key(s) (use --force to overwrite):")
        for word, old, new in skipped:
            print(f"  {word!r}: kept {old!r}, ignored {new!r}")


if __name__ == "__main__":
    main()
