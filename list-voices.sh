#!/bin/bash
# list-voices.sh — List available voices for the active TTS engine.
#
# SYNC NOTE: When adding engines to speak.sh, add a voice listing case here too.

set -euo pipefail

GLOBAL_CFG="$HOME/.claude/tts-config.json"
PROJECT_CFG=".claude/tts-config.json"

# --- Read engine + kokoro_python from config ---
eval "$(python3 -c "
import json
g, p = {}, {}
try:
    with open('$GLOBAL_CFG') as f: g = json.load(f)
except: pass
try:
    with open('$PROJECT_CFG') as f: p = json.load(f)
except: pass
cfg = {**g, **p}
print(f'engine={cfg.get(\"engine\", \"kokoro\")}')
print(f'kokoro_python={cfg.get(\"kokoro_python\", \"\")}')
" 2>/dev/null)"

engine="${engine:-kokoro}"
KOKORO_VENV="${kokoro_python:-$HOME/.claude/tts-venv/bin/python}"

case "$engine" in
  kokoro)
    "$KOKORO_VENV" -c "
from kokoro_mlx.voices import VoiceManager
import glob, os
cache = os.path.expanduser('~/.cache/huggingface/hub/models--mlx-community--Kokoro-82M-bf16/snapshots')
dirs = sorted(glob.glob(os.path.join(cache, '*')))
model_path = dirs[-1] if dirs else 'mlx-community/Kokoro-82M-bf16'
vm = VoiceManager(model_path)
print('\n'.join(vm.list_voices()))
"
    ;;
  qwen)
    echo "Aiden"
    echo "Ryan"
    echo "Vivian"
    echo "Serena"
    echo "Uncle_Fu"
    echo "Dylan"
    echo "Eric"
    echo "Ono_Anna"
    echo "Sohee"
    ;;
  *)
    echo "Unknown engine: $engine" >&2
    exit 1
    ;;
esac
