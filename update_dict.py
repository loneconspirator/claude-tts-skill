#!/usr/bin/env python3
"""Add validated substitutions to phonemizer-dict.json.

Usage:
    python update_dict.py [--force] word1=sub1 "word2=ipa:..." ...

    Each argument is a word=substitution pair. Keys are lowercased.
    An existing key is kept unless --force is passed, which replaces it.
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
        print(f"Usage: {sys.argv[0]} [--force] word1=sub1 word2=sub2 ...", file=sys.stderr)
        sys.exit(2)

    args = sys.argv[1:]
    force = "--force" in args
    args = [a for a in args if a != "--force"]

    new_entries: dict[str, str] = {}
    for arg in args:
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

    added, replaced, skipped = [], [], []
    for word, sub in new_entries.items():
        if word in raw and not force:
            skipped.append((word, raw[word], sub))
            continue
        if word in raw:
            replaced.append((word, raw[word], sub))
        else:
            added.append((word, sub))
        raw[word] = sub

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

    if replaced:
        print(f"Replaced {len(replaced)} entr{'y' if len(replaced) == 1 else 'ies'}:")
        for word, old, new in replaced:
            print(f"  {word!r}: {old!r} → {new!r}")

    if skipped:
        print(f"\nSkipped {len(skipped)} already-present key(s) (use --force to overwrite):")
        for word, old, new in skipped:
            print(f"  {word!r}: kept {old!r}, ignored {new!r}")


if __name__ == "__main__":
    main()
