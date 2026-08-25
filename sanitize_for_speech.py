"""Rewrite text for listening, not reading, before it is spoken.

Reads from stdin, writes sanitized text to stdout. Used by speak.sh (every
caller) and tts-stop-speak.sh (condense fallbacks and short questions), so
all raw-text paths into the engines share one implementation.

Two jobs:
  1. Strip markdown (bold, italic, code fences, headings, bullets, links).
  2. Remove or rewrite things that are fine on screen but unlistenable when
     read aloud: file paths, shell commands, env vars, JSON, GUIDs, version
     strings, URLs, code blocks.

Design choices:
- Fenced code blocks are replaced with a short spoken reference ("code,
  five lines"), not read character-by-character.
- File paths become their basename with the extension spoken ("main dot
  go") — the full path is noise.
- Shell commands and variable assignments are dropped; the surrounding
  prose usually says what they do.
- GUIDs/UUIDs/hashes are replaced with "that" / "it" — meaningless spoken.
- snake_case identifiers are preserved (they're words, not markup).
"""
import re
import sys


def _basename_spoken(path: str) -> str:
    """Turn a file path into its basename with a speakable extension."""
    base = path.rstrip("/").rsplit("/", 1)[-1]
    if "." in base:
        name, ext = base.rsplit(".", 1)
        return f"{name} dot {ext}"
    return base


def sanitize(text: str) -> str:
    t = text

    # --- Fenced code blocks: replace with a spoken placeholder ---
    def _code_block(m: re.Match) -> str:
        body = m.group(2)
        lines = [ln for ln in body.strip().splitlines() if ln.strip()]
        n = len(lines)
        if n == 0:
            return " code. "
        if n == 1:
            return " a one-line command. "
        return f" code, {n} lines. "

    t = re.sub(r"```(\w*)\n(.*?)```", _code_block, t, flags=re.S)
    # Language hint on its own line after a fence (e.g. "bash" before code)
    t = re.sub(r"^\s*(?:bash|sh|shell|zsh|python|javascript|js|typescript|ts|json|yaml|yml|xml|html|css|sql|go|rust|java|c|cpp|csharp|ruby|php|swift|kotlin|dockerfile|makefile|ini|toml|conf|config)\s*$", "", t, flags=re.M)

    # --- GUIDs, UUIDs, long hex strings -> dropped (meaningless spoken) ---
    t = re.sub(
        r"\b[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}\b",
        "",
        t,
    )
    t = re.sub(r"\b[0-9a-fA-F]{16,}\b", "", t)

    # --- Model names and version strings -> spoken form or dropped ---
    # gpt-image-2, imagen-4.0-{fast,generate}, gemini-3.1-flash-preview
    # These are unlistenable when read as hyphenated soup. Drop them entirely.
    t = re.sub(r"\b(?:gpt|imagen|gemini|claude|qwen|llama|mistral|deepseek|o\d|dall-e|midjourney|stable-diffusion|flux|sora|runway|pika|kling|luma|haiper|gen-?\d|sdxl|sd\d)[\w.-]*\b", "", t, flags=re.I)
    # --- Version strings: 1.2.3, 2026-04-21, v13 ---
    # Run BEFORE the pure-punctuation cleanup so dates like "2026" survive.
    t = re.sub(r"\bv?\d+(?:\.\d+)+\b", "", t)
    t = re.sub(r"\b\d{4}-\d{2}-\d{2}\b", "", t)

    # --- Hyphenated identifiers with 3+ segments (iap-token, fast-generate) ---
    t = re.sub(r"\b\w+(?:-\w+){2,}\b", "", t)
    # data[0].b64_json, a.b.c — dotted/delimited identifiers
    t = re.sub(r"\b\w+\[\d+\](?:\.\w+)*\b", "", t)
    t = re.sub(r"\b\w+\.\w+\.\w+\b", "", t)

    # --- Braced expansion lists {a,b,c} -> dropped ---
    t = re.sub(r"\{[^}]*\}", "", t)

    # --- Inline code: keep the content, drop the backticks ---
    t = re.sub(r"`+([^`]*)`+", r"\1", t)

    # --- Bold / italic / strikethrough: drop markers, keep content ---
    t = re.sub(r"\*\*\*(.+?)\*\*\*", r"\1", t)
    t = re.sub(r"\*\*(.+?)\*\*", r"\1", t)
    t = re.sub(r"\*(.+?)\*", r"\1", t)
    t = re.sub(r"~~(.+?)~~", r"\1", t)

    # --- Headings and list markers ---
    t = re.sub(r"^#{1,6}\s*", "", t, flags=re.M)
    t = re.sub(r"^\s*[-*+]\s+", "", t, flags=re.M)
    t = re.sub(r"^\s*\d+[.)]\s+", "", t, flags=re.M)

    # --- Links [text](url) -> text; bare URLs -> dropped ---
    t = re.sub(r"\[([^\]]+)\]\([^)]+\)", r"\1", t)
    t = re.sub(r"https?://\S+", "", t)

    # --- Shell command lines: drop lines that are clearly commands ---
    # curl, export, variable assignments, flags-only lines, pipe continuations
    t = re.sub(r"^\s*(?:curl|wget|export|source|set|unset|cd|ls|cat|echo|printf|grep|awk|sed|jq|python3?|node|npm|pip|brew|git|docker|kubectl|ssh|scp|rsync|chmod|chown|mkdir|rm|mv|cp|tar|zip|unzip|gzip|gunzip)\b.*$", "", t, flags=re.M)
    t = re.sub(r"^\s*[A-Za-z_][A-Za-z0-9_]*=.*$", "", t, flags=re.M)
    t = re.sub(r"^\s*[-]{1,2}[A-Za-z].*$", "", t, flags=re.M)  # flag-only lines
    t = re.sub(r"^\s*[|>&;].*$", "", t, flags=re.M)  # pipe/redirect continuations
    t = re.sub(r"\\\s*$", "", t, flags=re.M)  # trailing backslashes

    # --- JSON objects/arrays on their own line -> dropped ---
    t = re.sub(r"^\s*[\{\[].*[\}\]]\s*$", "", t, flags=re.M)
    t = re.sub(r"^\s*[\{\[].*$", "", t, flags=re.M)  # opening braces
    t = re.sub(r"^.*[\}\]]\s*$", "", t, flags=re.M)  # closing braces

    # --- File paths -> dropped entirely (unlistenable) ---
    t = re.sub(r"(?:[\w.~-]+/)+[\w.~-]+", "", t)

    # --- $(command substitution) and $VAR -> dropped ---
    t = re.sub(r"\$\([^)]+\)", "", t)
    t = re.sub(r"\$[A-Za-z_][A-Za-z0-9_]*", "", t)

    # --- Underscore emphasis (leave snake_case alone) ---
    t = re.sub(r"\b_([^_]+)_\b", r"\1", t)

    # --- Remaining stray markdown and code punctuation ---
    t = re.sub(r"[*~`#]", "", t)
    t = re.sub(r"(?<![A-Za-z0-9])_|_(?![A-Za-z0-9])", "", t)
    # Brackets, braces, and stray punctuation
    t = re.sub(r"[\[\]{}]", "", t)
    # Standalone symbols that are unlistenable: = < > | & ; \ ^ % @ /
    t = re.sub(r"\s[=<>|&;\\^%@/]\s", " ", t)
    t = re.sub(r"^[=<>|&;\\^%@/]+|[=<>|&;\\^%@/]+$", "", t)
    # Slashes surrounded by nothing (leftover path fragments)
    t = re.sub(r"(^|\s)/+(\s|$)", r"\1\2", t)
    # Stray commas/periods left by dropped items
    t = re.sub(r"\s+([,.])", r"\1", t)
    t = re.sub(r"([,.])\s*([,.])", r"\1", t)
    t = re.sub(r"([,.])\s*([,.])", r"\1", t)  # run twice for chains
    t = re.sub(r"^[,.\s]+$", "", t, flags=re.M)
    # Lines that are now just punctuation and whitespace
    t = re.sub(r"^\s*[\W_]*\s*$", "", t, flags=re.M)
    # Colon followed by only punctuation to end of line (e.g. "OpenAI:,,")
    t = re.sub(r":\s*[\W_]+\s*$", ".", t, flags=re.M)
    # Leftover hyphens and digits from partial model names (e.g. "--001")
    # Only match when the token has NO letters at all — pure punctuation+digits.
    # Skip pure digits: years, counts, and ordinals are meaningful in prose.
    t = re.sub(r"(?<![A-Za-z0-9])[\W_]*\d+[\W_]*(?![A-Za-z0-9])(?<!\d)", "", t)
    # Any remaining token that has no letters (pure punctuation)
    # Keep em-dashes and en-dashes — they're meaningful sentence punctuation.
    # Skip tokens that are purely digits (years, counts, ordinals).
    t = re.sub(r"(?<![A-Za-z])[^\sA-Za-z\d—–]+(?![A-Za-z])", "", t)

    # --- Collapse whitespace ---
    t = re.sub(r"[ \t]+", " ", t)
    # Drop lines that are now empty or just punctuation
    t = re.sub(r"^\s*[\W_]*\s*$", "", t, flags=re.M)
    t = re.sub(r"\n{3,}", "\n\n", t)
    # Drop sentences that are now just punctuation (e.g. ",,,,.")
    # Run after all other cleanup so chains collapse fully.
    t = re.sub(r"(?:^|\n)\s*[\W_]+\s*(?:\n|$)", lambda m: m.group(1) if m.group(1) else "", t)
    # Any line that ends up with no letters at all
    t = re.sub(r"^[^A-Za-z]*$", "", t, flags=re.M)
    # Sentences that end with a colon and nothing after (e.g. "OpenAI:,, Google:")
    t = re.sub(r":\s*$", ".", t, flags=re.M)
    # Final pass: collapse again after all drops
    t = re.sub(r"[ \t]+", " ", t)
    t = re.sub(r"\n{3,}", "\n\n", t)

    return t.strip()


if __name__ == "__main__":
    print(sanitize(sys.stdin.read()))

