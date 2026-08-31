#!/bin/bash
# browse-voices.sh — interactive Kokoro voice browser (arrow through, hear each).
#
# Kokoro only. Other engines expose their voices through list-voices.sh.

set -euo pipefail

# Resolve $0 through any symlinks so SCRIPT_DIR is the real skill dir,
# not wherever the symlink lives (e.g. ~/.local/bin/voices).
SOURCE="$0"
while [[ -L "$SOURCE" ]]; do
  DIR="$(cd -P "$(dirname "$SOURCE")" && pwd)"
  SOURCE="$(readlink "$SOURCE")"
  [[ "$SOURCE" != /* ]] && SOURCE="$DIR/$SOURCE"
done
SCRIPT_DIR="$(cd -P "$(dirname "$SOURCE")" && pwd)"

eval "$(python3 "$SCRIPT_DIR/tts_config.py" engine kokoro_python 2>/dev/null)"

engine="${ENGINE:-kokoro}"
KOKORO_VENV="${KOKORO_PYTHON:-$HOME/.claude/tts-venv/bin/python}"

if [[ "$engine" != "kokoro" ]]; then
  echo "Voice browsing is kokoro-only; the active engine is '$engine'." >&2
  echo "Run: $SCRIPT_DIR/list-voices.sh" >&2
  exit 1
fi

exec "$KOKORO_VENV" "$SCRIPT_DIR/browse_voices.py" "$@"
