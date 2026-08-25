#!/usr/bin/env python3
"""Decide whether TTS must stay silent for the session that fired a hook.

Probe and background sessions run in a per-session scratchpad directory. They
speak over the interactive session that spawned them, about work the user never
asked them for. Both TTS hooks consult this before doing anything else, and so
does Pi's tts-auto-speak extension, which lives outside this repo -- the rule
and its glob list stay in one place.

Reads a hook payload on stdin and matches its `cwd` against the config's
`mute_cwd_globs`. Exits 0 (printing the cwd) when muted, 1 otherwise. A payload
with no cwd falls back to this process's own cwd -- hooks run in the session's
directory, so the fallback is the same answer by another route, and the check
does not depend on the payload carrying the field.
"""
from __future__ import annotations

import fnmatch
import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from tts_config import resolve  # noqa: E402


def main() -> int:
    try:
        payload = json.load(sys.stdin)
    except Exception:
        payload = {}
    if not isinstance(payload, dict):
        payload = {}

    cwd = str(payload.get("cwd") or "").strip() or os.getcwd()

    # Resolve from the session's directory, not this process's, so a project
    # that adds its own mute globs is found from where the session actually is.
    try:
        globs = resolve(Path(cwd))[0].get("mute_cwd_globs")
    except Exception:
        return 1
    if not isinstance(globs, list):
        return 1

    for pattern in globs:
        if fnmatch.fnmatch(cwd, str(pattern)):
            print(cwd)
            return 0
    return 1


if __name__ == "__main__":
    sys.exit(main())
