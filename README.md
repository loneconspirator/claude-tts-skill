# claude-tts-skill

Give Claude Code a voice. A user-invocable `/tts` skill that lets Claude speak its responses through your speakers using local text-to-speech models on macOS (Apple Silicon).

Supports three engines:

- **Kokoro** (default) — fast, 54 voices, real speed control, runs standalone in a Python venv
- **Chatterbox** — voice cloning from a reference .wav, emotion control, runs in a Pinokio Python env
- **Qwen** — multi-language, hits a Pinokio API server

## Install

Clone this repo directly into `~/.claude/skills/`:

```bash
git clone https://github.com/loneconspirator/claude-tts-skill.git ~/.claude/skills/tts
```

Claude Code will pick up the skill on next session start. Use `/tts on` to enable.

## Prerequisites

### Kokoro (default engine)

Kokoro runs in its own Python venv to keep dependencies isolated. Create it at `~/.claude/tts-venv`:

```bash
python3 -m venv ~/.claude/tts-venv
~/.claude/tts-venv/bin/pip install kokoro-mlx soundfile numpy
```

If you want the venv elsewhere, set `kokoro_python` in your config to point at its `python` binary.

### Chatterbox (optional)

Requires the [Pinokio](https://pinokio.computer/) mlx-audio environment. The default path is:

```
~/pinokio/api/Qwen3-TTS-MLX-WebUI-Enhanced.git/app/env/bin/python3
```

If your Pinokio app lives elsewhere, set `chatterbox_python` in your config.

Chatterbox also needs a reference audio .wav for voice cloning. Default location: `~/.claude/tts-reference-voice.wav`. Override with `ref_audio` in config.

### Qwen (optional)

Requires [Qwen3-TTS-MLX-WebUI-Enhanced](https://github.com/Qwen) running locally in Pinokio. Set `api_url` in your config if you've changed its port from the default `http://127.0.0.1:42003`.

## Enabling auto-speak in sessions

The skill ships with `tts-inject.sh`, a SessionStart hook that tells Claude to speak a summary at the end of each response when TTS is enabled.

Wire it into your `~/.claude/settings.json`:

```json
{
  "hooks": {
    "SessionStart": [
      {
        "hooks": [
          {
            "type": "command",
            "command": "bash ~/.claude/skills/tts/tts-inject.sh"
          }
        ]
      }
    ]
  }
}
```

The hook is a no-op when TTS is disabled, so it's safe to leave wired up permanently.

## Configuration

Two config files (project overrides global):

- **Global**: `~/.claude/tts-config.json`
- **Project**: `.claude/tts-config.json` (optional, per-repo)

All fields are optional. Common ones:

| Field | Engine | Description |
| --- | --- | --- |
| `enabled` | all | Master on/off |
| `engine` | all | `kokoro` (default), `qwen`, `chatterbox` |
| `voice` | all | Engine-specific. Run `/tts voices` to list. |
| `speed` | kokoro | 0.5–2.0 |
| `instruct` | qwen | Style prompt |
| `api_url` | qwen | API base URL |
| `exaggeration` | chatterbox | 0.0–1.0 emotion intensity |
| `cfg_weight` | chatterbox | 0.0–1.0 style adherence |
| `ref_audio` | chatterbox | Path to .wav for voice cloning |
| `kokoro_python` | kokoro | Override Kokoro Python interpreter path |
| `chatterbox_python` | chatterbox | Override Chatterbox Python interpreter path |
| `summary_model` | all | Model that condenses replies for speech |
| `summary_prompt` | all | Override the condense prompt (default: `summary-prompt.txt` in the skill dir — edit that file to change it) |
| `summary_min_chars` | all | Replies shorter than this skip the condense pass |
| `mute_cwd_globs` | all | Directories that stay silent. Defaults to `["*/scratchpad", "*/scratchpad/*"]`, which keeps probe and background sessions from speaking over the session that spawned them. |

Use `/tts status` to see the effective merged config.

## Pronunciation overrides

Some words get silently dropped by `espeak` (the phonemizer Kokoro uses). The skill includes `/tts heal`, which curates a runtime miss log (`phonemizer-misses.log`) into a personal dictionary (`phonemizer-dict.json`).

Your `phonemizer-dict.json` is gitignored — it's personal and grows over time. `phonemizer-dict.example.json` ships as the starter shape.

## Commands

See [SKILL.md](SKILL.md) for the full command reference. Quick tour:

- `/tts on` / `/tts off` — toggle
- `/tts engine <name>` — switch engines
- `/tts voice <name>` — pick a voice
- `/tts voices` — list available voices for current engine
- `/tts browse` — browse Kokoro voices interactively, hearing each as you select it; arrow keys or mouse (`~/.claude/skills/tts/browse-voices.sh`)
- `/tts status` — show effective config
- `/tts heal` — curate pronunciation misses

## Adding an engine

See [adding-engines.md](adding-engines.md).

## License

MIT — see [LICENSE](LICENSE).
