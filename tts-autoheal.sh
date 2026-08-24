#!/bin/bash
# tts-autoheal.sh — background curation of phonemizer misses.
#
# Invoked fire-and-forget by speak.sh after enqueueing. Looks for words the
# phonemizer could not resolve, asks a headless model for substitutions,
# validates each one, and writes the survivors to the dictionary.
#
# Entries added this way are recorded in phonemizer-autoheal.json so they can
# be reviewed or reverted later — a wrong guess is a silently wrong
# pronunciation, which is worse than the spelled-out fallback it replaces.
#
# Off unless auto_heal is true in tts-config.json.

SKILL_DIR="$HOME/.claude/skills/tts"
VENV_PYTHON="$HOME/.claude/tts-venv/bin/python"
LOG="/tmp/tts-autoheal.log"
LOCK="/tmp/tts-autoheal.lock"

# --- Config ---
# Read before the cd below: project config is found by walking up from the
# current directory, and cd'ing into the skill dir first would resolve against
# the skill's own tree rather than the caller's project.
eval "$(python3 "$SKILL_DIR/tts_config.py" auto_heal heal_model heal_max_words 2>/dev/null)"

# The curation helpers resolve the dict and miss log relative to themselves,
# but run from the skill dir so their own imports line up.
cd "$SKILL_DIR" || exit 0

[ "$AUTO_HEAL" = "yes" ] || exit 0

# --- Single-instance lock ---
# speak.sh fires this after every clip, so a multi-sentence read starts
# several at once. mkdir is atomic: whoever wins curates, the rest exit.
# A stale lock (crash, kill) is cleared after 10 minutes.
if [ -d "$LOCK" ]; then
  if [ -n "$(find "$LOCK" -maxdepth 0 -mmin +10 2>/dev/null)" ]; then
    rmdir "$LOCK" 2>/dev/null
  else
    exit 0
  fi
fi
mkdir "$LOCK" 2>/dev/null || exit 0
trap 'rmdir "$LOCK" 2>/dev/null' EXIT

# --- Anything to do? ---
# diff_misses.py prints a header plus one row per uncurated word, or a
# "Nothing to curate" line when the log and dict agree.
PENDING="$("$VENV_PYTHON" diff_misses.py 2>/dev/null | awk 'NR>2 && NF' | head -n "$HEAL_MAX_WORDS")"
[ -z "${PENDING// }" ] && exit 0
grep -q "Nothing to curate" <<<"$PENDING" && exit 0

WORDS="$(awk '{print $1}' <<<"$PENDING" | paste -sd, -)"
[ -z "${WORDS// }" ] && exit 0

echo "$(date '+%H:%M:%S') curating: $WORDS" >> "$LOG"

# --- Ask for substitutions ---
# Mirrors the guidance in CURATE_MISSES.md: homophones for acronyms and true
# compounds, IPA for anything that would otherwise be split mid-word (the
# phonemizer inserts an audible gap between space-separated tokens).
PROMPT='For each word below, give a substitution that makes an English text-to-speech phonemizer pronounce it correctly.

Rules, in priority order:
1. Acronyms and initialisms: spell the letter names, e.g. pg -> "pee gee", yml -> "why em el".
2. True compound words: split at the morpheme boundary, e.g. tarball -> "tar ball".
3. Everything else — derivatives, proper nouns, single semantic words — use IPA prefixed with "ipa:", e.g. validator -> "ipa:ˈvælɪdeɪtər". The phonemizer puts an audible gap between space-separated tokens, so never split one word into more than two tokens. "valid eight er" is wrong; use IPA.

Output one line per word, formatted exactly as:
word=substitution

No commentary, no numbering, no markdown. If you cannot pronounce a word confidently, omit it entirely rather than guessing.

Words:
'

RAW="$(printf '%s%s\n' "$PROMPT" "$(awk '{print $1}' <<<"$PENDING")" \
  | timeout 60 claude -p --model "$HEAL_MODEL" 2>>"$LOG")"

if [ -z "${RAW// }" ]; then
  echo "$(date '+%H:%M:%S') model returned nothing" >> "$LOG"
  exit 0
fi

# --- Validate, then write ---
# Never add an unvalidated entry: validate_sub.py exits non-zero when the
# phonemizer produces nothing for the substitution.
VALID=()
while IFS= read -r line; do
  case "$line" in *=*) ;; *) continue ;; esac
  word="${line%%=*}"
  sub="${line#*=}"
  word="$(tr -d '[:space:]' <<<"$word" | tr '[:upper:]' '[:lower:]')"
  sub="$(sed -e 's/^[[:space:]]*//' -e 's/[[:space:]]*$//' <<<"$sub")"
  [ -z "$word" ] && continue
  [ -z "${sub// }" ] && continue
  # Only accept words we actually asked about.
  grep -qx "$word" <<<"$(awk '{print tolower($1)}' <<<"$PENDING")" || continue
  if "$VENV_PYTHON" validate_sub.py "$word" "$sub" >/dev/null 2>&1; then
    VALID+=("$word=$sub")
  else
    echo "$(date '+%H:%M:%S') rejected (failed validation): $word=$sub" >> "$LOG"
  fi
done <<<"$RAW"

[ "${#VALID[@]}" -eq 0 ] && exit 0

"$VENV_PYTHON" update_dict.py "${VALID[@]}" >> "$LOG" 2>&1

# --- Record what was auto-added, for review ---
python3 - "${VALID[@]}" <<'PY' 2>/dev/null
import json, os, sys, datetime

path = os.path.expanduser('~/.claude/skills/tts/phonemizer-autoheal.json')

try:
    with open(path) as f:
        rec = json.load(f)
except Exception:
    rec = {}

stamp = datetime.datetime.now().isoformat(timespec='seconds')
for arg in sys.argv[1:]:
    if '=' not in arg:
        continue
    word, sub = arg.split('=', 1)
    rec.setdefault(word, {'substitution': sub, 'added': stamp})

with open(path, 'w') as f:
    json.dump(dict(sorted(rec.items())), f, indent=2, ensure_ascii=False)
    f.write('\n')
PY

echo "$(date '+%H:%M:%S') added ${#VALID[@]}: ${VALID[*]}" >> "$LOG"
exit 0
