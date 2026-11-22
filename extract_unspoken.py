"""Extract the assistant text that has not been spoken yet.

Reads the Stop-hook payload on stdin, prints the text to speak on stdout,
and prints the uuid to record as the new watermark on stderr.

Why a watermark instead of "the last turn": a turn interleaved with tool
calls is split across many assistant entries, and the trailing one is often
a short lead-in ("Let me check the config."). Taking only that entry
undercounts the reply, so it falls under summary_min_chars and is silently
never spoken. Trying to find the turn's start by walking back to the last
user message does not work either -- tool_result entries are also typed
'user', and the boundary is ambiguous when a hook fires without a fresh
user message.

The transcript is a linked list (uuid / parentUuid), so "everything since
the last thing I actually spoke" is exact. Following the parent chain back
from the newest leaf also keeps us on the active branch, so rewound or
sidelined branches are not read back.
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

# One global path meant that with several sessions running, whichever wrote
# last owned the watermark: every other session then read a uuid from someone
# else's transcript, found nothing after it, undercounted its turn to a few
# characters, fell under summary_min_chars, and never spoke at all. The Stop
# hook now points this at a per-session file. The old path stays as the
# default so a direct call still behaves the way it always did.
WATERMARK = Path(os.environ.get("TTS_WATERMARK_FILE")
                 or "/tmp/tts-stop-speak.spoken-uuid")

# Tool calls that mean "the turn ended because Claude needs the user".
# Their prompt text lives in the tool_use input, not in a text block, so
# without this the most explicit ask-the-user signal is the one thing the
# extractor cannot see.
QUESTION_TOOLS = {"AskUserQuestion", "ExitPlanMode"}

# Marker line prepended to extracted question text. The Stop hook greps for
# this to decide whether to bypass summary_min_chars -- a short question is
# exactly what should survive the floor, not what should be filtered by it.
QUESTION_MARKER = "[[TTS_QUESTION]]"


def question_text(block: dict) -> str:
    """Render a question tool_use block as speakable text.

    Only the question and the short option labels are used. Option
    descriptions are written for the screen -- long, full of file paths and
    identifiers -- and reading them aloud is worse than not speaking at all.
    """
    inp = block.get("input") or {}
    name = block.get("name")

    if name == "ExitPlanMode":
        # The plan itself is long markdown; the condenser handles it, but the
        # spoken point is that approval is being requested.
        plan = (inp.get("plan") or "").strip()
        head = "Asking to approve a plan before starting work."
        return f"{head}\n\n{plan}" if plan else head

    out = []
    for q in inp.get("questions") or []:
        if not isinstance(q, dict):
            continue
        text = (q.get("question") or "").strip()
        if not text:
            continue
        labels = [
            (o.get("label") or "").strip()
            for o in (q.get("options") or [])
            if isinstance(o, dict) and (o.get("label") or "").strip()
        ]
        if labels:
            text += " Options are: " + "; ".join(labels) + "."
        out.append(text)
    return "\n".join(out)


def main() -> None:
    try:
        payload = json.load(sys.stdin)
    except Exception:
        return

    fallback = payload.get("last_assistant_message", "") or ""
    path = payload.get("transcript_path")
    if not path:
        print(fallback)
        return

    try:
        raw = open(path).read().splitlines()
    except Exception:
        print(fallback)
        return

    entries = {}
    order = []
    for ln in raw:
        try:
            d = json.loads(ln)
        except Exception:
            continue
        uid = d.get("uuid")
        if not uid:
            continue
        entries[uid] = d
        order.append(uid)

    if not order:
        print(fallback)
        return

    # Walk the parent chain back from the newest entry so we only ever read
    # the branch that is actually live.
    chain = []
    seen = set()
    cur = order[-1]
    while cur and cur in entries and cur not in seen:
        seen.add(cur)
        chain.append(cur)
        cur = entries[cur].get("parentUuid")
    chain.reverse()

    try:
        last_spoken = WATERMARK.read_text().strip()
    except Exception:
        last_spoken = ""

    # Everything after the watermark. If the watermark is missing or is not on
    # this branch (new session, rewind, cleared /tmp), fall back to the last
    # assistant run only -- speaking an entire session's backlog would be worse
    # than speaking too little.
    if last_spoken and last_spoken in chain:
        tail = chain[chain.index(last_spoken) + 1:]
    else:
        tail = []
        for uid in reversed(chain):
            if entries[uid].get("type") == "assistant":
                tail.append(uid)
            elif tail:
                break
        tail.reverse()

    parts = []
    newest_assistant = ""
    saw_question = False
    for uid in tail:
        d = entries[uid]
        if d.get("type") != "assistant":
            continue
        newest_assistant = uid
        blocks = d.get("message", {}).get("content", [])
        texts = []
        for b in blocks:
            if not isinstance(b, dict):
                continue
            if b.get("type") == "text":
                texts.append(b.get("text", ""))
            elif b.get("type") == "tool_use" and b.get("name") in QUESTION_TOOLS:
                q = question_text(b)
                if q:
                    texts.append(q)
                    saw_question = True
        texts = [t for t in texts if t.strip()]
        if texts:
            parts.append("\n".join(texts))

    # Advance the watermark to the newest entry on the branch regardless of
    # whether it carried text, so tool-only turns are not re-scanned forever.
    new_mark = chain[-1] if chain else ""
    if newest_assistant:
        new_mark = chain[-1]

    body = "\n\n".join(parts) if parts else fallback
    # The marker rides on stdout ahead of the text so the caller can make the
    # min-chars decision without re-parsing the transcript. The Stop hook
    # strips it before the text reaches the condenser.
    if saw_question and body.strip():
        body = f"{QUESTION_MARKER}\n{body}"
    print(body)
    print(new_mark, file=sys.stderr)


if __name__ == "__main__":
    main()
