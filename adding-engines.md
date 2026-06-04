# Adding New TTS Engines to /tts

> **SYNC NOTE:** This doc, `speak.sh`, and `SKILL.md` all live in `~/.claude/skills/tts/` and must be kept in sync. If you change an engine in `speak.sh`, update this doc and `SKILL.md` too — and vice versa.

Guide for adding a new TTS engine (e.g. Chatterbox) to the existing /tts system.

## Architecture Overview

Three files need changes:

1. **`~/.claude/skills/tts/speak.sh`** — the bash script that reads config, dispatches to the active engine, and plays audio
2. **`~/.claude/skills/tts/SKILL.md`** — the skill definition that tells Claude how to use /tts
3. **`~/.claude/tts-config.json`** — config schema (add any engine-specific fields)

## Current Setup

- **Venv**: `~/.claude/tts-venv/` (Python 3.12) — Kokoro is installed here
- **Config**: `~/.claude/tts-config.json` (global), `.claude/tts-config.json` (project override)
- **Config fields**: `enabled`, `engine`, `voice`, `speed`, `instruct`, `api_url`
- **Existing engines**: `kokoro` (standalone, venv), `qwen` (HTTP API via Pinokio)

## Step-by-Step: Adding a New Engine

### 1. Install the engine

Install into the existing venv (`~/.claude/tts-venv/`) if possible:

```bash
~/.claude/tts-venv/bin/pip install <package>
```

If it has conflicting deps with Kokoro, create a separate venv:

```bash
python3.12 -m venv ~/.claude/tts-venv-<engine>
~/.claude/tts-venv-<engine>/bin/pip install <package>
```

If it needs model weights from HuggingFace, download them and note the cache path. The Kokoro setup had to convert `.pt` voice files to `.safetensors` — check if similar conversion is needed.

### 2. Prototype the generation

Before touching speak.sh, get a working snippet that:
- Takes text input
- Generates audio (numpy array or wav bytes)
- Saves to a temp .wav file
- Plays via `afplay`

Test it standalone. Verify any engine-specific parameters actually work (speed, emotion, style, etc.). Document which params are real vs decorative.

### 3. Add to speak.sh

Read `~/.claude/skills/tts/speak.sh` first. The script structure is:

- **Config reading** — `read_config()` merges global + project JSON configs and outputs key=value pairs
- **Engine functions** — `speak_kokoro()`, `speak_qwen()`, etc. Each is a self-contained bash function
- **Dispatch** — a `case "$engine"` block at the bottom routes to the right function

**a)** If your engine needs new config fields, add them to the `read_config()` python snippet's key list and add a default below the `eval`:

```bash
# In read_config(), add to the for loop:
for k in ('engine', 'voice', 'speed', 'instruct', 'api_url', 'exaggeration'):

# Below the eval, add a default:
exaggeration="${exaggeration:-0.5}"
```

**b)** Add a `speak_<engine>()` function. Two patterns depending on how the engine runs:

- **Standalone (like Kokoro)** — call the venv python with an inline script:

```bash
speak_newengine() {
  "$NEWENGINE_VENV" -c "
import sys
# ... engine-specific imports and generation ...
# Write to temp wav and afplay it
" "$TEXT" "$voice" "$speed"
}
```

- **HTTP API (like Qwen)** — use curl to hit the API, decode the response, play it:

```bash
speak_newengine() {
  local body
  body=$(python3 -c "
import json
body = {'text': '''...',  'speed': $speed}
print(json.dumps(body))
")
  local response
  response=$(curl -s -X POST "${api_url}/api/endpoint" -H "Content-Type: application/json" -d "$body" --max-time 300)
  # decode and play...
}
```

**c)** Add the engine to the dispatch case block:

```bash
case "$engine" in
  kokoro)    speak_kokoro ;;
  qwen)      speak_qwen ;;
  newengine) speak_newengine ;;
  *)         echo "Unknown engine: $engine" >&2; exit 1 ;;
esac
```

### 4. Update config schema

Add any engine-specific fields to `~/.claude/tts-config.json`. Keep backward compatible — new fields should have sensible defaults. Example for Chatterbox:

```json
{
  "enabled": true,
  "engine": "chatterbox",
  "voice": "default",
  "speed": 1.0,
  "exaggeration": 0.5,
  "cfg_weight": 0.5,
  "instruct": "",
  "api_url": "http://127.0.0.1:42003"
}
```

### 5. Update the skill

Read `~/.claude/skills/tts/SKILL.md` first. Add:

**a)** New engine section under `## Engines`:

```markdown
### NewEngine
- Speed control: works/broken
- Style/emotion: describe what parameters exist and whether they work
- Voices: list them or describe how to list
- Dependencies: what's needed to run (venv, server, etc.)
```

**b)** New commands if needed (e.g. `/tts exaggeration 0.8`)

**c)** Update the config fields section

**d)** Update the engine choices in `/tts engine <...>`

### 6. Test end-to-end

```bash
# Direct test — temporarily edit config to use the new engine, then:
~/.claude/skills/tts/speak.sh "Testing new engine"

# Or temporarily hardcode the engine in the script for testing
```

## Chatterbox — ADDED 2026-04-21

Chatterbox is now integrated as the third engine. Implementation notes:

- **Python env**: Uses the Pinokio mlx-audio env (default: `~/pinokio/api/Qwen3-TTS-MLX-WebUI-Enhanced.git/app/env/bin/python3`, overridable via `chatterbox_python` in `tts-config.json`)
- **Model**: `mlx-community/Chatterbox-TTS-fp16` (auto-downloaded on first use, cached in `~/.cache/huggingface/hub/`)
- **Import**: `from mlx_audio.tts.models.chatterbox import Model` + `from mlx_audio.tts.generate import load_audio`
- **Key params that work**: `exaggeration` (0.0-1.0, emotion intensity), `cfg_weight` (0.0-1.0, style guidance)
- **Voice cloning**: Requires a reference .wav file — no preset voices. Default reference generated from Kokoro bf_emma at `~/.claude/tts-reference-voice.wav`
- **Speed**: Not supported (parameter ignored by the model)
- **Output**: 24kHz WAV, generator yields `GenerationResult` objects
- **Turbo variant**: `mlx-community/chatterbox-turbo-fp16` exists but has weight loading issues with current mlx_audio version (LSTM architecture mismatch). Also requires HF auth token. Skip for now.
- **Regular Chatterbox** (`Chatterbox-TTS-fp16`) has no `conds.safetensors` (no built-in voice), so `ref_audio` is always required

## Other Candidate Engines

- **Parler-TTS**: Natural language voice description ("speaks quickly with a warm tone"). Imprecise but interesting. PyTorch/MPS, not MLX-native.
- **Orpheus TTS**: Excellent emotion tags (`<laugh>`, `<sigh>`) but 3B params, poor Apple Silicon support.
- **F5-TTS**: Good voice cloning via MLX, but no speed/style params exposed.
