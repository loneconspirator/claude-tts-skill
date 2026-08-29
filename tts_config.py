#!/usr/bin/env python3
"""Resolve effective TTS config: global defaults + every project config above cwd.

Single source of truth for config lookup. Every script that needs a setting
shells out to this rather than reimplementing the merge — there were six
copies before, which meant six places to change when the lookup rules moved.

Project config is found by walking up from the current directory collecting
every `.claude/tts-config.json`, so a setting applies from subdirectories and a
repo inherits whatever its parent directory sets without having to restate it.
Nearer files win key by key. The search stops at $HOME (or filesystem root) so
a stray config in a parent of your whole tree cannot leak past your home
directory.

Git worktrees splice in their main repo. A worktree at ~/code/project-mybranch
is a sibling of ~/code/project, not a child, so walking directories alone would
never see the repo's own config. When the walk reaches a linked worktree's
root, the main repo root's config is merged directly beneath it — the worktree's
own settings still win, and the walk then continues up the worktree's ancestry.
Only the main repo's root directory is consulted, not its ancestors.

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


def worktree_main_root(directory: Path) -> Path | None:
    """Main repo root, if `directory` is the root of a linked git worktree.

    A linked worktree's `.git` is a file reading `gitdir: <main>/.git/worktrees/<name>`,
    and that directory holds a `commondir` file pointing back at `<main>/.git`.
    Read those files directly rather than shelling out to git: this runs on
    every hook, once per ancestor, and a subprocess each time is a cost the
    lookup does not need to pay.

    Returns None for an ordinary repo, a submodule (its gitdir has no
    `commondir`), or a bare main repo (no working tree to hold a config).
    """
    dot_git = directory / ".git"
    try:
        if not dot_git.is_file():
            return None
        pointer = dot_git.read_text(errors="replace").strip()
    except OSError:
        return None
    if not pointer.startswith("gitdir:"):
        return None

    gitdir = Path(pointer[len("gitdir:"):].strip())
    if not gitdir.is_absolute():
        gitdir = directory / gitdir

    try:
        common = Path((gitdir / "commondir").read_text().strip())
    except OSError:
        return None
    if not common.is_absolute():
        common = gitdir / common
    try:
        common = common.resolve()
    except OSError:
        return None

    if common.name != ".git":
        return None
    return common.parent


def project_config_chain(start: Path | None = None) -> list[Path]:
    """Every project `.claude/tts-config.json` that applies at `start`, nearest first.

    Walks upward like git's .git discovery, but collects the whole chain rather
    than stopping at the first hit, and splices a linked worktree's main repo in
    directly below the worktree root. Stops after $HOME. The global config is
    excluded — `resolve` layers that in underneath the whole chain.
    """
    try:
        current = (start or Path.cwd()).resolve()
    except Exception:
        return []

    home = Path.home().resolve()
    try:
        global_cfg = GLOBAL_CFG.resolve()
    except OSError:
        global_cfg = GLOBAL_CFG

    chain: list[Path] = []
    seen: set[Path] = set()

    def collect(directory: Path) -> None:
        candidate = directory / PROJECT_REL
        try:
            if not candidate.is_file():
                return
            resolved = candidate.resolve()
        except OSError:
            return
        if resolved == global_cfg or resolved in seen:
            return
        seen.add(resolved)
        chain.append(candidate)

    for directory in [current, *current.parents]:
        collect(directory)
        main_root = worktree_main_root(directory)
        if main_root is not None:
            collect(main_root)
        if directory == home:
            break
    return chain


def find_project_config(start: Path | None = None) -> Path | None:
    """Nearest project config, the one whose settings beat every other file."""
    chain = project_config_chain(start)
    return chain[0] if chain else None


def _origin_label(path: Path) -> str:
    """Readable origin for a config: the directory it governs, $HOME abbreviated."""
    project_dir = path.parent.parent
    try:
        return "~/" + str(project_dir.relative_to(Path.home()))
    except ValueError:
        return str(project_dir)


def resolve(start: Path | None = None) -> tuple[dict, dict]:
    """Return (merged config, {key: origin}) where origin is default/global/project dir."""
    merged = dict(DEFAULTS)
    origin = {k: "default" for k in DEFAULTS}

    # Farthest first, so each nearer file overwrites what the last one set.
    layers = [(_load(GLOBAL_CFG), "global")]
    for path in reversed(project_config_chain(start)):
        layers.append((_load(path), _origin_label(path)))

    for value, label in layers:
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
        print(f"global:  {GLOBAL_CFG}")
        chain = project_config_chain()
        print("project: (none found)" if not chain else "project:")
        for i, path in enumerate(chain, 1):
            print(f"  {i}. {path}")
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
