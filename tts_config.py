#!/usr/bin/env python3
"""Resolve effective TTS config: global defaults + nearest project override.

Single source of truth for config lookup. Every script that needs a setting
shells out to this rather than reimplementing the merge — there were six
copies before, which meant six places to change when the lookup rules moved.

Project config is found by walking up from the current directory to the first
`.claude/tts-config.json`, so it applies from subdirectories too, the way git
finds `.git`. The search stops at $HOME (or filesystem root) so a stray config
in a parent of your whole tree cannot leak into unrelated projects.

The global file is never treated as a project override, even when the cwd is
inside ~/.claude — otherwise ~/.claude/tts-config.json would shadow itself.

Usage:
    tts_config.py                       # all keys as KEY=value shell assignments
    tts_config.py voice engine          # only these keys
    tts_config.py --json                # merged config as JSON
    tts_config.py --origin              # each key with the file it came from

Shell:
    eval "$(python3 tts_config.py voice speed)"
    echo "$VOICE $SPEED"
"""
from __future__ import annotations

import json
import os
import shlex
import sys
from pathlib import Path

GLOBAL_CFG = Path.home() / ".claude" / "tts-config.json"
PROJECT_REL = Path(".claude") / "tts-config.json"

# Defaults live here so every caller agrees on them. A script that wants a
# different fallback can still override after eval'ing.
DEFAULTS: dict = {
    "enabled": False,
    "engine": "kokoro",
    "voice": "af_heart",
    "speed": 1.0,
    "instruct": "",
    "api_url": "http://127.0.0.1:42003",
    "exaggeration": 0.5,
    "cfg_weight": 0.5,
    "ref_audio": str(Path.home() / ".claude" / "tts-reference-voice.wav"),
    "auto_heal": False,
    "heal_model": "claude-haiku-4-5-20251001",
    "heal_max_words": 12,
    "summary_model": "claude-haiku-4-5-20251001",
    "summary_min_chars": 200,
    # Sessions whose cwd matches one of these stay silent. Probe and
    # background sessions run in a scratchpad directory and would
    # otherwise speak over the interactive session that spawned them.
    "mute_cwd_globs": ["*/scratchpad", "*/scratchpad/*"],
}


def _load(path: Path) -> dict:
    try:
        with open(path) as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def find_project_config(start: Path | None = None) -> Path | None:
    """Nearest .claude/tts-config.json at or above `start`.

    Walks upward like git's .git discovery. Stops after $HOME so a config in
    a shared parent directory does not silently apply to everything beneath it.
    """
    try:
        current = (start or Path.cwd()).resolve()
    except Exception:
        return None

    home = Path.home().resolve()
    for directory in [current, *current.parents]:
        candidate = directory / PROJECT_REL
        if candidate.is_file() and candidate.resolve() != GLOBAL_CFG.resolve():
            return candidate
        if directory == home:
            break
    return None


def resolve(start: Path | None = None) -> tuple[dict, dict]:
    """Return (merged config, {key: origin}) where origin is default/global/project."""
    merged = dict(DEFAULTS)
    origin = {k: "default" for k in DEFAULTS}

    for value, label in ((_load(GLOBAL_CFG), "global"),
                         (_load(p) if (p := find_project_config(start)) else {}, "project")):
        for k, v in value.items():
            merged[k] = v
            origin[k] = label

    return merged, origin


def _shell_key(key: str) -> str:
    return key.upper().replace("-", "_")


def main() -> None:
    args = sys.argv[1:]
    merged, origin = resolve()

    if "--json" in args:
        print(json.dumps(merged, indent=2, ensure_ascii=False))
        return

    if "--origin" in args:
        project = find_project_config()
        print(f"global:  {GLOBAL_CFG}")
        print(f"project: {project or '(none found)'}")
        for k in sorted(merged):
            print(f"  {k:20} = {str(merged[k]):32} ({origin[k]})")
        return

    keys = args or sorted(merged)
    for key in keys:
        if key not in merged:
            continue
        value = merged[key]
        if isinstance(value, bool):
            value = "yes" if value else "no"
        print(f"{_shell_key(key)}={shlex.quote(str(value))}")


if __name__ == "__main__":
    main()
