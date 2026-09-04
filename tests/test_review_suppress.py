#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Тесты для scripts/review_suppress.py (ведение suppress.json — подавления
ложных срабатываний ревью, решения Ревьюера «не баг» из 05a).

    python3 -m unittest tests.test_review_suppress -v
"""
from __future__ import annotations

import io
import json
import contextlib
import sys
import tempfile
import unittest
from datetime import date
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "scripts"))

import review_suppress as rs  # noqa: E402  (импорт после правки sys.path)


class TestAdd(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.dir = Path(self._tmp.name)
        self.sup = self.dir / "code-review" / "suppress.json"

    def _run(self, *argv):
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            rc = rs.main(list(argv))
        return rc, out.getvalue()

    def test_add_creates_file_with_entry(self):
        rc, out = self._run(
            "add", str(self.sup), "CommentedOutCodeLine", "Module.bsl",
            "--line", "269", "--reason", "описательный комментарий, не код",
            "--author", "Roman")
        self.assertEqual(rc, 0)
        entries = json.loads(self.sup.read_text(encoding="utf-8"))
        self.assertEqual(len(entries), 1)
        e = entries[0]
        self.assertEqual(e["key"], "CommentedOutCodeLine")
        self.assertEqual(e["file"], "Module.bsl")
        self.assertEqual(e["line"], 269)
        self.assertEqual(e["reason"], "описательный комментарий, не код")
        self.assertEqual(e["date"], date.today().isoformat())
        self.assertIn("не забудьте отметить решение в 05a", out)

    def test_add_without_line_suppresses_whole_file(self):
        rc, _ = self._run("add", str(self.sup), "MagicNumber", "Module.bsl",
                          "--reason", "осмысленные константы проекта")
        self.assertEqual(rc, 0)
        entries = json.loads(self.sup.read_text(encoding="utf-8"))
        self.assertIsNone(entries[0]["line"])

    def test_add_appends_and_skips_duplicate(self):
        self._run("add", str(self.sup), "MagicNumber", "Module.bsl",
                  "--reason", "первая")
        rc, _ = self._run("add", str(self.sup), "MagicNumber", "Module.bsl",
                          "--reason", "дубль")
        self.assertEqual(rc, 2)
        entries = json.loads(self.sup.read_text(encoding="utf-8"))
        self.assertEqual(len(entries), 1)          # дубль не добавлен

    def test_add_requires_reason(self):
        with self.assertRaises(SystemExit):        # argparse: --reason обязателен
            rs.main(["add", str(self.sup), "MagicNumber", "Module.bsl"])
        self.assertFalse(self.sup.exists())

    def test_add_rejects_broken_existing_file(self):
        self.sup.parent.mkdir(parents=True, exist_ok=True)
        self.sup.write_text(json.dumps(
            [{"key": "X", "file": "Y"}]),          # нет reason — битая запись
            encoding="utf-8")
        rc, _ = self._run("add", str(self.sup), "MagicNumber", "Module.bsl",
                          "--reason", "поверх битого файла")
        self.assertEqual(rc, 2)                    # громкая ошибка, не тишина


class TestCheck(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.dir = Path(self._tmp.name)
        self.sup = self.dir / "suppress.json"

    def _run(self, *argv):
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            rc = rs.main(list(argv))
        return rc, out.getvalue()

    def test_check_missing_file_is_ok(self):
        rc, out = self._run("check", str(self.sup))
        self.assertEqual(rc, 0)
        self.assertIn("подавлений нет", out)

    def test_check_lists_entries(self):
        self._run("add", str(self.sup), "CommentedOutCodeLine", "Module.bsl",
                  "--line", "269", "--reason", "описательный комментарий",
                  "--author", "Roman")
        rc, out = self._run("check", str(self.sup))
        self.assertEqual(rc, 0)
        self.assertIn("1 подавлений", out)
        self.assertIn("CommentedOutCodeLine", out)
        self.assertIn("Module.bsl:269", out)
        self.assertIn("описательный комментарий", out)


if __name__ == "__main__":
    unittest.main()
