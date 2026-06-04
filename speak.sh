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

GLOBAL_CFG="$HOME/.claude/tts-config.json"
PROJECT_CFG=".claude/tts-config.json"
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

# --- Merge config (project overrides global) ---
read_config() {
  python3 -c "
import json, sys

g, p = {}, {}
try:
    with open('$GLOBAL_CFG') as f: g = json.load(f)
except: pass
try:
    with open('$PROJECT_CFG') as f: p = json.load(f)
except: pass

cfg = {**g, **p}
# Output as key=value for shell consumption
for k in ('engine', 'voice', 'speed', 'instruct', 'api_url', 'exaggeration', 'cfg_weight', 'ref_audio', 'kokoro_python', 'chatterbox_python'):
    print(f'{k}={cfg.get(k, \"\")}')
"
}

eval "$(read_config)"

engine="${engine:-kokoro}"
voice="${voice:-af_heart}"
speed="${speed:-1.0}"
instruct="${instruct:-}"
api_url="${api_url:-http://127.0.0.1:42003}"
exaggeration="${exaggeration:-0.5}"
cfg_weight="${cfg_weight:-0.5}"
ref_audio="${ref_audio:-$HOME/.claude/tts-reference-voice.wav}"
KOKORO_VENV="${kokoro_python:-$KOKORO_VENV_DEFAULT}"
CHATTERBOX_PYTHON="${chatterbox_python:-$CHATTERBOX_PYTHON_DEFAULT}"

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
