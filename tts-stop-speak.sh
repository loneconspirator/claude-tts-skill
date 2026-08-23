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

PAYLOAD="$(cat)"
SKILL_DIR="$HOME/.claude/skills/tts"
LOG="/tmp/tts-stop-speak.log"

# --- Config (global + project merge, project wins) ---
# summary_model / summary_prompt / summary_min_chars are read here so the
# condenser can be retargeted at a faster model without touching this script.
# Emitted as shell assignments and eval'd, same pattern as speak.sh.
# The heredoc is quoted ('PY') so the shell passes this through verbatim —
# the dict-merge braces and nested quotes below do not survive `python3 -c`.
eval "$(python3 <<'PY' 2>/dev/null
import json, os, shlex

g, p = {}, {}
try:
    with open(os.path.expanduser('~/.claude/tts-config.json')) as f:
        g = json.load(f)
except Exception:
    pass
try:
    with open('.claude/tts-config.json') as f:
        p = json.load(f)
except Exception:
    pass
cfg = {**g, **p}

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
