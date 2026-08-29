---
name: tts
description: Toggle text-to-speech output and configure voice/engine settings. Use when user says /tts with arguments like on, off, voice, speed, style, engine, or status.
user_invocable: true
argument-hint: "[on|off|voice|voices|browse|engine|speed|style|status|init|heal] --project --global"
---

# TTS Voice Control

> **SYNC NOTE:** This file, `speak.sh`, `list-voices.sh`, `browse-voices.sh`, and `adding-engines.md` all live in this folder and must be kept in sync. Engine changes touch all of them.

Manage text-to-speech for Claude's responses.

## Config Layering

Configs coalesce. Start from the global file, then overlay every
`.claude/tts-config.json` found walking up from the current directory, nearer
files winning key by key. A repo that sets only `voice` still inherits `speed`
from its parent directory.

- **Global**: `~/.claude/tts-config.json` — user-wide defaults, lowest precedence
- **Project**: `.claude/tts-config.json` — every one at or above the cwd, up to `$HOME`
- **Worktrees**: at the root of a linked git worktree, the main repo's root config is
  merged in directly beneath the worktree's own, then the walk continues up the
  worktree's ancestry. Only the repo root is consulted, not its ancestors — a
  worktree at `~/code/project-mybranch` is a sibling of `~/code/project`, so
  directory walking alone would never find the repo's config.

Precedence from `~/code/project-mybranch/src` (worktree of `~/code/project`),
highest first:

```
~/code/project-mybranch/src/.claude/tts-config.json
~/code/project-mybranch/.claude/tts-config.json
~/code/project/.claude/tts-config.json
~/code/.claude/tts-config.json
~/.claude/tts-config.json                            (the global file)
```

`python3 ~/.claude/skills/tts/tts_config.py --origin` prints the chain that
applies here and which file each value came from.

### Config fields

- `enabled` — boolean toggle
- `engine` — `"kokoro"` (default), `"qwen"`, or `"chatterbox"`
- `voice` — voice name (engine-specific, see below)
- `speed` — speech rate multiplier (works on Kokoro, ignored on Qwen and Chatterbox)
- `instruct` — style instruction (Qwen only)
- `api_url` — Qwen API base URL
- `exaggeration` — emotion intensity 0.0-1.0 (Chatterbox only, default 0.5)
- `cfg_weight` — style adherence 0.0-1.0 (Chatterbox only, default 0.5)
- `ref_audio` — path to reference audio .wav for voice cloning (Chatterbox only, default `~/.claude/tts-reference-voice.wav`)

## Engines

### Kokoro (default)
- Speed control **actually works** (0.5 to 2.0)
- 54 voices. Common ones: `af_heart`, `af_bella`, `af_nova`, `am_adam`, `am_michael`, `bf_emma`, `bm_george`
- The phonemizer follows the voice's language prefix (`af`/`am` en-us, `bf`/`bm` en-gb, `ef` es, `jf` ja, …). Japanese and Chinese need optional misaki packs — without them those voices fall back to en-us phonemes (`uv pip install --python ~/.claude/tts-venv/bin/python 'misaki[ja]'` installs one).
- Prefix meanings: `af_` = American female, `am_` = American male, `bf_` = British female, `bm_` = British male
- Runs via venv at `~/.claude/tts-venv/bin/python`
- No style/instruct support
- No external server needed — runs standalone

### Chatterbox
- **Exaggeration** (0.0-1.0): Emotion intensity — this actually works. 0.0 = flat, 0.5 = natural, 1.0 = very expressive
- **cfg_weight** (0.0-1.0): Classifier-free guidance / style adherence. Higher = more consistent but less natural
- Speed control: **not supported** (parameter ignored)
- No preset voices — uses **voice cloning** from a reference audio file
- Default reference: `~/.claude/tts-reference-voice.wav` (generated from Kokoro bf_emma)
- Custom reference: set `ref_audio` in config to any .wav file path
- English only
- Runs via mlx_audio in the Pinokio Python environment (no server needed)
- Model: `mlx-community/Chatterbox-TTS-fp16` (downloaded on first use)

### Qwen
- Speed control broken (parameter ignored)
- Style instruct parameter available but inconsistent
- Voices: Aiden, Ryan, Vivian, Serena, Uncle_Fu, Dylan, Eric, Ono_Anna, Sohee
- Requires Qwen3-TTS-MLX-WebUI-Enhanced running in Pinokio

## First-run setup

If any TTS command fails because the environment isn't ready (no venv at `~/.claude/tts-venv`, missing `kokoro-mlx`, SSL errors talking to Hugging Face, `FileNotFoundError` on a voice safetensors, etc.), **read `SETUP.md` in this same folder and follow it**. That file contains a health check and ordered repair recipes. Do not load it during normal command handling — only when the environment is broken or `/tts on` is being run for the first time on this machine.

## Commands

Parse the user's arguments (available as `$ARGUMENTS`):

- `/tts on` — Set `enabled: true`. Confirm with a spoken test. If the spoken test errors out, fall through to `SETUP.md` before reporting failure.
- `/tts off` — Set `enabled: false`. Confirm silently.
- `/tts engine <kokoro|qwen|chatterbox>` — Switch TTS engine.
- `/tts voice <name>` — Set voice (see engine-specific voices above).
- `/tts voices` — List available voices for current engine.
- `/tts browse` — Interactive voice browser (Kokoro only): arrow through the voices and hear each one, then save it globally (`s`) or for this project (`p`). It takes over the terminal, so hand the user the command to run rather than running it yourself.
- `/tts speed <number>` — Set speed (0.5 to 2.0, Kokoro only).
- `/tts style <description>` — Set voice style instruction (Qwen only). Use `/tts style clear` to remove.
- `/tts exaggeration <number>` — Set emotion intensity 0.0-1.0 (Chatterbox only).
- `/tts cfg <number>` — Set style adherence / cfg_weight 0.0-1.0 (Chatterbox only).
- `/tts ref <path>` — Set reference audio .wav path for voice cloning (Chatterbox only).
- `/tts status` — Show effective config (merged) and which file each value comes from.
- `/tts init` - Create a project config (`.claude/tts-config.json`) with no settings (`{}`)
- `/tts heal` — Curate phonemizer misses into the dictionary. **Only when invoked**, read `~/.claude/skills/tts/CURATE_MISSES.md` and follow it. Do not load that file otherwise.
- `/tts` (no args) — Toggle enabled on/off.

Add `--global` or `--project` flag to target a specific config file. Default behavior:
- **Without flag**: writes to the nearest project config (`.claude/tts-config.json` at or above the cwd) if one exists, otherwise global (`~/.claude/tts-config.json`). Writes stay where the command was called — a worktree's setting is never written into its main repo.
- `/tts voice am_adam --global` — sets voice in global config
- `/tts engine qwen --project` — sets engine in project config

## Implementation

### Reading effective config

Run `python3 ~/.claude/skills/tts/tts_config.py --json` (or `--origin` to see
where each value came from). That script owns the upward walk, the worktree
splice, and the merge — do not reimplement the layering anywhere else.

### Writing config changes

1. Determine target file (see flag rules above)
2. Read the target file
3. Apply the change
4. Write it back

### Speaking

Use the Bash tool:

```bash
~/.claude/skills/tts/speak.sh "Text to speak"
```

`speak.sh` reads the effective TTS config automatically — no flags needed.

**Kokoro uses a queueing daemon** (`tts_daemon.py`) so multiple `speak.sh`
calls stream smoothly: each call returns immediately after enqueueing,
synthesis runs in the background, and clips play back in order. Feeding a
paragraph one sentence at a time produces a continuous read with no
per-sentence model reload. The daemon auto-starts on first use, keeps the
model in memory, and shuts itself down after 5 minutes idle.

Queue controls (use the venv python):

```bash
~/.claude/tts-venv/bin/python ~/.claude/skills/tts/tts_enqueue.py flush     # drop pending, finish current
~/.claude/tts-venv/bin/python ~/.claude/skills/tts/tts_enqueue.py stop      # flush + kill current playback
~/.claude/tts-venv/bin/python ~/.claude/skills/tts/tts_enqueue.py ping      # health check
~/.claude/tts-venv/bin/python ~/.claude/skills/tts/tts_enqueue.py shutdown  # stop the daemon
```

Set `TTS_NO_QUEUE=1` to bypass the daemon and synthesize inline (the
pre-queue behavior). Qwen and Chatterbox always run inline; only Kokoro
uses the queue.

### Listing voices

```bash
~/.claude/skills/tts/list-voices.sh
```

Reads the active engine from config and lists its available voices.

### Browsing voices by ear

```bash
~/.claude/skills/tts/browse-voices.sh
```

A curses browser over the Kokoro voices: moving the selection renders and
plays that voice, `s` saves it as the global default, `p` saves it for the
current project, `t` changes the sample text, `+`/`-` change speed. Each
voice introduces itself in English ("This is the voice of Alloy, American
Female"), since English is what the speak path hands it. Previews use the
same phonemizer the speak path would (see the Kokoro engine notes above), so
what you hear is what you get.

`p` appears only when a `.claude` directory exists at or above the current
directory; it writes `voice` into that directory's `tts-config.json`, creating
the file if this is the project's first setting. The header shows the global
and project voices side by side, and `*` marks the one in effect.

The mouse works too where the terminal reports it: click a voice to hear it,
click any bracketed button on the bottom row, scroll to move through the list.
Clicks go to the browser while it runs, so copying text off the screen needs
the usual modifier (Option in Terminal.app and iTerm2, Shift elsewhere).

This is an interactive full-screen program — tell the user to run it in their
terminal; do not launch it from a tool call.

## Ongoing Behavior

Speaking behavior is handled automatically by the `~/.claude/hooks/tts-inject.sh` SessionStart hook. When TTS is enabled, the hook injects the speak command into Claude's context at session start — no need to load this skill for that.
