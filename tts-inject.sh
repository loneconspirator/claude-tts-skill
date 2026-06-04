#!/bin/bash
# tts-inject.sh — SessionStart hook: inject TTS speaking instructions if enabled
# Reads effective TTS config (global + project merge) and emits a concise
# system reminder with the exact speak command when TTS is on.

GLOBAL_CFG="$HOME/.claude/tts-config.json"
PROJECT_CFG=".claude/tts-config.json"

# Read global config (required)
if [ ! -f "$GLOBAL_CFG" ]; then
  exit 0
fi

ENABLED=$(python3 -c "
import json, sys

g, p = {}, {}
try:
    with open('$GLOBAL_CFG') as f: g = json.load(f)
except: pass
try:
    with open('$PROJECT_CFG') as f: p = json.load(f)
except: pass

cfg = {**g, **p}
if not cfg.get('enabled', False):
    sys.exit(0)

print('yes')
" 2>/dev/null)

if [ "$ENABLED" = "yes" ]; then
  echo 'TTS is enabled. At the end of your final response, use Bash to speak a concise conversational summary (no code, no paths, no markdown — just natural speech):'
  echo '~/.claude/skills/tts/speak.sh "Your summary here"'
fi

exit 0
