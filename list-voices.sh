#!/bin/bash
# list-voices.sh — List available voices for the active TTS engine.
#
# SYNC NOTE: When adding engines to speak.sh, add a voice listing case here too.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"

# --- Read engine + kokoro_python from config ---
eval "$(python3 "$SCRIPT_DIR/tts_config.py" engine kokoro_python 2>/dev/null)"

engine="${ENGINE:-kokoro}"
KOKORO_VENV="${KOKORO_PYTHON:-$HOME/.claude/tts-venv/bin/python}"

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
