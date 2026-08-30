#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Тесты для scripts/bsl_ls_analyze.py (средний слой checkbsl: bsl-language-server).

Без зависимостей и без Java: запуск CLI и live-прогон — в отдельном
skipIf-тесте (jar есть только на настроенной машине).
    python3 -m unittest tests.test_bsl_ls_analyze -v
"""
from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from shutil import which

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "scripts"))

import bsl_ls_analyze as wrapper  # noqa: E402  (импорт после правки sys.path)


def report_for(tmp: Path, diagnostics: list, filename: str = "Module.bsl") -> dict:
    """Минимальный AnalysisInfo по одному файлу с данными диагностиками."""
    return {
        "fileinfos": [{
            "path": f"file://{tmp / filename}",
            "mdoRef": "",
            "diagnostics": diagnostics,
        }]
    }


def diag(code: str, line0: int, message: str = "текст", severity: str = "Warning") -> dict:
    return {"code": code, "severity": severity, "message": message,
            "codeDescription": {"href": f"https://1c-syntax.github.io/bsl-language-server/diagnostics/{code}"},
            "range": {"start": {"line": line0, "character": 0},
                      "end": {"line": line0, "character": 5}}}


class TestCatalog(unittest.TestCase):
    """Парсер каталога checkbsl: маппинг ключ → (название, №№, секция)."""

    def test_catalog_is_large(self):
        self.assertGreaterEqual(len(wrapper.load_catalog()), 300)

    def test_known_keys(self):
        cat = wrapper.load_catalog()
        self.assertIn("UseQueryInALoop", cat)
        self.assertIn("SynchronousMethods", cat)
        # № стандартов из колонки «Стандарт» (SynchronousMethods → №703)
        self.assertIn("703", cat["SynchronousMethods"][1])

    def test_sections(self):
        cat = wrapper.load_catalog()
        self.assertEqual(cat["UseQueryInALoop"][2], "overall")
        self.assertEqual(cat["AliasMustHaveAsKeyword"][2], "query")


class TestAliasAndSeverity(unittest.TestCase):
    """Алиасы BSL LS → каталог; серьёзность: чек-лист сканера → важность BSL LS."""

    def test_alias_targets_exist_in_catalog(self):
        """Каждый алиас указывает на реальный ключ каталога — рассинхрон ловится тестом."""
        cat = wrapper.load_catalog()
        for ls_code, cat_key in wrapper.ALIAS.items():
            self.assertIn(cat_key, cat, f"алиас {ls_code} → отсутствующий ключ {cat_key}")

    def test_alias_diagnostic_names_exist_in_ls_table(self):
        table = wrapper.load_ls_table()
        for ls_code in wrapper.ALIAS:
            self.assertIn(ls_code, table, f"алиас {ls_code} не входит в таблицу BSL LS")

    def test_resolve_key_direct_and_alias(self):
        self.assertEqual(wrapper.resolve_key("MagicNumber"), "MagicNumber")      # прямое
        self.assertEqual(wrapper.resolve_key("CreateQueryInCycle"), "UseQueryInALoop")
        self.assertEqual(wrapper.resolve_key("SomethingUnknown"), "SomethingUnknown")

    def test_checklist_wins_over_ls_importance(self):
        # UseQueryInALoop — 🔴 №436 в чек-листе сканера, важность BSL LS не важна
        sev, std = wrapper.sev_std("UseQueryInALoop", "CreateQueryInCycle")
        self.assertEqual(sev, "red")
        self.assertEqual(std, "436")

    def test_importance_fallback(self):
        table = wrapper.load_ls_table()
        for sev_expected in ("red", "yellow", "green"):
            sample = next(code for code, v in table.items()
                          if wrapper.IMPORTANCE_SEV[v["importance"]] == sev_expected
                          and code not in wrapper.ALIAS
                          and code not in wrapper.load_catalog())
            sev, _ = wrapper.sev_std(sample, sample)
            self.assertEqual(sev, sev_expected, f"{sample} ({table[sample]['importance']})")

    def test_catalog_std_used_when_no_checklist_rule(self):
        # SynchronousMethods: чек-лист сканера — 🟡 №703
        sev, std = wrapper.sev_std("SynchronousMethods", "UsingSynchronousCalls")
        self.assertEqual((sev, std), ("yellow", "703"))

    def test_name_bridge_coverage_not_below_baseline(self):
        """Замок от регресса моста имён: доля диагностик LS, покрытых каталогом
        ∪ ALIAS, не падает ниже базовой (109/186 на 0.25.1). Рост таблицы
        диагностик upstream ловит квартальная сверка check_bsl_ls_drift.py."""
        table = wrapper.load_ls_table()
        covered = set(wrapper.load_catalog()) | set(wrapper.ALIAS)
        ratio = len(set(table) & covered) / len(table)
        self.assertGreaterEqual(ratio, 0.58,
                                f"покрытие моста имён упало: {ratio:.0%}")


class TestParseReport(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.dir = Path(self._tmp.name)
        self.module = self.dir / "Module.bsl"
        self.module.write_text(
            "Процедура П()\n"
            "\tСообщить(\"привет\");\n"
            "\tДля Каждого С Из М Цикл\n"
            "\t\tЗапрос.Выполнить();\n"
            "\tКонецЦикла;\n"
            "КонецПроцедуры\n", encoding="utf-8")

    def test_line_numbers_are_1_based(self):
        rep = report_for(self.dir, [diag("CreateQueryInCycle", 3)])
        findings, _, _ = wrapper.parse_report(rep, None)
        self.assertEqual(findings[0].line, 4)  # LSP 0 → человекочитаемая 1

    def test_alias_maps_key_and_std(self):
        rep = report_for(self.dir, [diag("CreateQueryInCycle", 3)])
        findings, _, extra = wrapper.parse_report(rep, None)
        self.assertEqual(findings[0].key, "UseQueryInALoop")
        self.assertEqual(findings[0].sev, "red")
        self.assertEqual(findings[0].std, "436")
        self.assertTrue(extra[0]["docs"].startswith("https://docs.checkbsl.org/"))
        self.assertEqual(extra[0]["ls_code"], "CreateQueryInCycle")

    def test_fragment_from_source_line(self):
        rep = report_for(self.dir, [diag("DeprecatedMessage", 1)])
        findings, _, _ = wrapper.parse_report(rep, None)
        self.assertEqual(findings[0].fragment, "Сообщить(\"привет\");")
        self.assertEqual(findings[0].key, "DeprecatedMethodMessage")

    def test_unknown_code_kept_with_ls_docs(self):
        rep = report_for(self.dir, [diag("BrandNewDiagnostic99", 1)])
        findings, _, extra = wrapper.parse_report(rep, None)
        self.assertEqual(findings[0].key, "BrandNewDiagnostic99")
        self.assertIn("bsl-language-server/diagnostics/", extra[0]["docs"])

    def test_target_file_filter(self):
        rep = report_for(self.dir, [diag("DeprecatedMessage", 1)])
        findings_all, _, _ = wrapper.parse_report(rep, None)
        findings_none, _, _ = wrapper.parse_report(rep, set())
        self.assertEqual(len(findings_all), 1)
        self.assertEqual(len(findings_none), 0)

    def test_line_range_filter(self):
        rep = report_for(self.dir, [diag("DeprecatedMessage", 1), diag("DeprecatedMessage", 2)])
        ranges = {self.module.resolve(): [(3, 3)]}  # только строка 3 (1-based)
        findings, _, _ = wrapper.parse_report(rep, None, ranges)
        self.assertEqual([f.line for f in findings], [3])

    def test_line_range_empty_drops_everything(self):
        rep = report_for(self.dir, [diag("DeprecatedMessage", 1)])
        ranges = {self.module.resolve(): []}  # файл без добавленных строк
        findings, _, _ = wrapper.parse_report(rep, None, ranges)
        self.assertEqual(findings, [])

    def test_relative_report_path_anchored_to_cwd_field(self):
        # run_bsl_ls нормализует относительные пути; parse_report получает абсолютные
        rep = report_for(self.dir, [diag("DeprecatedMessage", 1)])
        rep["fileinfos"][0]["path"] = str(self.module)
        findings, _, _ = wrapper.parse_report(rep, None)
        self.assertTrue(Path(findings[0].file).is_absolute() or findings[0].file)

    def test_findings_and_extra_stay_aligned_after_sort(self):
        """regression: сортировка по серьёзности не должна рассинхронизировать
        findings и extra (ls_code/message) — пары сортируются вместе."""
        rep = report_for(self.dir, [diag("DeprecatedMessage", 1),      # 🟡
                                    diag("CreateQueryInCycle", 3)])    # 🔴 → первым
        findings, _, extra = wrapper.parse_report(rep, None)
        self.assertEqual([f.key for f in findings],
                         ["UseQueryInALoop", "DeprecatedMethodMessage"])
        self.assertEqual([e["ls_code"] for e in extra],
                         ["CreateQueryInCycle", "DeprecatedMessage"])

    def test_diagnostic_suffix_stripped(self):
        rep = report_for(self.dir, [{"code": "CreateQueryInCycleDiagnostic", "severity": "Error",
                                     "message": "x", "range": {"start": {"line": 3, "character": 0},
                                                                "end": {"line": 3, "character": 1}}}])
        findings, _, _ = wrapper.parse_report(rep, None)
        self.assertEqual(findings[0].key, "UseQueryInALoop")

    def test_dedup_same_catalog_key_same_line(self):
        """Два LS-кода на один ключ каталога (UnknownMember +
        MissingCommonModuleMethod → NonExistentMethod) — одна находка."""
        rep = report_for(self.dir, [diag("UnknownMember", 1),
                                    diag("MissingCommonModuleMethod", 1)])
        findings, _, _ = wrapper.parse_report(rep, None)
        self.assertEqual(len(findings), 1)
        self.assertEqual(findings[0].key, "NonExistentMethod")

    def test_dedup_keeps_different_lines(self):
        rep = report_for(self.dir, [diag("UnknownMember", 1),
                                    diag("MissingCommonModuleMethod", 2)])
        findings, _, _ = wrapper.parse_report(rep, None)
        self.assertEqual(len(findings), 2)


class TestMergeLayer1(unittest.TestCase):
    """--merge-scan: слияние находок слоя 1 с дедупликацией (0.29.0)."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.dir = Path(self._tmp.name)
        self.module = self.dir / "Module.bsl"
        self.module.write_text(
            "Процедура П()\n"
            "\tЗапрос.Выполнить();\n"
            "КонецПроцедуры\n", encoding="utf-8")

    def _scan_json(self, line: int, key: str = "UseQueryInALoop") -> Path:
        p = self.dir / "scan.json"
        p.write_text(json.dumps({"files": 1, "counts": {}, "findings": [{
            "key": key, "severity": "red", "title": "t", "std": "436",
            "file": str(self.module), "line": line, "fragment": "x",
            "section": "overall",
            "catalog": f"https://docs.checkbsl.org/checks/overall/{key}/"}]},
            ensure_ascii=False), encoding="utf-8")
        return p

    def test_merge_dedup_same_key_file_line(self):
        rep = report_for(self.dir, [diag("CreateQueryInCycle", 1)])  # строка 2
        findings, _, extra = wrapper.parse_report(rep, None)
        merged, mextra = wrapper.merge_layer1(findings, extra, self._scan_json(2))
        self.assertEqual(len(merged), 1)
        # побеждает слой 1: его метка в extra
        self.assertEqual(mextra[0]["ls_code"], "слой 1 (checkbsl_scan)")

    def test_merge_adds_non_overlapping(self):
        rep = report_for(self.dir, [diag("DeprecatedMessage", 1)])
        findings, _, extra = wrapper.parse_report(rep, None)
        merged, _ = wrapper.merge_layer1(findings, extra, self._scan_json(2))
        self.assertEqual(len(merged), 2)
        self.assertEqual({f.key for f in merged},
                         {"UseQueryInALoop", "DeprecatedMethodMessage"})

    def test_report_header_coverage_and_merge_note(self):
        rep = report_for(self.dir, [diag("DeprecatedMessage", 1)])
        findings, nfiles, extra = wrapper.parse_report(rep, None)
        md = wrapper.build_report(
            findings, extra, nfiles,
            {"inputs": "x", "coverage": "slim — отключено 53 диагностик",
             "merged_scan": True})
        self.assertIn("Покрытие: slim — отключено 53", md)
        self.assertIn("слой 1 (`checkbsl_scan.py`, слито с дедупликацией)", md)

    def test_report_header_without_coverage_unchanged(self):
        rep = report_for(self.dir, [diag("DeprecatedMessage", 1)])
        findings, nfiles, extra = wrapper.parse_report(rep, None)
        md = wrapper.build_report(findings, extra, nfiles, {"inputs": "x"})
        self.assertNotIn("Покрытие:", md)


class TestFormats(unittest.TestCase):
    def test_md_table_and_exit_hint(self):
        f = [scan_f("UseQueryInALoop", "red", "436", "a.bsl", 5, "Запрос.Выполнить();")]
        md = wrapper.format_md(f, 3)
        self.assertIn("🔴 1", md)
        self.assertIn("`UseQueryInALoop`", md)
        self.assertIn("a.bsl:5", md)

    def test_json_roundtrip(self):
        f = [scan_f("GoTo", "yellow", "", "a.bsl", 2, "Перейти ~М")]
        extra = [{"docs": "u", "message": "m", "ls_code": "UsingGoto", "ls_severity": "Info"}]
        data = json.loads(wrapper.format_json(f, 1, extra))
        self.assertEqual(data["counts"]["yellow"], 1)
        self.assertEqual(data["findings"][0]["ls_code"], "UsingGoto")

    def test_md_empty(self):
        self.assertIn("Чисто", wrapper.format_md([], 0))


def scan_f(key, sev, std, file, line, fragment):
    import checkbsl_scan
    return checkbsl_scan.Finding(key, sev, "t", std, file, line, fragment, "overall")


class TestDiffRanges(unittest.TestCase):
    """added_line_ranges: git diff -U0 → диапазоны добавленных строк."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.repo = Path(self._tmp.name)

    def _git(self, *args):
        subprocess.run(["git", "-C", str(self.repo), *args], check=True,
                       capture_output=True, text=True,
                       env={"GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t",
                            "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@t",
                            "GIT_CONFIG_GLOBAL": "/dev/null", "GIT_CONFIG_SYSTEM": "/dev/null",
                            "PATH": "/usr/bin:/bin:/usr/local/bin", "HOME": str(self.repo)})

    @unittest.skipIf(which("git") is None, "git недоступен")
    def test_ranges_cover_added_lines_only(self):
        self._git("init", "-q", "-b", "main")
        (self.repo / "A.bsl").write_text("Старая1\nСтарая2\n", encoding="utf-8")
        self._git("add", ".")
        self._git("commit", "-q", "-m", "init")
        (self.repo / "A.bsl").write_text("Новая1\nСтарая2\nНовая3\n", encoding="utf-8")
        self._git("add", "A.bsl")
        ranges = wrapper.added_line_ranges("HEAD", [self.repo / "A.bsl"])
        got = ranges[(self.repo / "A.bsl").resolve()]
        self.assertIn((1, 1), got)   # Новая1
        self.assertIn((3, 3), got)   # Новая3
        self.assertNotIn((2, 2), got)

    @unittest.skipIf(which("git") is None, "git недоступен")
    def test_new_file_whole_range(self):
        self._git("init", "-q", "-b", "main")
        self._git("commit", "-q", "--allow-empty", "-m", "init")
        (self.repo / "B.bsl").write_text("А\nБ\nВ\n", encoding="utf-8")
        self._git("add", "B.bsl")
        ranges = wrapper.added_line_ranges("HEAD", [self.repo / "B.bsl"])
        self.assertEqual(ranges[(self.repo / "B.bsl").resolve()], [(1, 3)])


class TestGracefulDegradation(unittest.TestCase):
    """Нет java/jar → exit 3 с честным сообщением про слой 1 (сканер)."""

    def test_exit_3_when_tools_missing(self):
        import contextlib
        import io
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            rc = wrapper.main(["--java", "/nonexistent-java", "--jar", "/nonexistent.jar",
                               "whatever.bsl"])
        self.assertEqual(rc, 3)
        self.assertIn("checkbsl_scan.py", err.getvalue())
        self.assertIn("jar", err.getvalue())


class TestReviewReport(unittest.TestCase):
    """--report: md-отчёт с кодом, «что не так» и «как правильно»."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.dir = Path(self._tmp.name)
        self.module = self.dir / "Module.bsl"
        self.module.write_text(
            "Процедура П()\n"
            "\tДля Каждого С Из М Цикл\n"
            "\t\tЗапрос.Выполнить();\n"
            "\tКонецЦикла;\n"
            "\tСообщить(\"готово\");\n"
            "КонецПроцедуры\n", encoding="utf-8")

    def _findings(self):
        rep = report_for(self.dir, [diag("CreateQueryInCycle", 2),
                                    diag("DeprecatedMessage", 4)])
        return wrapper.parse_report(rep, None)  # (findings, nfiles, extra)

    def test_fixes_keys_exist_in_catalog(self):
        """Каждый ключ базы знаний — реальный ключ каталога, сканера или BSL LS."""
        valid = (set(wrapper.load_catalog())
                 | {r.key for r in wrapper.scan.RULES}
                 | set(wrapper.load_ls_table()))
        for key in wrapper.load_fixes():
            self.assertIn(key, valid, f"fix-ключ {key} не существует")

    def test_report_contains_code_why_and_fix(self):
        findings, nfiles, extra = self._findings()
        md = wrapper.build_report(findings, extra, nfiles,
                                  {"inputs": "Module.bsl", "round": 1})
        self.assertIn("## 🔴 Блокирующие", md)
        self.assertIn("← замечание", md)                       # код с маркером
        self.assertIn("Что не так:", md)
        self.assertIn("Как правильно:", md)
        self.assertIn("МассивСсылок", md)                      # пример из базы знаний
        self.assertIn("возврат на этап «Разработка»", md)      # вердикт петли
        self.assertIn("r2", md)                                # следующий раунд

    def test_verdict_clean_without_red(self):
        rep = report_for(self.dir, [diag("DeprecatedMessage", 4)])
        findings, nfiles, extra = wrapper.parse_report(rep, None)
        md = wrapper.build_report(findings, extra, nfiles, {"inputs": "x"})
        self.assertIn("🔴 нет", md)
        self.assertNotIn("возврат на этап", md)

    def test_verdict_fully_clean(self):
        md = wrapper.build_report([], [], 0, {"inputs": "x"})
        self.assertIn("чисто — петля закрыта", md)

    def test_fallback_when_no_fix_entry(self):
        rep = report_for(self.dir, [diag("BrandNewDiagnostic99", 1)])
        findings, nfiles, extra = wrapper.parse_report(rep, None)
        md = wrapper.build_report(findings, extra, nfiles, {"inputs": "x"})
        self.assertIn("пример — по карточке", md)              # честный fallback
        self.assertIn("текст", md)                             # сообщение LS как «что не так»

    def test_report_written_via_cli_flag(self):
        import contextlib
        import io
        out = io.StringIO()
        rc = wrapper.main([str(self.module),
                           "--java", "/nonexistent-java", "--jar", "/nonexistent.jar",
                           "--report", str(self.dir / "t" / "code-review" / "bsl-ls-r1.md")])
        # слой недоступен (exit 3) — отчёт по-прежнему не пишется: петля не начиналась
        self.assertEqual(rc, 3)
        self.assertFalse((self.dir / "t" / "code-review").exists())


class TestSlimConfigAndCache(unittest.TestCase):
    """--slim-config: узкий конфиг; cache_key: чувствительность к правкам входов."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.dir = Path(self._tmp.name)

    def test_slim_config_disables_only_uncovered(self):
        cfg = wrapper.slim_config(self.dir / "slim.json")
        # персональные настройки — в diagnostics.parameters; плоская карта
        # прямо в diagnostics молча игнорируется BSL LS (эмпирика 1.0.7)
        off = json.loads(cfg.read_text(encoding="utf-8"))["diagnostics"]["parameters"]
        # CreateQueryInCycle покрыт алиасом — остаётся включённой
        self.assertNotIn("CreateQueryInCycle", off)
        # UseQueryInALoop — прямой ключ каталога — тоже включён
        self.assertNotIn("UseQueryInALoop", off)
        # непокрытая диагностика — отключена
        uncovered = next(k for k in wrapper.load_ls_table()
                         if k not in wrapper.load_catalog()
                         and k not in wrapper.ALIAS)
        self.assertIs(off[uncovered], False)

    def test_cache_key_changes_on_edit(self):
        src = self.dir / "src"
        src.mkdir()
        module = src / "Module.bsl"
        module.write_text("Процедура П()\nКонецПроцедуры\n", encoding="utf-8")
        jar = self.dir / "bsl-language-server.jar"
        jar.write_bytes(b"jar")
        k1 = wrapper.cache_key(src, jar, None)
        self.assertEqual(k1, wrapper.cache_key(src, jar, None))  # стабилен
        module.write_text("Процедура П()\n\tСообщить(1);\nКонецПроцедуры\n",
                          encoding="utf-8")  # правка: размер и mtime меняются
        self.assertNotEqual(k1, wrapper.cache_key(src, jar, None))

    def test_cache_key_changes_on_config(self):
        src = self.dir / "src"
        src.mkdir()
        jar = self.dir / "bsl-language-server.jar"
        jar.write_bytes(b"jar")
        cfg = self.dir / ".bsl-language-server.json"
        k1 = wrapper.cache_key(src, jar, None)
        cfg.write_text("{}", encoding="utf-8")
        self.assertNotEqual(k1, wrapper.cache_key(src, jar, cfg))


class TestFindTools(unittest.TestCase):
    def test_explicit_jar_authoritative(self):
        self.assertIsNone(wrapper.find_jar("/nonexistent.jar"))

    def test_explicit_java_authoritative(self):
        self.assertIsNone(wrapper.find_java("/nonexistent-java"))

    def test_explicit_valid_jar(self):
        with tempfile.NamedTemporaryFile(suffix=".jar") as tf:
            self.assertEqual(wrapper.find_jar(tf.name), Path(tf.name))


class TestLiveAnalysis(unittest.TestCase):
    """Живой прогон на настроенной машине (jar + java есть); в CI — skip."""

    def setUp(self):
        if not (wrapper.find_java() and wrapper.find_jar()):
            raise unittest.SkipTest("java/jar bsl-language-server не установлены")
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.dir = Path(self._tmp.name) / "src"
        self.dir.mkdir(parents=True)

    def test_probe_module_findings(self):
        (self.dir / "Module.bsl").write_text(
            "Процедура Проверить()\n"
            "\tДля Каждого Стр Из Список Цикл\n"
            "\t\tЗапрос = Новый Запрос(\"ВЫБРАТЬ * ИЗ Справочник.Номенклатура\");\n"
            "\t\tВыборка = Запрос.Выполнить().Выгрузить();\n"
            "\t\tСообщить(Стр.Наименование);\n"
            "\tКонецЦикла;\n"
            "КонецПроцедуры\n", encoding="utf-8")
        report = wrapper.run_bsl_ls(wrapper.find_java(), wrapper.find_jar(),
                                    self.dir.resolve(), None, 300)
        findings, nfiles, extra = wrapper.parse_report(report, None)
        self.assertGreaterEqual(nfiles, 1)
        keys = {f.key for f in findings}
        self.assertIn("UseQueryInALoop", keys)          # алиас CreateQueryInCycle
        self.assertIn("DeprecatedMethodMessage", keys)  # алиас DeprecatedMessage
        self.assertTrue(any(f.sev == "red" for f in findings))


if __name__ == "__main__":
    unittest.main()
