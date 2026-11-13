#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Структурные замки постоянного набора триггеров скилов (issue #109).

Тесты проверяют полноту и машиночитаемость набора, но не имитируют выбор модели.
Фактическая активация в ZCode/Kimi выполняется по docs/skill-trigger-regression.md.
"""
from __future__ import annotations

import json
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
FIXTURE = REPO_ROOT / "tests" / "fixtures" / "skill_triggers.json"


class TestSkillTriggerRegression(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.data = json.loads(FIXTURE.read_text(encoding="utf-8"))
        cls.cases = cls.data["cases"]
        cls.skills = sorted(
            path.name for path in (REPO_ROOT / "skills").iterdir() if path.is_dir()
        )

    def test_every_real_skill_has_exactly_one_case(self):
        actual = [case["skill"] for case in self.cases]
        self.assertEqual(len(actual), len(set(actual)), "дубли кейсов скилов")
        self.assertEqual(sorted(actual), self.skills)
        self.assertEqual(len(actual), 30)

    def test_each_case_has_required_phrase_counts(self):
        for case in self.cases:
            with self.subTest(skill=case["skill"]):
                self.assertGreaterEqual(len(case["positive"]), 3)
                self.assertLessEqual(len(case["positive"]), 5)
                self.assertGreaterEqual(len(case["negative"]), 1)
                self.assertLessEqual(len(case["negative"]), 2)

    def test_case_shape_and_scenario(self):
        required = {"skill", "scenario", "positive", "negative", "expected"}
        for case in self.cases:
            with self.subTest(skill=case["skill"]):
                self.assertEqual(set(case), required)
                self.assertIn(case["scenario"], self.data["scenarios"])
                self.assertEqual(set(case["expected"]), {"router", "skills"})

    def test_router_matches_scenario_or_is_direct_entry(self):
        for case in self.cases:
            with self.subTest(skill=case["skill"]):
                default = self.data["scenarios"][case["scenario"]]["default_router"]
                router = case["expected"]["router"]
                self.assertTrue(
                    router == default or router == case["skill"],
                    f"неожиданный маршрутизатор {router}, ожидался {default} или прямой вход",
                )

    def test_expected_skills_exist_and_include_target(self):
        for case in self.cases:
            with self.subTest(skill=case["skill"]):
                expected = case["expected"]["skills"]
                self.assertIn(case["skill"], expected)
                self.assertEqual(len(expected), len(set(expected)))
                self.assertTrue(set(expected).issubset(set(self.skills)))

    def test_both_workflow_scenarios_are_covered(self):
        actual = {case["scenario"] for case in self.cases}
        self.assertEqual(actual, {"full_conveyor", "point_development"})

    def test_all_phrases_are_nonempty_and_globally_unique(self):
        phrases = []
        for case in self.cases:
            for kind in ("positive", "negative"):
                for phrase in case[kind]:
                    self.assertIsInstance(phrase, str)
                    self.assertEqual(phrase, phrase.strip())
                    self.assertTrue(phrase)
                    phrases.append(phrase)
        self.assertEqual(len(phrases), len(set(phrases)), "фразы не должны дублироваться")
        self.assertGreaterEqual(sum(len(case["positive"]) for case in self.cases), 90)
        self.assertGreaterEqual(sum(len(case["negative"]) for case in self.cases), 30)


if __name__ == "__main__":
    unittest.main(verbosity=2)
