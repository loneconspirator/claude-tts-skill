# Curate Phonemizer Misses

Referenced from `SKILL.md`. Use when asked to update `phonemizer-dict.json` from new entries in `phonemizer-misses.log`.

All scripts live in `.claude/skills/tts/` alongside the data files.

> **Python env:** `validate_sub.py` imports `kokoro_mlx`, which only exists in the TTS venv. Use `~/.claude/tts-venv/bin/python` for every script in this guide — system `python` will fail with `ModuleNotFoundError`. Run scripts from the skill directory: `cd ~/.claude/skills/tts`.

---

## Step 1 — Find what needs curation

```bash
~/.claude/tts-venv/bin/python diff_misses.py
```

This prints words from `phonemizer-misses.log` that don't yet have a dict entry, along with their current runtime resolution (how the system handled them as a fallback). Any word shown with a `spell:` resolution is being spelled out letter-by-letter at runtime and definitely needs a better entry.

---

## Step 2 — Propose substitutions

For each word, choose a substitution using this priority order:

**1. Homophone / respelling** — an English word or phrase that sounds the same when phonemized. Prefer this when a clean option exists. Best for true compound words, acronyms, and initialisms — places where a small gap between tokens is natural or unnoticeable.
- `"pg"` → `"pee gee"` (initialism)
- `"tarball"` → `"tar ball"` (compound)
- `"non"` → `"nun"` (single token, no gap)

**2. IPA** (prefixed `ipa:`) — use when no clean homophone exists, for proper nouns with a known pronunciation, **or whenever the source is a single semantic word that would have to be split across multiple tokens**. The phonemizer inserts a perceptible gap between space-separated tokens, which sounds awkward inside what should be one word.
- `"portainer"` → `"ipa:pɔːrˈteɪnər"`
- `"heroku"` → `"ipa:həˈroʊku:"`
- `"validator"` → `"ipa:ˈvælɪdeɪtər"` (NOT `"valid eight er"` — three audible gaps mid-word)

**Rule of thumb:** if you find yourself wanting to write more than two space-separated tokens for a single word, switch to IPA. Two tokens is borderline — fine for compounds (`tar ball`), bad for derivatives (`valid eight`).

**Never** just copy a `spell:` resolution into the dict — that's always the runtime's last resort and should be replaced with something better.

### Common patterns

| Pattern | Example | Approach |
|---|---|---|
| Acronym / initialism | `ts`, `yml`, `pg` | Spell out letter names: `"tee ess"`, `"why em el"`, `"pee gee"` |
| True compound word | `tarball`, `greenlight` | Split at morpheme boundary: `"tar ball"`, `"green light"` |
| Single word with suffix / derivative | `validator`, `phonemizer`, `initializer` | **IPA** — splitting like `"valid eight er"` puts gaps where there shouldn't be any |
| Proper noun | `Portainer`, `Fastmail` | IPA if pronunciation is clear; compound split fine for true compounds like `Fastmail` |
| Common word the phonemizer chokes on | `non`, `verified` | Single-token homophone if one exists; otherwise IPA |

---

## Step 3 — Validate each substitution

**Do not add anything to the dict without validating it first.**

```bash
~/.claude/tts-venv/bin/python validate_sub.py <word> <substitution>
```

Examples:
```bash
~/.claude/tts-venv/bin/python validate_sub.py pg "pee gee"
~/.claude/tts-venv/bin/python validate_sub.py yml "why em el"
~/.claude/tts-venv/bin/python validate_sub.py portainer "ipa:pɔːrˈteɪnər"
```

Exit code 0 means valid; exit code 1 means the phonemizer produced nothing. If a substitution fails, try an alternate homophone or switch strategies. Do not add failing entries.

---

## Step 4 — Update the dict

Pass all validated pairs to the updater in one call:

```bash
~/.claude/tts-venv/bin/python update_dict.py pg="pee gee" yml="why em el" portainer="ipa:pɔːrˈteɪnər"
```

The script lowercases all keys, preserves the `_comment` field, and keeps the file alphabetically sorted. It will skip (and report) any keys already present in the dict.

---

## Step 5 — Report to the user

After updating, summarize:
- How many entries were added
- Any words that couldn't be validated, with the reason
- Any judgment calls worth flagging (e.g. `non` → `nun` works phonetically but may sound odd mid-compound)
