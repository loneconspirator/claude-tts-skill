#!/bin/bash
# tts-inject.sh — SessionStart hook: inject TTS speaking instructions if enabled
# Reads effective TTS config (global + project merge) and emits a concise
# system reminder with the exact speak command when TTS is on.

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"

eval "$(python3 "$SCRIPT_DIR/tts_config.py" enabled 2>/dev/null)"

if [ "${ENABLED:-no}" = "yes" ]; then
  echo 'TTS is enabled. At the end of your final response, use Bash to speak a concise conversational summary (no code, no paths, no markdown — just natural speech):'
  echo '~/.claude/skills/tts/speak.sh "Your summary here"'
fi

exit 0
