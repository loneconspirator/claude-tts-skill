#!/bin/bash
# @raycast.schemaVersion 1
# @raycast.title Stop Speaking
# @raycast.mode silent
# @raycast.packageName TTS
# @raycast.icon 🔇
# @raycast.description Stop TTS playback immediately and drop anything queued.
# @raycast.author mike

# Kill the clip that is playing and discard the rest of the queue. Safe to hit
# when nothing is speaking — the daemon just reports ok.
"$HOME/.claude/tts-venv/bin/python" \
  "$HOME/.claude/skills/tts/tts_enqueue.py" stop >/dev/null 2>&1

exit 0
