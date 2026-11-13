#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Тесты для scripts/validate_skills.py.

Запуск без зависимостей:
    python3 -m unittest tests.test_validate_skills -v
или (из корня репо):
    python3 tests/test_validate_skills.py

Fixture-скилы лежат в tests/fixtures/ — это ИСКУССТВЕННЫЕ скилы,
содержащие ошибки. Реальные skills/ ими не затрагиваются: валидатор
вызывается напрямую через validate_skill(skill_dir), а не через main(),
поэтому fixture-каталоги не нужно класть в skills/.
"""
from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

# Добавляем корень репозитория и scripts/ в sys.path, чтобы импортировать валидатор.
REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))
sys.path.insert(0, str(REPO_ROOT / "scripts"))

import validate_skills  # noqa: E402  (импорт после правки sys.path)

FIXTURES = REPO_ROOT / "tests" / "fixtures"


class TestParseFrontmatter(unittest.TestCase):
    """Парсер frontmatter: базовые случаи."""

    def test_inline_value(self):
        fields, err = validate_skills.parse_frontmatter("---\nname: foo\n---\nbody")
        self.assertIsNone(err)
        self.assertEqual(fields["name"], "foo")

    def test_folded_block_scalar(self):
        text = "---\ndescription: >\n  строка один\n  строка два\n---\n"
        fields, err = validate_skills.parse_frontmatter(text)
        self.assertIsNone(err)
        self.assertEqual(fields["description"], "строка один строка два")

    def test_missing_closing_fence(self):
        _, err = validate_skills.parse_frontmatter("---\nname: foo\n")
        self.assertIsNotNone(err)

    def test_no_opening_fence(self):
        _, err = validate_skills.parse_frontmatter("name: foo\n---\n")
        self.assertIsNotNone(err)


class TestValidateSkillFixtures(unittest.TestCase):
    """Гоняет валидатор на fixture-скилах и asserts на ожидаемые ошибки."""

    def _run(self, fixture_name):
        """Запускает validate_skill на fixture и возвращает список сообщений ERR."""
        skill_dir = FIXTURES / fixture_name
        self.assertTrue(skill_dir.is_dir(), f"fixture {fixture_name} не найден")
        rep = validate_skills.validate_skill(skill_dir, global_refs={}, global_md={})
        return [f.message for f in rep.errors]

    def test_valid_skill_has_no_errors(self):
        """Эталонный скил: 0 ошибок."""
        errs = self._run("1c-valid")
        self.assertEqual(errs, [], f"ожидалось 0 ошибок, получено: {errs}")

    def test_broken_skill_name_mismatch(self):
        """name='1c-wrong-name' в каталоге 1c-broken — ошибка несовпадения."""
        errs = self._run("1c-broken")
        self.assertTrue(
            any("не совпадает с каталогом" in m for m in errs),
            f"ожидалась ошибка несовпадения name, получено: {errs}",
        )

    def test_broken_skill_forbidden_token_toro(self):
        """Токен 'торо_' в теле — ошибка проектной специфики."""
        errs = self._run("1c-broken")
        self.assertTrue(
            any("торо_" in m and "проектная специфика" in m for m in errs),
            f"ожидалась ошибка про 'торо_', получено: {errs}",
        )

    def test_broken_skill_forbidden_token_project_toir(self):
        """Токен 'Project/Toir' — ошибка проектной специфики."""
        errs = self._run("1c-broken")
        self.assertTrue(
            any("Project/Toir" in m for m in errs),
            f"ожидалась ошибка про 'Project/Toir', получено: {errs}",
        )

    def test_broken_skill_description_over_soft_limit(self):
        """description >250 симв → предупреждение (WARN), но не ошибка.
        Проверяем через warnings, т.к. это soft-лимит."""
        skill_dir = FIXTURES / "1c-broken"
        rep = validate_skills.validate_skill(skill_dir, global_refs={}, global_md={})
        warns = [f.message for f in rep.warnings]
        self.assertTrue(
            any("> 250" in m or "риск under-trigger" in m for m in warns),
            f"ожидалось предупреждение о длине description, warnings: {warns}",
        )

    def test_no_license_skill_missing_required_field(self):
        """Скил без license — ошибка об обязательном поле."""
        errs = self._run("1c-no-license")
        self.assertTrue(
            any("license" in m and "обязательное" in m for m in errs),
            f"ожидалась ошибка про отсутствие license, получено: {errs}",
        )


class TestRealSkills(unittest.TestCase):
    """Гоняет валидатор на реальных skills/ — интеграционный тест.
    Если кто-то сломает скил, этот тест упадёт первым."""

    def test_all_real_skills_pass(self):
        skills_dir = REPO_ROOT / "skills"
        skill_dirs = sorted(d for d in skills_dir.iterdir() if d.is_dir())
        self.assertGreater(len(skill_dirs), 0, "каталог skills/ пуст")

        # собираем глобальные индексы (как делает main())
        global_refs = {}
        for d in skill_dirs:
            refs_dir = d / "references"
            if refs_dir.is_dir():
                for p in refs_dir.glob("*.md"):
                    global_refs.setdefault(p.name, d.name)
        global_md = {}
        for p in REPO_ROOT.rglob("*.md"):
            if ".git" in p.parts or "tests" in p.parts:
                continue
            global_md.setdefault(p.name, []).append(p)

        total_err = 0
        failures = []
        for d in skill_dirs:
            rep = validate_skills.validate_skill(d, global_refs=global_refs, global_md=global_md)
            if rep.errors:
                total_err += len(rep.errors)
                failures.append(f"{d.name}: {[f.message for f in rep.errors]}")

        self.assertEqual(
            total_err, 0,
            f"валидатор нашёл {total_err} ошибок в skills/:\n" + "\n".join(failures),
        )


class TestRepositoryDocumentation(unittest.TestCase):
    """Замки против рассинхрона документации с фактическим кодом."""

    def test_reference_count_must_match_actual_files(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            refs = root / "skills" / "1c-example" / "references"
            refs.mkdir(parents=True)
            (refs / "one.md").write_text("one", encoding="utf-8")
            (refs / "two.md").write_text("two", encoding="utf-8")
            (root / "README.md").write_text(
                "**1 скилов** + 1 карточки-ссылки\n"
                "**Текущий: ✅ v1.0.0** — 1 скилов + 1 references\n",
                encoding="utf-8",
            )

            errors = validate_skills.check_reference_count(root)
            self.assertEqual(len(errors), 2, errors)
            self.assertTrue(all("фактически 2" in error for error in errors), errors)

    def write_java_fixture(self, root: Path, readme_java: str) -> None:
        workflow = root / ".github" / "workflows" / "bsl-ls-live.yml"
        workflow.parent.mkdir(parents=True)
        workflow.write_text('java-version: "21"\n', encoding="utf-8")
        (root / "README.md").write_text(readme_java, encoding="utf-8")
        skill = root / "skills" / "1c-code-review" / "SKILL.md"
        skill.parent.mkdir(parents=True)
        skill.write_text("Требуется Java 21+.\n", encoding="utf-8")
        script = root / "scripts" / "bsl_ls_analyze.py"
        script.parent.mkdir(parents=True)
        script.write_text("# Требуется Java 21+\n", encoding="utf-8")

    def test_java_11_is_rejected_when_ci_uses_21(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self.write_java_fixture(root, "Требуется Java 11+.\n")
            errors = validate_skills.check_java_requirements(root)
            self.assertTrue(
                any("Java 11+" in error and "CI Java 21+" in error for error in errors),
                errors,
            )

    def test_review_intro_must_describe_three_layers(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            skill = root / "skills" / "1c-code-review" / "SKILL.md"
            skill.parent.mkdir(parents=True)
            skill.write_text(
                "Проверка — два детерминированных слоя: checkbsl_scan.py, "
                "bsl_ls_analyze.py и metadata_scan.py.\n"
                "## Место в конвейере\n",
                encoding="utf-8",
            )
            errors = validate_skills.check_review_intro(root)
            self.assertTrue(any("три детерминированных слоя" in error for error in errors), errors)

    def test_review_intro_must_mention_every_script(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            skill = root / "skills" / "1c-code-review" / "SKILL.md"
            skill.parent.mkdir(parents=True)
            skill.write_text(
                "Проверка — три\nдетерминированных слоя: "
                "checkbsl_scan.py и bsl_ls_analyze.py.\n"
                "## Место в конвейере\n",
                encoding="utf-8",
            )
            errors = validate_skills.check_review_intro(root)
            self.assertEqual(len(errors), 1, errors)
            self.assertIn("metadata_scan.py", errors[0])

    def test_real_repository_documentation_passes(self):
        report = validate_skills.validate_repository(REPO_ROOT)
        self.assertEqual(
            [finding.message for finding in report.errors],
            [],
            "реальная документация не прошла замки документного дрейфа",
        )


if __name__ == "__main__":
    unittest.main(verbosity=2)
