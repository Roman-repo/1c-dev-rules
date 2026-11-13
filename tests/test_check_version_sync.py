#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Тесты замков синхронизации версии, включая next-session-prompt.md."""
from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "scripts"))

import check_version_sync  # noqa: E402


class TestNextSessionPrompt(unittest.TestCase):
    expected = "1.2.3"
    markers = {
        "H1": "ПЛАГИН 1.2.3",
        "контекст": "Контекст: плагин 1c-dev-rules **1.2.3**",
        "итоговая проверка": "Плагин 1c-* v1.2.3",
        "команда обновления": "обновлять до 1.2.3 rsync",
        "верификация доставки": "верификация доставки 1.2.3",
    }

    def valid_text(self) -> str:
        return "\n".join(
            (
                "# Промпт для новой сессии — ПЛАГИН 1.2.3",
                self.markers["контекст"],
                "Историческая справка: ранее был релиз 0.1.0.",
                self.markers["команда обновления"] + "-ом из терминала",
                self.markers["итоговая проверка"] + " (8 этапов)",
                self.markers["верификация доставки"] + " (клон синхронизирован)",
            )
        )

    def check_text(self, text: str):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "next-session-prompt.md"
            path.write_text(text, encoding="utf-8")
            return check_version_sync.check_next_session_prompt(path, self.expected)

    def test_valid_markers_ignore_release_history(self):
        self.assertEqual(self.check_text(self.valid_text()), [])

    def test_stale_version_in_each_current_marker(self):
        for label, marker in self.markers.items():
            with self.subTest(marker=label):
                stale = self.valid_text().replace(marker, marker.replace("1.2.3", "9.9.9"))
                errors = self.check_text(stale)
                self.assertTrue(
                    any(label in error and "9.9.9" in error for error in errors),
                    errors,
                )

    def test_missing_current_marker_is_an_error(self):
        for label, marker in self.markers.items():
            with self.subTest(marker=label):
                errors = self.check_text(self.valid_text().replace(marker, ""))
                self.assertTrue(
                    any(label in error and "не найден" in error for error in errors),
                    errors,
                )

    def test_missing_file(self):
        path = Path(tempfile.gettempdir()) / "definitely-missing-next-session-prompt.md"
        self.assertFalse(path.exists())
        errors = check_version_sync.check_next_session_prompt(path, self.expected)
        self.assertEqual(len(errors), 1)
        self.assertIn("не найден", errors[0])

    def test_real_prompt_matches_plugin_manifest(self):
        manifest = REPO_ROOT / ".zcode-plugin" / "plugin.json"
        expected = json.loads(manifest.read_text(encoding="utf-8"))["version"]
        errors = check_version_sync.check_next_session_prompt(
            REPO_ROOT / "docs" / "next-session-prompt.md", expected
        )
        self.assertEqual(errors, [])


if __name__ == "__main__":
    unittest.main(verbosity=2)
