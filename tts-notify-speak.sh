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

# Probe and background sessions run in a scratchpad directory: they speak over
# the interactive session that spawned them, about work the user never asked
# them for. Checked before the config read and the condense call, so a muted
# session costs nothing.
if MUTED_CWD="$(printf %s "$PAYLOAD" | python3 "$SKILL_DIR/tts_cwd_muted.py")"; then
  echo "$(date '+%H:%M:%S') muted cwd, staying silent: $MUTED_CWD" >> "$LOG"
  exit 0
fi

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

# The 60s idle notification ("Claude is waiting for your input") carries no
# information — after each break it just says the same thing. The user asked
# that it stay silent; only actual events (permission prompts, subagent
# input) get spoken.
case "$MSG" in
  *"waiting for your input"*) exit 0 ;;
esac

echo "$(date '+%H:%M:%S') notify: ${MSG}" >> "$LOG"

# --- Talky: a stopped agent is the case this product exists for ---
# The message here is usually near-empty -- across 27 real events, 20 were the
# bare string "Claude needs your permission" with no tool and no arguments --
# so the hub reads the pane to find out what it actually wants. Inert unless
# talky_enabled is true.
if [ "$(python3 -c "
import sys, os
sys.path.insert(0, os.path.expanduser('~/.claude/skills/tts'))
try:
    from tts_config import resolve
    print('yes' if resolve()[0].get('talky_enabled') else 'no')
except Exception:
    print('no')
" 2>/dev/null)" = "yes" ] && [ -f "$HOME/code/talky-dev/talky/talky_send.py" ]; then
  if printf %s "$PAYLOAD" | python3 "$HOME/code/talky-dev/talky/talky_send.py" \
       --source notification "$MSG" 2>>"$LOG"; then
    echo "$(date '+%H:%M:%S') -> talky (notify)" >> "$LOG"
    exit 0
  fi
  echo "$(date '+%H:%M:%S') talky handoff failed, speaking locally" >> "$LOG"
fi

"$SKILL_DIR/speak.sh" "$MSG"
exit 0
