#!/bin/bash
# @raycast.schemaVersion 1
# @raycast.title Speak Selection
# @raycast.mode silent
# @raycast.packageName TTS
# @raycast.icon 🔊
# @raycast.description Copy the current selection and send it to the local TTS daemon.
# @raycast.author mike

set -euo pipefail

# Stash whatever the user had on the clipboard so we can restore it afterward.
ORIGINAL="$(pbpaste || true)"

# Trigger Cmd-C in the frontmost app, then wait for the pasteboard to actually
# change. Polling is more reliable than a fixed sleep — slow apps (Preview on
# big PDFs, web pages mid-render) sometimes take 200-300ms to deliver the copy.
osascript -e 'tell application "System Events" to keystroke "c" using {command down}'

TEXT=""
for _ in 1 2 3 4 5 6 7 8 9 10; do
  CURRENT="$(pbpaste || true)"
  if [ "$CURRENT" != "$ORIGINAL" ] && [ -n "${CURRENT// }" ]; then
    TEXT="$CURRENT"
    break
  fi
  sleep 0.05
done

# Fall back to whatever's on the clipboard if nothing changed (e.g. user
# already had the text copied, or the app blocked the keystroke).
if [ -z "$TEXT" ]; then
  TEXT="$(pbpaste || true)"
fi

if [ -z "${TEXT// }" ]; then
  echo "Nothing to speak"
  exit 0
fi

"$HOME/.claude/skills/tts/speak.sh" "$TEXT"

# Restore the original clipboard so we don't trash the user's copy buffer.
if [ -n "$ORIGINAL" ]; then
  printf %s "$ORIGINAL" | pbcopy
fi
