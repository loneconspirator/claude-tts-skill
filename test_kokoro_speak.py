"""Run with the Kokoro venv: ~/.claude/tts-venv/bin/python -m unittest test_kokoro_speak

The text is English whatever the voice. kokoro-mlx 0.1.4 can build an Italian
phonemizer, and on 2026-11-07 `if_sara` started reading English as Italian.
"""
from __future__ import annotations

import unittest
from unittest import mock

import kokoro_speak


class Recorder:
    def __init__(self, vocab, language="en"):
        self.language = language


class TestEnglishPhonemesForEveryVoice(unittest.TestCase):
    def language(self, voice: str) -> str:
        with mock.patch.object(kokoro_speak, "Phonemizer", Recorder):
            phon, language = kokoro_speak.phonemizer_for({}, voice)
        self.assertEqual(phon.language, language)
        return language

    def test_an_italian_voice_reads_english(self):
        self.assertEqual(self.language("if_sara"), "en-us")

    def test_a_chinese_voice_reads_english(self):
        self.assertEqual(self.language("zm_yunjian"), "en-us")

    def test_a_british_voice_keeps_its_accent(self):
        self.assertEqual(self.language("bf_emma"), "en-gb")

    def test_an_american_voice_is_unchanged(self):
        self.assertEqual(self.language("af_heart"), "en-us")

    def test_a_missing_british_phonemizer_falls_back_to_american(self):
        def only_us(vocab, language="en"):
            if language != "en-us":
                raise ValueError(language)
            return Recorder(vocab, language)
        with mock.patch.object(kokoro_speak, "Phonemizer", only_us):
            _, language = kokoro_speak.phonemizer_for({}, "bf_emma")
        self.assertEqual(language, "en-us")

    def test_the_real_phonemizer_builds_for_each(self):
        """Against the installed kokoro-mlx, not a stub: no model needed."""
        vocab = {}
        for voice, want in (("if_sara", "en-us"), ("zm_yunjian", "en-us"),
                            ("bf_emma", "en-gb"), ("af_heart", "en-us")):
            _, language = kokoro_speak.phonemizer_for(vocab, voice)
            self.assertEqual(language, want, voice)


if __name__ == "__main__":
    unittest.main(verbosity=2)
