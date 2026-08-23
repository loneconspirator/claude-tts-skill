#!/bin/bash
# @raycast.schemaVersion 1
# @raycast.title Speak Selection
# @raycast.mode silent
# @raycast.packageName TTS
# @raycast.icon 🔊
# @raycast.description Copy the current selection and send it to the local TTS daemon.
# @raycast.author mike

set -euo pipefail

SKILL_DIR="$HOME/.claude/skills/tts"
KOKORO_PYTHON="$HOME/.claude/tts-venv/bin/python"

# Raycast runs this in silent mode, so anything on stderr flashes past too
# fast to read. Mirror stderr into a log we can inspect after the fact.
LOG="/tmp/tts-raycast.log"
exec 2> >(tee -a "$LOG" >&2)
echo "=== run $(date '+%H:%M:%S') ===" >> "$LOG"

# Stash whatever the user had on the clipboard so we can restore it afterward.
ORIGINAL="$(pbpaste || true)"

# Write a sentinel onto the clipboard first. If Cmd-C lands, the app's copy
# overwrites it; if the sentinel survives, we know for certain the copy never
# happened — which text comparison alone cannot tell us, since an unchanged
# clipboard looks identical to a failed copy.
SENTINEL="__tts_copy_probe_$$__"

# Never leave the sentinel on the clipboard, no matter how we exit.
restore_clipboard() {
  if [ "$(pbpaste 2>/dev/null || true)" = "$SENTINEL" ]; then
    printf %s "$ORIGINAL" | pbcopy
  fi
}
trap restore_clipboard EXIT

printf %s "$SENTINEL" | pbcopy

# Trigger Cmd-C in the frontmost app, then wait for the pasteboard to actually
# change. Polling is more reliable than a fixed sleep — slow apps (Preview on
# big PDFs, web pages mid-render) sometimes take 200-300ms to deliver the copy.
#
# If the keystroke is refused (missing Accessibility permission, error 1002)
# we must NOT silently fall back to the existing clipboard — that reads back
# whatever was copied hours ago and looks like the daemon replaying old text.
# Let the frontmost app settle after Raycast's own window dismisses, so the
# keystroke lands in Firefox rather than a window that is still going away.
sleep 0.15

# Send Cmd-C and wait for the sentinel to be replaced. The keystroke is
# occasionally dropped outright — the app has focus but never services it —
# so retry rather than failing on a single miss. Each attempt polls ~1s,
# which is well past Firefox's usual response time.
TEXT=""
for attempt in 1 2 3; do
  if ! KEYSTROKE_ERR="$(osascript -e 'tell application "System Events" to keystroke "c" using {command down}' 2>&1)"; then
    echo "Copy failed — could not send Cmd-C: $KEYSTROKE_ERR" >&2
    echo "Grant Accessibility permission to whichever app runs this script." >&2
    exit 1
  fi

  for _ in $(seq 1 20); do
    CURRENT="$(pbpaste || true)"
    if [ "$CURRENT" != "$SENTINEL" ] && [ -n "${CURRENT// }" ]; then
      TEXT="$CURRENT"
      break
    fi
    sleep 0.05
  done

  if [ -n "$TEXT" ]; then
    break
  fi
  if [ "$attempt" -lt 3 ]; then
    echo "Copy attempt $attempt did not land, retrying..." >&2
  fi
done

# Sentinel survived: the copy never landed. Restore the user's clipboard and
# bail rather than reading back hours-old text as if it were the selection.
if [ -z "$TEXT" ]; then
  echo "Copy did not land — nothing selected, or the app ignored Cmd-C." >&2
  echo "Refusing to read the previous clipboard contents." >&2
  exit 1
fi

if [ -z "${TEXT// }" ]; then
  echo "Nothing to speak"
  exit 0
fi

# What engine is active? Streaming + pauses only work for kokoro (the daemon
# path). Qwen and Chatterbox synthesize the whole thing inline, so just hand
# them the full text and let speak.sh do its thing.
ENGINE="$(python3 -c "
import json, os
g, p = {}, {}
try:
    with open(os.path.expanduser('~/.claude/tts-config.json')) as f: g = json.load(f)
except Exception: pass
try:
    with open('.claude/tts-config.json') as f: p = json.load(f)
except Exception: pass
print({**g, **p}.get('engine', 'kokoro'))
")"

if [ "$ENGINE" != "kokoro" ]; then
  "$SKILL_DIR/speak.sh" "$TEXT"
else
  # Split into (text, gap_after_seconds) chunks. Each chunk is one record:
  #   text\x1Fgap\x1E
  # \x1F (US) separates the two fields, \x1E (RS) terminates the record.
  # Using non-printable separators sidesteps quoting hell for arbitrary input.
  CHUNKS="$(TEXT="$TEXT" python3 <<'PY'
import os, re, sys

text = os.environ['TEXT']

# Gap budget (seconds of silence inserted *after* the chunk):
#   sentence  — within a paragraph/list item
#   item      — between list items, headings, or a blank-line-less line break
#   para      — between paragraphs (blank line in source)
GAP_SENTENCE = 0.1
GAP_ITEM     = 0.25
GAP_PARA     = 0.30

# Sentence splitter: end-of-sentence punctuation followed by whitespace/EOL,
# with a few abbreviation guards so "Dr. Smith" doesn't split.
ABBR = {
    'mr', 'mrs', 'ms', 'dr', 'st', 'jr', 'sr', 'vs', 'etc', 'e.g', 'i.e',
    'fig', 'no', 'inc', 'ltd', 'co', 'al',
}

def split_sentences(block: str):
    # Walk the string, breaking after .!? when the preceding token isn't an
    # abbreviation and the next char is whitespace or end-of-string.
    out, buf = [], []
    i, n = 0, len(block)
    while i < n:
        ch = block[i]
        buf.append(ch)
        if ch in '.!?':
            # collapse runs of terminal punctuation ("?!", "...")
            while i + 1 < n and block[i + 1] in '.!?':
                i += 1
                buf.append(block[i])
            # peek for whitespace / EOL
            j = i + 1
            if j >= n or block[j].isspace():
                tail = ''.join(buf)
                # last word before the punctuation
                m = re.search(r'([A-Za-z][A-Za-z.]*)[.!?]+$', tail)
                last = m.group(1).lower().rstrip('.') if m else ''
                if last not in ABBR:
                    sent = tail.strip()
                    if sent:
                        out.append(sent)
                    buf = []
                    # swallow the whitespace
                    while j < n and block[j].isspace() and block[j] != '\n':
                        j += 1
                    i = j - 1
        i += 1
    tail = ''.join(buf).strip()
    if tail:
        out.append(tail)
    return out

# Paragraphs = blocks separated by one or more blank lines.
paragraphs = re.split(r'\n[ \t]*\n+', text.strip())

records = []  # list of (chunk_text, gap_after_seconds)

for p_idx, para in enumerate(paragraphs):
    para = para.strip('\n')
    if not para.strip():
        continue
    # Inside a paragraph, lines without a blank between them are still
    # treated as separate "items" — covers bullet lists, headings, code-like
    # blocks. Strip common list/markdown leaders so they don't get spoken.
    raw_lines = [ln for ln in para.split('\n') if ln.strip()]
    cleaned_lines = []
    for ln in raw_lines:
        ln = ln.rstrip()
        ln = re.sub(r'^\s*(?:[-*+•]|\d+[.)])\s+', '', ln)  # list bullets
        ln = re.sub(r'^\s*#{1,6}\s+', '', ln)              # markdown headings
        ln = re.sub(r'^\s*>\s?', '', ln)                   # blockquote markers
        if ln.strip():
            cleaned_lines.append(ln.strip())

    for l_idx, line in enumerate(cleaned_lines):
        sentences = split_sentences(line) or [line]
        for s_idx, sent in enumerate(sentences):
            if s_idx < len(sentences) - 1:
                gap = GAP_SENTENCE
            elif l_idx < len(cleaned_lines) - 1:
                gap = GAP_ITEM
            elif p_idx < len(paragraphs) - 1:
                gap = GAP_PARA
            else:
                gap = 0.0  # nothing after the very last chunk
            records.append((sent, gap))

# Emit. \x1F between fields, \x1E ends each record.
buf = []
for sent, gap in records:
    buf.append(f"{sent}\x1F{gap:.2f}\x1E")
sys.stdout.write(''.join(buf))
PY
)"

  # Iterate the records. read -d $'\x1E' loops one record per iteration; we
  # split each record on \x1F into text + gap.
  while IFS= read -r -d $'\x1E' RECORD; do
    [ -z "$RECORD" ] && continue
    CHUNK_TEXT="${RECORD%%$'\x1F'*}"
    GAP="${RECORD##*$'\x1F'}"
    [ -z "${CHUNK_TEXT// }" ] && continue
    "$SKILL_DIR/speak.sh" "$CHUNK_TEXT"
    # Skip zero/near-zero gaps (final chunk, or unset).
    if [ -n "$GAP" ] && awk "BEGIN{exit !($GAP > 0.01)}"; then
      "$KOKORO_PYTHON" "$SKILL_DIR/tts_enqueue.py" pause "$GAP" >/dev/null
    fi
  done <<< "$CHUNKS"
fi

# Restore the original clipboard so we don't trash the user's copy buffer.
if [ -n "$ORIGINAL" ]; then
  printf %s "$ORIGINAL" | pbcopy
fi
