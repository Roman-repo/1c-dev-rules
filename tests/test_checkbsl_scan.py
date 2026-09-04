#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Тесты для scripts/checkbsl_scan.py (детерминированный regex-слой checkbsl).

Запуск без зависимостей:
    python3 -m unittest tests.test_checkbsl_scan -v
или (из корня репо):
    python3 -m unittest discover tests -v
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

import checkbsl_scan as scanner  # noqa: E402  (импорт после правки sys.path)


def scan_code(code: str, filename: str = "Module.bsl", **kw):
    """Сканирует строку как модуль с заданным именем файла (важно для scope-правил)."""
    return scanner.scan_text(code, filename, **kw)


def keys(findings):
    return [f.key for f in findings]


class TestSplitComment(unittest.TestCase):
    """Деление строки на код и комментарий (// внутри строк не трогаем)."""

    def test_plain_comment(self):
        code, comment = scanner.split_comment("А = 1; // TODO: переделать")
        self.assertEqual(code, "А = 1; ")
        self.assertEqual(comment, " TODO: переделать")

    def test_slashes_inside_string(self):
        code, comment = scanner.split_comment('А = "http://x//y"; // коммент')
        self.assertEqual(code, 'А = "http://x//y"; ')
        self.assertEqual(comment, " коммент")

    def test_doubled_quote_in_string(self):
        code, comment = scanner.split_comment('А = "он сказал ""//"""; // хвост')
        self.assertEqual(code, 'А = "он сказал ""//"""; ')
        self.assertEqual(comment, " хвост")

    def test_no_comment(self):
        code, comment = scanner.split_comment("А = 1;")
        self.assertEqual(code, "А = 1;")
        self.assertIsNone(comment)


class TestMaskStrings(unittest.TestCase):
    def test_content_masked(self):
        self.assertEqual(scanner.mask_strings('А = "123abc" + Б;'),
                         'А = "......" + Б;')

    def test_structure_preserved(self):
        masked = scanner.mask_strings('Если Стр = "" Тогда')
        self.assertNotIn("Если", masked[4:])  # код вне строк не трогаем
        self.assertIn("Если", masked[:4])


class TestLoopTracking(unittest.TestCase):
    """UseQueryInALoop: только внутри «… Цикл» / «Для Каждого … Цикл»."""

    def test_query_in_for_loop(self):
        code = "Для Каждого Строка Из ТЗ Цикл\n\tЗапрос.Выполнить();\nКонецЦикла;"
        self.assertIn("UseQueryInALoop", keys(scan_code(code)))

    def test_query_in_while_loop(self):
        code = "Пока Выборка.Следующий() Цикл\n\tТЗ = Запрос.Выгрузить();\nКонецЦикла;"
        self.assertIn("UseQueryInALoop", keys(scan_code(code)))

    def test_query_outside_loop(self):
        code = "ТЗ = Запрос.Выполнить().Выгрузить();"
        self.assertNotIn("UseQueryInALoop", keys(scan_code(code)))

    def test_after_loop_ends(self):
        code = ("Для Каждого Строка Из ТЗ Цикл\nКонецЦикла;\n"
                "Результат = Запрос.Выполнить();")
        self.assertNotIn("UseQueryInALoop", keys(scan_code(code)))

    def test_nested_loops(self):
        code = ("Для А Из М Цикл\n\tПока Истина Цикл\n\t\tЗапрос.Выполнить();\n"
                "\tКонецЦикла;\nКонецЦикла;")
        self.assertIn("UseQueryInALoop", keys(scan_code(code)))


class TestVirtualTableFilter(unittest.TestCase):
    """VirtualTablesWithoutInnerFilter: голая ВТ = 🔴 №733 (ищем и в тексте запроса)."""

    def test_bare_slice_in_query_text(self):
        code = ('Запрос.Текст = "ВЫБРАТЬ *\n'
                '| ИЗ РегистрСведений.Товары.СрезПоследних КАК Срез"')
        self.assertIn("VirtualTablesWithoutInnerFilter", keys(scan_code(code)))

    def test_empty_parens(self):
        code = 'Запрос.Текст = "| ИЗ РегистрНакопления.Остатки()"'
        self.assertIn("VirtualTablesWithoutInnerFilter", keys(scan_code(code)))

    def test_slice_with_params_ok(self):
        code = ('Запрос.Текст = "ВЫБРАТЬ *\n'
                '| ИЗ РегистрСведений.Товары.СрезПоследних(&Дата, Номенклатура = &Н)"')
        self.assertNotIn("VirtualTablesWithoutInnerFilter", keys(scan_code(code)))

    def test_alias_after_kak_ok(self):
        # «КАК Остатки» — алиас, не вызов ВТ; вызов с периодом и отбором — чист
        code = 'Запрос.Текст = "| КАК Остатки ИЗ РегистрНакопления.Остатки(&Период, Склад = &С)"'
        self.assertNotIn("VirtualTablesWithoutInnerFilter", keys(scan_code(code)))

    def test_empty_period_param_fires(self):
        # «СрезПоследних(, Отбор)» — отбор есть, периода нет: ВТ по всей истории
        # (живой тест 0.27: ТОИР, торо_ВыбытиеОбъектаРемонта, строка 57)
        code = ('Запрос.Текст = "| ЛЕВОЕ СОЕДИНЕНИЕ'
                ' РегистрСведений.торо_Статусы.СрезПоследних(, Регистратор <> &Ссылка)"')
        self.assertIn("VirtualTablesWithoutInnerFilter", keys(scan_code(code)))

    def test_empty_period_param_ostatki_fires(self):
        code = 'Запрос.Текст = "| ИЗ РегистрНакопления.Остатки(, Склад = &С)"'
        self.assertIn("VirtualTablesWithoutInnerFilter", keys(scan_code(code)))

    def test_inside_identifier_ok(self):
        code = "ВЫБРАТЬ РаботыСрезПоследних.Период ИЗ РаботыСрезПоследних"
        self.assertNotIn("VirtualTablesWithoutInnerFilter", keys(scan_code(code)))

    def test_multiline_empty_period_fires(self):
        # «СрезПоследних(» и «, Отбор)» на разных строках — построчный проход
        # этого не видит, склейка литерала ловит (0.29.0)
        code = ("Запрос.Текст = \"ВЫБРАТЬ *\n"
                "| ИЗ РегистрСведений.Товары.СрезПоследних(\n"
                "| , Номенклатура = &Н) КАК Срез\"")
        found = [f for f in scan_code(code)
                 if f.key == "VirtualTablesWithoutInnerFilter"]
        self.assertEqual(len(found), 1)
        self.assertEqual(found[0].line, 2)  # строка начала вызова ВТ

    def test_multiline_with_period_ok(self):
        code = ("Запрос.Текст = \"ВЫБРАТЬ *\n"
                "| ИЗ РегистрСведений.Товары.СрезПоследних(\n"
                "| &Дата, Номенклатура = &Н) КАК Срез\"")
        self.assertNotIn("VirtualTablesWithoutInnerFilter", keys(scan_code(code)))

    def test_multiline_single_line_not_duplicated(self):
        # однострочный вызов ловится основным циклом — склейка не должна
        # давать вторую находку на ту же строку
        code = 'Запрос.Текст = "| ИЗ РегистрНакопления.Остатки(, Склад = &С)"'
        found = [f for f in scan_code(code)
                 if f.key == "VirtualTablesWithoutInnerFilter"]
        self.assertEqual(len(found), 1)

    def test_multiline_escaped_quotes(self):
        # удвоенная кавычка внутри литерала не закрывает строку
        code = ("Запрос.Текст = \"ВЫБРАТЬ \"\"поле\"\" *\n"
                "| ИЗ РегистрНакопления.Остатки(\n"
                "| , Склад = &С)\"")
        self.assertIn("VirtualTablesWithoutInnerFilter", keys(scan_code(code)))


class TestDeprecatedMethods(unittest.TestCase):
    def test_soobshit(self):
        self.assertIn("DeprecatedMethodMessage", keys(scan_code("Сообщить(\"Готово\");")))

    def test_soobshit_polzovatelyu_ok(self):
        self.assertNotIn("DeprecatedMethodMessage",
                         keys(scan_code("ОбщегоНазначения.СообщитьПользователю(\"Текст\");")))

    def test_nayti_bare(self):
        self.assertIn("DeprecatedFind", keys(scan_code("Поз = Найти(Строка, \"х\");")))

    def test_nayti_with_dot_ok(self):
        self.assertNotIn("DeprecatedFind",
                         keys(scan_code("Строка_ = ЗакрываемыеРемонты.Найти(Значение);")))

    def test_strnayti_ok(self):
        self.assertNotIn("DeprecatedFind", keys(scan_code("Поз = СтрНайти(Строка, \"х\");")))

    def test_this_form(self):
        self.assertIn("DeprecatedThisForm", keys(scan_code("ЭтаФорма.ОбновитьОтображениеДанных();")))

    def test_get_form(self):
        self.assertIn("DeprecatedMethodGetForm", keys(scan_code("Ф = ПолучитьФорму(\"Форма\",);")))

    def test_sync_methods(self):
        for call in ("Предупреждение(\"!\");", "Вопрос(\"Да?\");",
                     "ОткрытьФормуМодально(\"Ф\");", "ВвестиЧисло(Ч, \"Число\");"):
            self.assertIn("SynchronousMethods", keys(scan_code(call)), call)


class TestMagicNumber(unittest.TestCase):
    def test_hundred(self):
        self.assertIn("MagicNumber", keys(scan_code("Процент = Количество * 100;")))

    def test_whitelisted(self):
        self.assertNotIn("MagicNumber", keys(scan_code("Период = Период + 1;")))

    def test_number_in_string_ok(self):
        self.assertNotIn("MagicNumber", keys(scan_code('Код = "12345";')))

    def test_number_in_comment_ok(self):
        self.assertNotIn("MagicNumber", keys(scan_code("А = Б; // колонки 53-58")))

    def test_allow_number(self):
        f = scan_code("Процент = Количество * 100;", allow_numbers=["100"])
        self.assertNotIn("MagicNumber", keys(f))

    def test_digits_in_identifier_ok(self):
        # живой ложняк 0.29 (торо_ЗаявкаНаРемонт:13): «20» — часть имени модуля
        self.assertNotIn("MagicNumber", keys(scan_code(
            "торо_ЗаполнениеДокументов20.ЗаполнениеНаОсновании(Основание, ЭтотОбъект, Ложь);")))

    def test_digits_in_dotted_identifier_ok(self):
        self.assertNotIn("MagicNumber", keys(scan_code("Значение = Поле1.Реквизит2;")))

    def test_real_number_still_fires_beside_identifier(self):
        # цифра в имени — не находка, а отдельный литерал — находка
        f = [x for x in scan_code("Индекс2 = 55;") if x.key == "MagicNumber"]
        self.assertEqual(len(f), 1)

    def test_number_inside_multiline_query_ok(self):
        # строки внутри многострочного литерала запроса — не код (0.29.1):
        # цифры запроса не должны давать MagicNumber
        code = ("Запрос.Текст = \"ВЫБРАТЬ\n"
                "|\tСУММА(ЕСТЬNULL(Таб.Стоимость, 0) * 100) КАК Итог\n"
                "|ИЗ Таблица КАК Таб\"")
        self.assertNotIn("MagicNumber", keys(scan_code(code)))


class TestScopes(unittest.TestCase):
    """scope-правила: серверный контекст и модули форм — по содержимому/пути файла."""

    SERVER_MODULE = "#Если Сервер Тогда\nПроцедура П()\n\tВыполнить(Код);\nКонецПроцедуры\n#КонецЕсли"

    def test_execute_export_on_server(self):
        self.assertIn("ExecuteExport", keys(scan_code(self.SERVER_MODULE)))

    def test_execute_export_not_in_client_module(self):
        code = "#Если Клиент Тогда\nПроцедура П()\n\tВыполнить(Код);\nКонецПроцедуры\n#КонецЕсли"
        self.assertNotIn("ExecuteExport", keys(scan_code(code)))

    def test_query_execute_is_not_dynamic_code(self):
        self.assertNotIn("ExecuteExport",
                         keys(scan_code(self.SERVER_MODULE.replace("Выполнить(Код)",
                                                                  "Запрос.Выполнить()"))))

    def test_form_data_to_value_in_form_module(self):
        code = "Значение = ДанныеФормыВЗначение(Объект);"
        self.assertIn("DeprecatedMethodFormDataToValue",
                      keys(scan_code(code, "Forms/ФормаДокумента/Form/Module.bsl")))

    def test_form_data_to_value_outside_form_ok(self):
        code = "Значение = ДанныеФормыВЗначение(Объект);"
        self.assertNotIn("DeprecatedMethodFormDataToValue",
                         keys(scan_code(code, "CommonModules/ОбщийМодуль/Module.bsl")))


class TestCommentsAndStyle(unittest.TestCase):
    def test_todo(self):
        self.assertIn("TodoTagPresence", keys(scan_code("А = 1; // TODO доделать")))

    def test_fixme(self):
        self.assertIn("FixmeTagPresence", keys(scan_code("// FIXME: баг")))

    def test_commented_out_code(self):
        self.assertIn("CommentedOutCodeLine",
                      keys(scan_code("//\tЕсли Не Отказ Тогда\n//\t\tВозврат;\n//\tКонецЕсли;")))

    def test_commented_out_assignment(self):
        self.assertIn("CommentedOutCodeLine",
                      keys(scan_code("//Объект.Дата = ТекущаяДата();")))

    def test_descriptive_comment_with_assignment_text_ok(self):
        # живой ложняк 0.29 (field-report code-review-eval): присваивание в
        # середине описательного комментария — НЕ закомментированный код
        self.assertNotIn("CommentedOutCodeLine", keys(scan_code(
            '// Движения по регистру "Внешние основания для работ",'
            ' простановка Обработано = Истина')))

    def test_normal_comment_ok(self):
        self.assertNotIn("CommentedOutCodeLine",
                         keys(scan_code("// Рассчитывает стоимость по формуле сметы")))

    def test_doc_comment_proc_prose_ok(self):
        # живой ложняк 0.29 (торо_ЗаявкаНаРемонт:475/739/1066): документирующий
        # комментарий «// Процедура выполняет …» — не закомментированный код
        for line in ('// Процедура выполняет движения документа по регистру "торо_Плановые".',
                     '// Функция формирует таблицу плановых материальных затрат ',
                     '// Функция формирует таблицу плановых затрат запчастей '):
            self.assertNotIn("CommentedOutCodeLine", keys(scan_code(line)), line)

    def test_commented_proc_signature_fires(self):
        # закомментированная сигнатура (имя + скобка) — по-прежнему находка
        self.assertIn("CommentedOutCodeLine",
                      keys(scan_code("// Процедура ПередЗаписью(Отказ);")))
        self.assertIn("CommentedOutCodeLine",
                      keys(scan_code("//Функция Рассчитать(База) Экспорт")))

    def test_lowercase_prem(self):
        self.assertIn("StyleLowercaseПерем", keys(scan_code("перем Кэш Экспорт;")))

    def test_uppercase_Prem_ok(self):
        self.assertNotIn("StyleLowercaseПерем", keys(scan_code("Перем Кэш Экспорт;")))

    def test_space_before_paren(self):
        self.assertIn("StyleSpaceBeforeParen", keys(scan_code("Если ТипЗнч(Х) = Тип (\"Строка\") Тогда")))

    def test_goto(self):
        self.assertIn("GoTo", keys(scan_code("Перейти ~Метка;")))

    def test_one_symbol_variable(self):
        self.assertIn("OneSymbolVariable", keys(scan_code("перем п;")))

    def test_one_symbol_loop_var(self):
        self.assertIn("OneSymbolVariable", keys(scan_code("Для Каждого х Из ТЗ Цикл\nКонецЦикла;")))


class TestHardcodedLiterals(unittest.TestCase):
    def test_guid(self):
        self.assertIn("HardcodedGUID",
                      keys(scan_code("Ид = Новый УникальныйИдентификатор(\"a0b1c2d3-e4f5-6789-0abc-def012345678\");")))

    def test_path_unix(self):
        self.assertIn("HardcodedPaths", keys(scan_code('Каталог = "/home/user/data";')))

    def test_path_windows(self):
        self.assertIn("HardcodedPaths", keys(scan_code('Каталог = "C:\\\\temp";')))

    def test_email(self):
        self.assertIn("HardcodeEmail", keys(scan_code('Адрес = "user@example.com";')))

    def test_temp_files_dir(self):
        self.assertIn("TempFilesDir", keys(scan_code("Каталог = КаталогВременныхФайлов();")))


class TestScanPathsAndOutput(unittest.TestCase):
    """Файловый прогон, форматы вывода, коды возврата."""

    BAD_MODULE = "\n".join([
        "перем Кэш Экспорт;",
        "",
        "Процедура Проверить(Отказ)",
        "\tДля Каждого Строка Из Список Цикл",
        "\t\tРезультат = Запрос.Выполнить();",
        "\tКонецЦикла;",
        "\tСообщить(\"Проверено: \" + 100);",
        "КонецПроцедуры",
    ])

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.dir = Path(self._tmp.name)

    def write_module(self, relpath: str, code: str) -> Path:
        p = self.dir / relpath
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(code, encoding="utf-8")
        return p

    def test_directory_scan_finds_bsl_only(self):
        self.write_module("src/Module.bsl", self.BAD_MODULE)
        self.write_module("src/notes.md", "# не код")
        findings, nfiles, _ = scanner.scan_paths([self.dir])
        self.assertEqual(nfiles, 1)
        self.assertIn("UseQueryInALoop", keys(findings))
        self.assertIn("DeprecatedMethodMessage", keys(findings))

    def test_missing_path_raises(self):
        with self.assertRaises(FileNotFoundError):
            scanner.scan_paths([self.dir / "нет-такого"])

    def test_severity_order_red_first(self):
        self.write_module("Module.bsl", self.BAD_MODULE)
        findings, _, _ = scanner.scan_paths([self.dir])
        self.assertEqual(findings[0].sev, "red")

    def test_md_output_counts(self):
        self.write_module("Module.bsl", self.BAD_MODULE)
        findings, nfiles, _ = scanner.scan_paths([self.dir])
        md = scanner.format_md(findings, nfiles)
        self.assertIn("🔴", md)
        self.assertIn("`UseQueryInALoop`", md)
        self.assertIn("436", md)

    def test_json_output_catalog_links(self):
        self.write_module("Module.bsl", self.BAD_MODULE)
        findings, nfiles, _ = scanner.scan_paths([self.dir])
        data = json.loads(scanner.format_json(findings, nfiles))
        self.assertEqual(data["files"], 1)
        link = data["findings"][0]["catalog"]
        self.assertTrue(link.startswith("https://docs.checkbsl.org/checks/"))
        self.assertTrue(link.endswith("/"))

    def test_main_exit_1_on_red(self):
        p = self.write_module("Module.bsl", self.BAD_MODULE)
        rc = scanner.main(["--format", "json", str(p)])
        self.assertEqual(rc, 1)

    def test_main_exit_0_on_clean(self):
        p = self.write_module("Module.bsl",
                              "// Чистый модуль\nПерем Кэш;\nПроцедура Т()\n\tВозврат;\nКонецПроцедуры")
        rc = scanner.main(["--format", "json", str(p)])
        self.assertEqual(rc, 0)

    def test_main_no_input_exits_2(self):
        with self.assertRaises(SystemExit) as cm:
            scanner.main([])
        self.assertEqual(cm.exception.code, 2)


class TestDiffMode(unittest.TestCase):
    """--diff: только изменённые .bsl/.os относительно git-ссылки."""

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
    def test_diff_returns_changed_bsl(self):
        self._git("init", "-q", "-b", "main")
        (self.repo / "Keep.bsl").write_text("Переводы = 0;\n", encoding="utf-8")
        (self.repo / "Old.bsl").write_text("Сообщить(\"старое\");\n", encoding="utf-8")
        self._git("add", ".")
        self._git("commit", "-q", "-m", "init")
        (self.repo / "Old.bsl").unlink()                          # удалённый не попадает
        (self.repo / "New.bsl").write_text("Сообщить(\"новое\");\n", encoding="utf-8")
        (self.repo / "Keep.bsl").write_text("Переводы = 0;\n", encoding="utf-8")  # без изменений
        self._git("add", "-A")  # новые файлы должны попасть в индекс, иначе git их не видит
        files = [f.name for f in scanner.diff_files("HEAD", cwd=self.repo)]
        self.assertEqual(files, ["New.bsl"])


class TestOutputRobustness(unittest.TestCase):
    """О3/О4 (0.29.1): broken pipe и экранирование «|» во фрагментах."""

    def test_md_escape_pipe(self):
        self.assertEqual(scanner._md_escape("| ИЗ Справочник"), "\\| ИЗ Справочник")

    def test_format_md_escapes_pipe_in_fragment(self):
        f = [scanner.Finding("K", "yellow", "t", "", "a.bsl", 1,
                             "|\tЛЕВОЕ СОЕДИНЕНИЕ Таблица", "query")]
        md = scanner.format_md(f, 1)
        self.assertIn("\\|\tЛЕВОЕ СОЕДИНЕНИЕ", md)
        # сырой «|» внутри ячейки не должен остаться (сломал бы таблицу)
        row = [l for l in md.splitlines() if "ЛЕВОЕ СОЕДИНЕНИЕ" in l][0]
        self.assertNotIn("`|\t", row)

    def test_safe_print_survives_broken_pipe(self):
        class BrokenStdout:
            def write(self, s):
                raise BrokenPipeError("pipe closed")

            def flush(self):
                raise BrokenPipeError("pipe closed")

            def fileno(self):
                raise OSError("no fd")  # fallback в devnull недоступен — тоже ок

        import contextlib
        old = sys.stdout
        sys.stdout = BrokenStdout()
        try:
            scanner.safe_print("текст в закрытый пайп")  # не должно бросать
        finally:
            sys.stdout = old


class TestProjectConfig(unittest.TestCase):
    """allow-numbers из .checkbsl_scan.json проекта (0.30.0)."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.dir = Path(self._tmp.name)

    def test_missing_config_is_empty(self):
        self.assertEqual(scanner.load_project_allow_numbers(self.dir), [])

    def test_config_numbers_returned(self):
        (self.dir / scanner.PROJECT_CONFIG).write_text(
            json.dumps({"allow-numbers": ["100", "3600"]}), encoding="utf-8")
        self.assertEqual(scanner.load_project_allow_numbers(self.dir),
                         ["100", "3600"])

    def test_config_numbers_silence_magic(self):
        # e2e: allow из конфига гасит MagicNumber на «осмысленном» числе
        module = self.dir / "Module.bsl"
        module.write_text("ПроцентИзноса = 100;\n", encoding="utf-8")
        (self.dir / scanner.PROJECT_CONFIG).write_text(
            json.dumps({"allow-numbers": ["100"]}), encoding="utf-8")
        findings, _, _ = scanner.scan_paths(
            [module], scanner.load_project_allow_numbers(self.dir))
        self.assertEqual(keys(findings), [])

    def test_malformed_config_is_loud_error(self):
        (self.dir / scanner.PROJECT_CONFIG).write_text(
            json.dumps({"allow-numbers": "100"}), encoding="utf-8")
        with self.assertRaises(RuntimeError):
            scanner.load_project_allow_numbers(self.dir)


class TestSuppress(unittest.TestCase):
    """suppress.json: решения Ревьюера «не баг» вычитаются из находок (0.30.0)."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.dir = Path(self._tmp.name)
        self.module = self.dir / "Module.bsl"
        self.module.write_text('Сообщить("а");\nБ = 2;\nСообщить("б");\n',
                               encoding="utf-8")
        # без подавлений: DeprecatedMethodMessage на строках 1 и 3
        self.assertEqual(
            [f.line for f in scanner.scan_paths([self.module])[0]
             if f.key == "DeprecatedMethodMessage"], [1, 3])

    def _entry(self, **kw):
        e = {"key": "DeprecatedMethodMessage", "file": str(self.module),
             "reason": "не баг — тест"}
        e.update(kw)
        return e

    def test_load_suppress_validates_reason(self):
        p = self.dir / "suppress.json"
        p.write_text(json.dumps([{"key": "X", "file": "Y.bsl"}]),
                     encoding="utf-8")
        with self.assertRaises(RuntimeError):  # подавление без обоснования
            scanner.load_suppress(p)

    def test_load_suppress_rejects_non_int_line(self):
        p = self.dir / "suppress.json"
        p.write_text(json.dumps([self._entry(line="1")]), encoding="utf-8")
        with self.assertRaises(RuntimeError):
            scanner.load_suppress(p)

    def test_suppress_exact_line(self):
        findings, _, suppressed = scanner.scan_paths(
            [self.module], suppress=[self._entry(line=1)])
        self.assertEqual([f.line for f in findings
                          if f.key == "DeprecatedMethodMessage"], [3])
        self.assertEqual(len(suppressed), 1)
        self.assertEqual(suppressed[0].line, 1)

    def test_suppress_whole_file_without_line(self):
        findings, _, suppressed = scanner.scan_paths(
            [self.module], suppress=[self._entry(line=None)])
        self.assertNotIn("DeprecatedMethodMessage", keys(findings))
        self.assertEqual(len(suppressed), 2)

    def test_suppress_other_key_no_effect(self):
        findings, _, suppressed = scanner.scan_paths(
            [self.module], suppress=[self._entry(key="MagicNumber")])
        self.assertEqual(len(findings), 2)
        self.assertEqual(suppressed, [])

    def test_cli_flag_counts_suppressed(self):
        import contextlib
        import io
        p = self.dir / "suppress.json"
        p.write_text(json.dumps([self._entry(line=1)]), encoding="utf-8")
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            rc = scanner.main([str(self.module), "--suppress", str(p),
                               "--format", "json"])
        data = json.loads(out.getvalue())
        self.assertEqual(rc, 0)                     # оставшиеся находки 🟡
        self.assertEqual(data["suppressed"], 1)
        self.assertEqual(len(data["findings"]), 1)

    def test_cli_broken_suppress_file_exits_2(self):
        import contextlib
        import io
        p = self.dir / "suppress.json"
        p.write_text(json.dumps([{"key": "X"}]), encoding="utf-8")
        err = io.StringIO()
        with contextlib.redirect_stdout(io.StringIO()), \
                contextlib.redirect_stderr(err):
            rc = scanner.main([str(self.module), "--suppress", str(p)])
        self.assertEqual(rc, 2)
        self.assertIn("reason", err.getvalue())


if __name__ == "__main__":
    unittest.main()
