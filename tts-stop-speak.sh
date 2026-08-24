#!/bin/bash
# tts-stop-speak.sh — Stop hook: speak a condensed version of the response.
#
# Runs AFTER the response is complete, so nothing enters Claude's context:
# no injected instruction, no speak.sh calls in the transcript. Context cost
# when TTS is on is the same as when it is off — zero.
#
# Reads the hook payload on stdin, pulls the final assistant turn out of the
# transcript, sends it to a headless Haiku to strip code/paths/markdown, and
# pipes the result to speak.sh.

SKILL_DIR="$HOME/.claude/skills/tts"
LOG="/tmp/tts-stop-speak.log"

# Re-exec detached, then return immediately so the hook does not block the
# turn. The condense step costs several seconds — long enough that a hook
# timeout, or the harness reaping the hook's process group on exit, would kill
# it mid-flight. Backgrounding from settings.json with a trailing "&" is not
# enough: that child is still in the hook's process group.
#
# The payload moves in argv, not on a pipe: nohup points stdin at /dev/null,
# so a piped payload never reaches the child.
if [ -z "${TTS_STOP_DETACHED:-}" ]; then
  PAYLOAD="$(cat)"
  TTS_STOP_DETACHED=1 nohup "$0" "$PAYLOAD" >/dev/null 2>&1 &
  exit 0
fi

PAYLOAD="${1:-}"

# --- Config ---
# tts_config.py owns the merge and the walk up the directory tree. The prompt
# default lives here because it is specific to this hook, so this block calls
# resolve() directly rather than using the CLI.
eval "$(python3 <<'PY' 2>/dev/null
import shlex, sys
sys.path.insert(0, __import__('os').path.expanduser('~/.claude/skills/tts'))
from tts_config import resolve

cfg, _ = resolve()

DEFAULT_PROMPT = (
    "The text below is an AI coding assistant's most recent reply, about to "
    'be read aloud to the user who asked for it. Say what the assistant just '
    'did, found, or needs — as if telling them out loud. Do not summarize the '
    'text as a document, do not narrate it in the third person, and do not '
    "describe what the reply contains. Speak in the assistant's own voice, "
    'first person, present tense.\n\n'
    'Two or three sentences. Lead with the outcome or the next action. If '
    'there is a question or a thing for the user to do, that goes first and '
    'must survive.\n\n'
    'Spoken prose only: no code, no file paths, no command names, no '
    'markdown, no bullet points, no backticks, no version numbers or hashes. '
    'Never speak a filename or extension, not even a bare one: say "the '
    'config file" or "the settings", never "config dot json". Drop detail '
    'that only makes sense on screen.\n\n'
    'Output only the words to speak.'
)

out = {
    'ENABLED': 'yes' if cfg.get('enabled', False) else 'no',
    'SUMMARY_MODEL': cfg.get('summary_model', 'claude-haiku-4-5-20251001'),
    'SUMMARY_PROMPT': cfg.get('summary_prompt', DEFAULT_PROMPT),
    'SUMMARY_MIN_CHARS': str(cfg.get('summary_min_chars', 200)),
}
for k, v in out.items():
    print(f'{k}={shlex.quote(str(v))}')
PY
)"

[ "$ENABLED" = "yes" ] || exit 0

# Avoid recursion: a Stop hook that triggers another Stop sees this set.
if [ "$(printf %s "$PAYLOAD" | python3 -c "
import json, sys
try: print(json.load(sys.stdin).get('stop_hook_active', False))
except Exception: print(False)
" 2>/dev/null)" = "True" ]; then
  exit 0
fi

# --- Extract the full final assistant turn ---
# last_assistant_message in the payload holds only the trailing text block when
# a response is split by tool calls, so read the transcript instead and join
# every text block in the last assistant turn.
TEXT="$(printf %s "$PAYLOAD" | python3 -c "
import json, sys

try:
    payload = json.load(sys.stdin)
except Exception:
    sys.exit(0)

path = payload.get('transcript_path')
if not path:
    print(payload.get('last_assistant_message', ''))
    sys.exit(0)

try:
    lines = open(path).read().splitlines()
except Exception:
    print(payload.get('last_assistant_message', ''))
    sys.exit(0)

for ln in reversed(lines):
    try:
        d = json.loads(ln)
    except Exception:
        continue
    if d.get('type') != 'assistant':
        continue
    blocks = d.get('message', {}).get('content', [])
    texts = [c.get('text', '') for c in blocks if c.get('type') == 'text']
    if not texts:
        continue
    print('\n'.join(texts))
    break
" 2>/dev/null)"

# Nothing to say, or too short to be worth a model call and 8s of latency.
CHARS="${#TEXT}"
if [ "$CHARS" -lt "$SUMMARY_MIN_CHARS" ]; then
  exit 0
fi

# --- Condense to speech via headless Haiku ---
# Falls back to the raw text if the model call fails, so a transient error
# means slightly worse audio rather than silence.
SUMMARY="$(printf %s "$TEXT" | timeout 30 claude -p "$SUMMARY_PROMPT" \
  --model "$SUMMARY_MODEL" 2>>"$LOG")"

if [ -z "${SUMMARY// }" ]; then
  echo "$(date '+%H:%M:%S') $SUMMARY_MODEL failed, speaking raw text" >> "$LOG"
  SUMMARY="$TEXT"
fi

echo "$(date '+%H:%M:%S') ${CHARS}ch -> ${#SUMMARY}ch via $SUMMARY_MODEL" >> "$LOG"
"$SKILL_DIR/speak.sh" "$SUMMARY"

exit 0
