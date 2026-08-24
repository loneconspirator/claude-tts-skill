#!/bin/bash
# tts-notify-speak.sh — Notification hook: speak the notification text.
#
# Fires when Claude Code wants the user's attention: a permission prompt, a
# 60s idle timeout, or a subagent needing input. Unlike the Stop hook there is
# no reply to condense -- the payload carries a short, already-speakable
# string ("Claude needs your permission to use Bash"), so this speaks it
# directly. No model call, no latency.
#
# Deliberately does NOT touch the Stop hook's watermark. The two hooks speak
# different things and a notification must never mark reply text as spoken.

SKILL_DIR="$HOME/.claude/skills/tts"
LOG="/tmp/tts-stop-speak.log"

# Detach for the same reason the Stop hook does: speak.sh outlives the hook,
# and the harness reaps the hook's process group. Payload rides in argv
# because nohup points stdin at /dev/null.
# Resolve $0 before re-exec: nohup cannot find a relative path if the child
# is started from a different cwd, and the failure is silent.
SELF="$SKILL_DIR/$(basename "$0")"

if [ -z "${TTS_NOTIFY_DETACHED:-}" ]; then
  PAYLOAD="$(cat)"
  TTS_NOTIFY_DETACHED=1 nohup bash "$SELF" "$PAYLOAD" >/dev/null 2>&1 &
  exit 0
fi

PAYLOAD="${1:-}"

ENABLED="$(python3 -c "
import sys, os
sys.path.insert(0, os.path.expanduser('~/.claude/skills/tts'))
try:
    from tts_config import resolve
    cfg, _ = resolve()
    print('yes' if cfg.get('enabled', False) else 'no')
except Exception:
    print('no')
" 2>/dev/null)"

[ "$ENABLED" = "yes" ] || exit 0

MSG="$(printf %s "$PAYLOAD" | python3 -c "
import json, sys
try:
    d = json.load(sys.stdin)
except Exception:
    sys.exit(0)
# 'message' is the human-readable text; 'title' is a short category label.
print((d.get('message') or '').strip())
" 2>/dev/null)"

[ -n "${MSG// }" ] || exit 0

echo "$(date '+%H:%M:%S') notify: ${MSG}" >> "$LOG"
"$SKILL_DIR/speak.sh" "$MSG"
exit 0
