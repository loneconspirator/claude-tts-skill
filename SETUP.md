# TTS Skill Setup

This file is for **Claude** to use when the user runs `/tts on` (or any TTS command) on a machine where the skill has never been initialized. It documents the runtime dependencies the skill needs and how to diagnose / fix a broken environment.

When to read this file:
- `/tts on` is invoked and `speak.sh` has never been run successfully on this machine.
- Any TTS command produces an error (missing venv, ImportError, SSL failure, voice not found, etc.).
- The user explicitly asks to install or fix the TTS skill.

Otherwise, do **not** load this file — `SKILL.md` is sufficient for everyday command handling.

---

## What a working install looks like (default engine: Kokoro)

All four of these must be true:

1. **Venv exists** at `~/.claude/tts-venv/` with a Python 3.10–3.12 interpreter (Kokoro-mlx does **not** support 3.13+).
2. **Packages installed** in that venv: `kokoro-mlx`, `soundfile`, `numpy`. On corporate networks doing TLS interception (Zscaler, Netskope, etc.) also: `pip-system-certs`.
3. **Kokoro model + voices downloaded** to `~/.cache/huggingface/hub/models--mlx-community--Kokoro-82M-bf16/`. The `voices/` subdirectory inside the snapshot must contain ~54 `.safetensors` files. `KokoroModel.from_pretrained` only pulls the model weights — voices have to be fetched separately via `snapshot_download` with `allow_patterns=['voices/*.safetensors']`.
4. **Global config exists** at `~/.claude/tts-config.json` with at minimum `{"enabled": true}`.

Optionally (only if the user wants Claude to automatically speak in every session):

5. **SessionStart hook wired** in `~/.claude/settings.json` pointing at `bash ~/.claude/skills/tts/tts-inject.sh`. **Do not edit `settings.json` without asking the user first** — it's their global agent config.

---

## Quick health check

Run these in order; stop at the first failure and fix it.

```bash
# 1. Venv Python exists and is 3.10–3.12
~/.claude/tts-venv/bin/python --version
# Expect: Python 3.10.x / 3.11.x / 3.12.x

# 2. Core packages import
~/.claude/tts-venv/bin/python -c "import kokoro_mlx, soundfile, numpy; print('ok')"

# 3. Voices present
ls ~/.cache/huggingface/hub/models--mlx-community--Kokoro-82M-bf16/snapshots/*/voices/*.safetensors 2>/dev/null | wc -l
# Expect: 54

# 4. Config exists and is enabled
cat ~/.claude/tts-config.json
# Expect: {"enabled": true, ...}

# 5. End-to-end test (bypasses the daemon queue)
TTS_NO_QUEUE=1 ~/.claude/skills/tts/speak.sh "Hello from Kokoro."
```

If step 5 prints no error and audio plays, the skill is healthy.

---

## Install / repair recipes (in order of likelihood)

### Recipe A — fresh install on macOS Apple Silicon

```bash
# Get a compatible Python if the system default is >=3.13 or <3.10:
brew install python@3.12

# Create the venv. Use python3.12 explicitly — `python3` may point at 3.13+.
/opt/homebrew/opt/python@3.12/bin/python3.12 -m venv ~/.claude/tts-venv
~/.claude/tts-venv/bin/pip install --upgrade pip
~/.claude/tts-venv/bin/pip install kokoro-mlx soundfile numpy

# Download the voices (model.from_pretrained alone doesn't fetch them):
~/.claude/tts-venv/bin/python -c "
from huggingface_hub import snapshot_download
snapshot_download(repo_id='mlx-community/Kokoro-82M-bf16',
                  allow_patterns=['voices/*.safetensors'])
"

# Seed the global config if missing:
[ -f ~/.claude/tts-config.json ] || echo '{"enabled": true}' > ~/.claude/tts-config.json
```

### Recipe B — Python version mismatch

Symptom: `pip install kokoro-mlx` fails with
`ERROR: Could not find a version that satisfies the requirement misaki>=...`
and the log mentions `Requires-Python >=3.10,<3.13`.

Fix: install `python@3.12` via Homebrew (or `python@3.11`), then recreate the venv pointing at that interpreter. Do **not** keep a venv built on 3.13/3.14 — the resolver will silently install older, broken versions of `kokoro-mlx` (e.g. 0.1.0) that fail at runtime.

### Recipe C — SSL: CERTIFICATE_VERIFY_FAILED on huggingface.co

Symptom: HF downloads fail with `unable to get local issuer certificate`, but `curl https://huggingface.co/` works. Almost always means the network is doing TLS interception with a corporate CA that the macOS keychain trusts but Python's bundled `certifi` does not.

Verify with:
```bash
curl -sv --max-time 5 https://huggingface.co/ 2>&1 | grep -E "issuer|subject" | head -4
```
If the issuer is anything other than a public CA (DigiCert, Let's Encrypt, etc.) — e.g. `Zscaler`, `Netskope`, or a company name — that's the culprit.

Fix:
```bash
~/.claude/tts-venv/bin/pip install pip-system-certs
```
`pip-system-certs` patches `ssl`/`requests`/`urllib3` in that venv to trust the macOS keychain, which already contains the corporate root. No env vars needed afterward.

(`SSL_CERT_FILE=$(python -c 'import certifi; print(certifi.where())')` does **not** help here — certifi by definition only ships public roots.)

### Recipe D — `FileNotFoundError: Voice file not found: .../voices/<name>.safetensors`

The model weights downloaded but the voice safetensors didn't. Fix:
```bash
~/.claude/tts-venv/bin/python -c "
from huggingface_hub import snapshot_download
snapshot_download(repo_id='mlx-community/Kokoro-82M-bf16',
                  allow_patterns=['voices/*.safetensors'])
"
```

### Recipe E — `huggingface_hub` 1.x client lifecycle bug

Symptom: `RuntimeError: Cannot send a request, as the client has been closed.`

This was seen with `huggingface-hub==1.18.0`. If it recurs, either upgrade to a newer 1.x release once available, or pin downward to `huggingface-hub<1.0` (note: `kokoro-mlx` declares `>=1.0` so pip will warn — but the runtime still works).

### Recipe F — Chatterbox / Qwen engines

Only set these up if the user asks to switch engines. Both require [Pinokio](https://pinokio.computer/) with the `Qwen3-TTS-MLX-WebUI-Enhanced` app installed. See `README.md` for paths and engine notes. Do not touch these during a default-Kokoro setup.

---

## After install

1. Confirm `/tts on` produces audible speech (a one-sentence test is enough).
2. If the user wants Claude to speak automatically at the end of every response, ask whether to wire the SessionStart hook into `~/.claude/settings.json`:
   ```json
   "hooks": {
     "SessionStart": [
       { "hooks": [ { "type": "command",
                      "command": "bash ~/.claude/skills/tts/tts-inject.sh" } ] }
     ]
   }
   ```
   Without this hook, Claude only speaks when the skill is explicitly invoked — which is fine for testing but not for the everyday "Claude reads its answers aloud" experience the README describes.
