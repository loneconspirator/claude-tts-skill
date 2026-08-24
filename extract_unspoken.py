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
import sys
from pathlib import Path

WATERMARK = Path("/tmp/tts-stop-speak.spoken-uuid")


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
    for uid in tail:
        d = entries[uid]
        if d.get("type") != "assistant":
            continue
        newest_assistant = uid
        blocks = d.get("message", {}).get("content", [])
        texts = [
            b.get("text", "")
            for b in blocks
            if isinstance(b, dict) and b.get("type") == "text"
        ]
        texts = [t for t in texts if t.strip()]
        if texts:
            parts.append("\n".join(texts))

    # Advance the watermark to the newest entry on the branch regardless of
    # whether it carried text, so tool-only turns are not re-scanned forever.
    new_mark = chain[-1] if chain else ""
    if newest_assistant:
        new_mark = chain[-1]

    print("\n\n".join(parts) if parts else fallback)
    print(new_mark, file=sys.stderr)


if __name__ == "__main__":
    main()
