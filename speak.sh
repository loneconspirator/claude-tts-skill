#!/bin/bash
# speak.sh — Read TTS config and speak text via the active engine.
# Usage: speak.sh "Text to speak"
#        echo "Text to speak" | speak.sh
#
# SYNC NOTE: When adding or modifying engines here, also update:
#   - adding-engines.md (in this same folder) — step-by-step guide
#   - SKILL.md (in this same folder) — engine list, config fields, commands

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"

KOKORO_VENV_DEFAULT="$HOME/.claude/tts-venv/bin/python"
CHATTERBOX_PYTHON_DEFAULT="$HOME/pinokio/api/Qwen3-TTS-MLX-WebUI-Enhanced.git/app/env/bin/python3"

# --- Read text from arg or stdin ---
TEXT="${1:-}"
if [ -z "$TEXT" ]; then
  TEXT="$(cat)"
fi
if [ -z "$TEXT" ]; then
  echo "No text provided" >&2
  exit 1
fi

# Strip markdown before any engine sees it. Every caller funnels through
# here, including direct speak.sh calls from the model (the SessionStart
# inject hook). Kokoro reads "*" as "asterisk" and "`" as "backtick"; Qwen
# and Chatterbox mangle URLs and hashes. The Stop hook's condense prompt
# handles most of this, but direct calls and condense fallbacks arrive raw.
TEXT="$(printf %s "$TEXT" | python3 "$SCRIPT_DIR/sanitize_for_speech.py")"

if [ -z "${TEXT// }" ]; then
  # Sanitizing removed everything (e.g. a reply that was only a URL or only
  # markdown punctuation). Nothing speakable remains.
  exit 0
fi

# --- Merge config (nearest project config overrides global) ---
# tts_config.py owns the lookup rules, including the walk up the directory
# tree. It emits uppercase KEY=value; the lowercase names below are what the
# rest of this script expects.
eval "$(python3 "$SCRIPT_DIR/tts_config.py" \
  engine voice speed instruct api_url exaggeration cfg_weight ref_audio \
  kokoro_python chatterbox_python 2>/dev/null)"

engine="${ENGINE:-kokoro}"
voice="${VOICE:-af_heart}"
speed="${SPEED:-1.0}"
instruct="${INSTRUCT:-}"
api_url="${API_URL:-http://127.0.0.1:42003}"
exaggeration="${EXAGGERATION:-0.5}"
cfg_weight="${CFG_WEIGHT:-0.5}"
ref_audio="${REF_AUDIO:-$HOME/.claude/tts-reference-voice.wav}"
KOKORO_VENV="${KOKORO_PYTHON:-$KOKORO_VENV_DEFAULT}"
CHATTERBOX_PYTHON="${CHATTERBOX_PYTHON:-$CHATTERBOX_PYTHON_DEFAULT}"

# --- Log what was spoken ---
# When speech comes out wrong the audio is gone, and the daemon log records
# only character counts. Keep the exact text alongside the settings that
# produced it, plus who called us, so a bad read can be traced back to its
# source (a hook, a Raycast trigger, a stale queue) after the fact.
#
# Rotated at 1MB to stay bounded. Set TTS_NO_LOG=1 to skip.
SPEAK_LOG="${TTS_SPEAK_LOG:-/tmp/tts-speak.log}"
if [ "${TTS_NO_LOG:-}" != "1" ]; then
  if [ -f "$SPEAK_LOG" ] && [ "$(wc -c <"$SPEAK_LOG" 2>/dev/null || echo 0)" -gt 1048576 ]; then
    mv -f "$SPEAK_LOG" "$SPEAK_LOG.1" 2>/dev/null || true
  fi
  # Parent process name: which script or hook invoked this call.
  _caller="$(ps -o comm= -p "$PPID" 2>/dev/null | tail -1 | sed 's/^-//')"
  _caller="${_caller:-unknown}"
  {
    printf '%s [%s] engine=%s voice=%s speed=%s cwd=%s chars=%s\n' \
      "$(date '+%Y-%m-%d %H:%M:%S')" "$_caller" "$engine" "$voice" "$speed" \
      "$PWD" "${#TEXT}"
    printf '  text: %s\n' "$TEXT"
  } >> "$SPEAK_LOG" 2>/dev/null || true
fi

# --- Kokoro: enqueue to the daemon so multiple calls stream smoothly ---
# Set TTS_NO_QUEUE=1 to bypass the daemon and synthesize inline (old behavior).
speak_kokoro() {
  if [ "${TTS_NO_QUEUE:-}" = "1" ]; then
    "$KOKORO_VENV" "$SCRIPT_DIR/kokoro_speak.py" "$TEXT" "$voice" "$speed"
  else
    "$KOKORO_VENV" "$SCRIPT_DIR/tts_enqueue.py" enqueue "$TEXT" \
      --voice "$voice" --speed "$speed" >/dev/null
  fi
}

# --- Qwen: hit Pinokio API ---
speak_qwen() {
  local body
  body=$(python3 -c "
import json
body = {
    'text': '''$( echo "$TEXT" | sed "s/'/'\\\\''/g" )''',
    'speaker': '$voice',
    'language': 'English',
    'speed': $speed,
    'response_format': 'base64'
}
instruct = '$instruct'
if instruct:
    body['instruct'] = instruct
print(json.dumps(body))
")

  local response
  response=$(curl -s -X POST "${api_url}/api/v1/custom-voice/generate" \
    -H "Content-Type: application/json" \
    -d "$body" \
    --max-time 300)

  # Decode base64 audio and play
  local tmpfile
  tmpfile=$(mktemp /tmp/tts_XXXXXX.wav)
  echo "$response" | python3 -c "
import json, base64, sys
data = json.load(sys.stdin)
sys.stdout.buffer.write(base64.b64decode(data['audio']))
" > "$tmpfile"

  afplay "$tmpfile"
  rm -f "$tmpfile"
}

# --- Chatterbox: call mlx_audio via Pinokio python ---
speak_chatterbox() {
  "$CHATTERBOX_PYTHON" -c "
import sys, os, tempfile, subprocess
import numpy as np
import soundfile as sf
from mlx_audio.tts.generate import load_audio
from mlx_audio.tts.models.chatterbox import Model

text = sys.argv[1]
ref_path = sys.argv[2]
exagg = float(sys.argv[3])
cfg_w = float(sys.argv[4])

if not os.path.exists(ref_path):
    print(f'Reference audio not found: {ref_path}', file=sys.stderr)
    print('Chatterbox needs a reference audio file for voice cloning.', file=sys.stderr)
    print('Generate one with: ~/.claude/tts-venv/bin/python -c \"...\" or set ref_audio in tts-config.json', file=sys.stderr)
    sys.exit(1)

m = Model.from_pretrained('mlx-community/Chatterbox-TTS-fp16')
ref_audio = load_audio(ref_path, m.sample_rate)

for result in m.generate(
    text,
    ref_audio=ref_audio,
    exaggeration=exagg,
    cfg_weight=cfg_w,
):
    audio = np.array(result.audio)
    with tempfile.NamedTemporaryFile(suffix='.wav', delete=False) as f:
        sf.write(f.name, audio, result.sample_rate)
        subprocess.run(['afplay', f.name], check=True)
        os.unlink(f.name)
" "$TEXT" "$ref_audio" "$exaggeration" "$cfg_weight"
}

# --- Dispatch ---
case "$engine" in
  kokoro)     speak_kokoro ;;
  qwen)       speak_qwen ;;
  chatterbox) speak_chatterbox ;;
  *)          echo "Unknown engine: $engine" >&2; exit 1 ;;
esac

# --- Auto-heal (opt-in) ---
# Curate any words the phonemizer just failed on. Detached and silent: this
# must never delay speech or surface output on the caller's stderr. The script
# no-ops immediately unless auto_heal is enabled, and takes a lock so the
# several calls a multi-sentence read produces collapse into one run.
if [ -x "$SCRIPT_DIR/tts-autoheal.sh" ]; then
  "$SCRIPT_DIR/tts-autoheal.sh" >/dev/null 2>&1 </dev/null &
  disown 2>/dev/null || true
fi
