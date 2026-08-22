#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Тесты для scripts/delivery_tools.py.

Запуск без зависимостей:
    python3 -m unittest tests.test_delivery_tools -v
или (из корня репо):
    python3 tests/test_delivery_tools.py

Фикстуры — tests/fixtures/delivery/: четыре состояния задачи конвейера
(«отложено» с черновиком релиза, «принято» с боевым релизом, задача с
нарушениями всех проверяемых гейтов, задача только с карточкой 01).
"""
from __future__ import annotations

import contextlib
import io
import sys
import tempfile
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "scripts"))

import delivery_tools as dt  # noqa: E402  (импорт после правки sys.path)

FIXTURES = REPO_ROOT / "tests" / "fixtures" / "delivery"


def run_cli(cmd: str, task_dir: Path):
    """Вызов main() с перехватом stdout → (код выхода, текст)."""
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        code = dt.main(["delivery_tools.py", cmd, str(task_dir)])
    return code, buf.getvalue()


class TestMarkdownHelpers(unittest.TestCase):
    """Парсеры markdown: базовые случаи."""

    def test_table_rows_skips_separator(self):
        headers, rows = dt.table_rows("| A | B |\n|---|---|\n| 1 | 2 |")
        self.assertEqual(headers, ["A", "B"])
        self.assertEqual(rows, [["1", "2"]])

    def test_md_sections(self):
        secs = dt.md_sections("# T\nшапка\n## Один\nтекст\n## Два\nещё")
        self.assertEqual(secs["Один"].strip(), "текст")
        self.assertIn("шапка", secs["(шапка)"])

    def test_find_section_by_keywords(self):
        secs = {"Матрица трассировки и критерии приёмки": "x"}
        self.assertEqual(dt.find_section(secs, "Матрица"), "x")
        self.assertIsNone(dt.find_section(secs, "НетТакой"))

    def test_protocol_round_from_filename(self):
        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp) / "06-acceptance-protocol.r2.md"
            p.write_text("# П\n## Решение\n\n- [x] **Принято** — ок\n", encoding="utf-8")
            proto = dt.parse_protocol(p)
        self.assertEqual(proto.round, 2)
        self.assertEqual(proto.decision, "Принято")

    def test_protocol_review_date_parsing(self):
        """Дата пересмотра «Отложено»: форматы ГГГГ-ММ-ДД и ДД.ММ.ГГГГ; отсутствие → None."""
        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp) / "06-acceptance-protocol.md"
            p.write_text(
                "# П\n## Решение\n\n- [x] **Отложено** — нет среды\n\n"
                "**Дата пересмотра (обязательна для «Отложено»):** 2026-09-01\n",
                encoding="utf-8",
            )
            self.assertEqual(dt.parse_protocol(p).review_date, "2026-09-01")
            p.write_text(
                "# П\n## Решение\n\n- [x] **Отложено** — нет среды\n\n"
                "**Дата пересмотра (обязательна для «Отложено»):** 01.09.2026\n",
                encoding="utf-8",
            )
            self.assertEqual(dt.parse_protocol(p).review_date, "01.09.2026")
            p.write_text(
                "# П\n## Решение\n\n- [x] **Отложено** — нет среды\n\n"
                "**Дата пересмотра (обязательна для «Отложено»):** <ГГГГ-ММ-ДД>\n",
                encoding="utf-8",
            )
            self.assertIsNone(dt.parse_protocol(p).review_date)

    def test_parse_date_formats(self):
        self.assertEqual(dt._parse_date("2026-08-16"), dt.datetime(2026, 8, 16).date())
        self.assertEqual(dt._parse_date("16.08.2026"), dt.datetime(2026, 8, 16).date())
        self.assertIsNone(dt._parse_date("не дата"))

    def test_comparative_without_baseline(self):
        criteria = [
            ("1", "Список открывается не дольше, чем до доработки", "замер времени открытия до/после"),
            ("2", "Работает не хуже текущей версии", "проверить глазами"),
            ("3", "1000 заявок выгружаются не дольше 10 секунд", "замер на тестовой базе"),
        ]
        self.assertEqual(dt.comparative_without_baseline(criteria), ["2"])

    def test_brief_retrospective_detection(self):
        """Ретроспектива 01 заполнена = секция есть и без заглушек «<…>»."""
        with tempfile.TemporaryDirectory() as tmp:
            base = "# Карточка задачи — T: X\n\n## Критерии успеха\n\n| № | Критерий | Как проверим |\n|---|---|---|\n| 1 | к | п |\n"
            filled = Path(tmp) / "filled.md"
            filled.write_text(base + "\n## Ретроспектива\n\n| Оценка / факт | S / S — попадание |\n|---|---|\n", encoding="utf-8")
            self.assertTrue(dt.parse_brief(filled).retrospective_filled)
            stub = Path(tmp) / "stub.md"
            stub.write_text(base + "\n## Ретроспектива\n\n| Оценка / факт | <S> / <S / M / L> — <причина> |\n|---|---|\n", encoding="utf-8")
            self.assertFalse(dt.parse_brief(stub).retrospective_filled)
            absent = Path(tmp) / "absent.md"
            absent.write_text(base, encoding="utf-8")
            self.assertFalse(dt.parse_brief(absent).retrospective_filled)

    def test_repeated_failures_across_rounds(self):
        state = dt.load_state(FIXTURES / "rework" / "TASK-R1")
        self.assertEqual(dt.repeated_failures(state.protocols), {"1": [0, 1]})

    def test_status_counts_dual_static_and_deferred(self):
        m = dt.Matrix(rows=[dt.MatrixRow("1", "к", "1", "о", "п", "✅ статич. (05) / ⏳ интерактивно")])
        c = m.status_counts()
        self.assertEqual(c["ok"], ["1"])          # прошёл (статически)
        self.assertEqual(c["ok_static"], ["1"])   # подмножество ok
        self.assertEqual(c["deferred"], ["1"])
        self.assertEqual(c["red"], [])

    def test_status_counts_short_static_marker(self):
        m = dt.Matrix(rows=[dt.MatrixRow("1", "к", "1", "о", "п", "✅с")])
        c = m.status_counts()
        self.assertEqual(c["ok"], ["1"])
        self.assertEqual(c["ok_static"], ["1"])

    def test_status_counts_red_overrides_pass(self):
        """❌ в 06 сильнее ✅с в 05: критерий красный, не «прошедший»."""
        m = dt.Matrix(rows=[dt.MatrixRow("1", "к", "1", "о", "п", "✅с ❌")])
        c = m.status_counts()
        self.assertEqual(c["red"], ["1"])
        self.assertEqual(c["ok"], [])

    def test_matrix_legend_table_ignored(self):
        """0.17.1: легенда «Алфавит статусов» внутри секции «Матрица…» (формат
        шаблона 0.13.0+) не парсится как критерии — раньше давала ложные ERR
        «пустая ячейка» на строках Пометка/☐/✅/... (TASK-002, отклонение №1)."""
        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp) / "04-acceptance-criteria.md"
            p.write_text(
                "# К\n\n## Матрица трассировки и критерии приёмки\n\n"
                "| № | Критерий (проверяемо) | Шаг сценария | Объект/код (03) | Проверка | Статус 05 | Статус 06 |\n"
                "|---|---|---|---|---|---|---|\n"
                "| 1 | критерий | 1 | форма | ручная | ☐ | ☐ |\n\n"
                "### Алфавит статусов (обеих колонок)\n\n"
                "| Пометка | Значение | Кто ставит |\n"
                "|---|---|---|\n"
                "| ☐ | не проверялся | — |\n"
                "| ✅ | проверен живым прогоном | 05, 06 |\n\n"
                "Правила заполнения: текст вне таблиц.\n",
                encoding="utf-8",
            )
            m = dt.parse_matrix(p)
        self.assertEqual([r.num for r in m.rows], ["1"])
        self.assertEqual([r.criterion for r in m.rows], ["критерий"])
        self.assertEqual(m.rows[0].empty_trace_cols(), [])

    def test_matrix_two_status_columns(self):
        """Формат 0.13.0: «Статус 05»/«Статус 06» собираются в один статус строки."""
        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp) / "04-acceptance-criteria.md"
            p.write_text(
                "# К\n\n## Матрица трассировки и критерии приёмки\n\n"
                "| № | Критерий (проверяемо) | Шаг сценария | Объект/код (03) | Проверка | Статус 05 | Статус 06 |\n"
                "|---|---|---|---|---|---|---|\n"
                "| 1 | колонка | 1 | форма | ручная | ✅с | ⏳ |\n"
                "| 2 | итог | 2 | форма | ручная | ✅с | ✅ |\n"
                "| 3 | пусто | 1а.1 | форма | ручная | ✅с | ❌ |\n",
                encoding="utf-8",
            )
            m = dt.parse_matrix(p)
        c = m.status_counts()
        self.assertEqual(c["ok"], ["1", "2"])              # ❌ в 06 исключает строку 3
        self.assertEqual(c["ok_static"], ["1", "2"])       # 05 у обеих ✅с
        self.assertEqual(c["deferred"], ["1"])             # ⏳ в 06
        self.assertEqual(c["red"], ["3"])


class TestStatusCommand(unittest.TestCase):
    def test_deferred_full_cycle(self):
        code, out = run_cli("status", FIXTURES / "deferred" / "TASK-D1")
        self.assertEqual(code, 0)
        self.assertIn("TASK-D1", out)
        self.assertIn('5 «Внешняя приёмка»', out)
        self.assertIn("Отложено", out)
        self.assertIn("статич. 3", out)          # все три критерия — статич. + ⏳
        self.assertIn("1.0.0-draft", out)
        self.assertIn("ЧЕРНОВИК", out)
        self.assertIn("06-acceptance-protocol.r1.md", out)  # следующий шаг — возобновление
        self.assertIn("Возобновление (из", out)             # текст возобновления из 06
        self.assertIn("Пересмотр «Отложено»: 2026-02-01", out)  # дата пересмотра из 06
        self.assertIn("срок истёк", out)                     # просрочена — пометка для РП

    def test_accepted_reaches_release_stage(self):
        code, out = run_cli("status", FIXTURES / "accepted" / "TASK-A1")
        self.assertEqual(code, 0)
        self.assertIn('6 «Релиз»', out)
        self.assertIn("Принято", out)
        self.assertIn("1.0.0", out)

    def test_early_task_points_to_design(self):
        code, out = run_cli("status", FIXTURES / "early" / "TASK-E1")
        self.assertEqual(code, 0)
        self.assertIn('1 «Планирование»', out)
        self.assertIn("02-execution-scenario.md", out)      # следующий шаг — проектирование

    def test_status_shows_epic_link(self):
        """Поле «Эпик» из 01 выводится в status и парсится в Brief."""
        code, out = run_cli("status", FIXTURES / "roadmap" / "TASK-R1")
        self.assertEqual(code, 0)
        self.assertIn("Эпик: EP-001", out)
        brief = dt.parse_brief(FIXTURES / "roadmap" / "TASK-R1" / "01-task-brief.md")
        self.assertEqual(brief.epic, "EP-001")

    def test_not_a_task_dir(self):
        with tempfile.TemporaryDirectory() as tmp:
            code, out = run_cli("status", Path(tmp))
        self.assertEqual(code, 1)
        self.assertIn("не найден 01-task-brief.md", out)

    def test_rework_repeated_failure_demands_automation(self):
        code, out = run_cli("status", FIXTURES / "rework" / "TASK-R1")
        self.assertEqual(code, 0)
        self.assertIn("Дважды ❌ у приёмщика: критерий 1 (раунды 0, 1)", out)
        self.assertIn("автоматизация unit/BDD обязательна", out)
        code, out = run_cli("check", FIXTURES / "rework" / "TASK-R1")
        self.assertEqual(code, 1)   # «Возврат» — задача в цикле правки
        self.assertIn("критерий 1 упал ❌ в раундах (0, 1)", out)
        self.assertIn("unit/BDD-тестом, а не ручной проверкой", out)


class TestRoadmapCommand(unittest.TestCase):
    def test_roadmap_lists_epic_and_task_stages(self):
        """Сводка каталога доставки: эпик, задачи с этапами конвейера, связь через поле «Эпик»."""
        code, out = run_cli("roadmap", FIXTURES / "roadmap")
        self.assertEqual(code, 0)
        self.assertIn("Задач: 2, эпиков: 1", out)
        self.assertIn("Эпик EP-001 — Внедрить согласование заявок на ремонт", out)
        self.assertIn("| TASK-R1 |", out)
        self.assertIn("| 1 Планирование |", out)
        self.assertIn("| TASK-R2 |", out)
        self.assertIn("| 2 Проектирование |", out)
        self.assertIn("| EP-001 |", out)                     # колонка «Эпик» у обеих задач
        self.assertNotIn("карточка эпика EP-001 не найдена", out)

    def test_roadmap_skips_releases_and_requires_tasks(self):
        """_releases не считается задачей; пустой каталог — код 1."""
        code, out = run_cli("roadmap", FIXTURES / "deferred")   # только TASK-D1 + _releases
        self.assertEqual(code, 0)
        self.assertIn("TASK-D1", out)
        self.assertEqual(out.count("| TASK-"), 1)
        with tempfile.TemporaryDirectory() as tmp:
            code, out = run_cli("roadmap", Path(tmp))
        self.assertEqual(code, 1)
        self.assertIn("нет ни задач", out)

    def test_roadmap_orphan_epic_link(self):
        """Задача ссылается на эпик без карточки в каталоге — сигнал рассинхрона."""
        with tempfile.TemporaryDirectory() as tmp:
            task = Path(tmp) / "TASK-X1"
            task.mkdir()
            (task / "01-task-brief.md").write_text(
                "# Карточка задачи — TASK-X1: Сделать что-то\n\n- **Эпик:** EP-999\n",
                encoding="utf-8",
            )
            code, out = run_cli("roadmap", Path(tmp))
        self.assertEqual(code, 0)
        self.assertIn("EP-999", out)
        self.assertIn("карточка эпика EP-999 не найдена", out)


class TestCheckCommand(unittest.TestCase):
    def test_deferred_is_legal_pause_not_error(self):
        code, out = run_cli("check", FIXTURES / "deferred" / "TASK-D1")
        self.assertEqual(code, 0)
        self.assertIn("легальная пауза", out)
        self.assertIn("легален при «Отложено»", out)
        self.assertIn("срок пересмотра «Отложено» истёк (2026-02-01)", out)  # просрочка — WARN, не ERR
        self.assertIn("ERR 0", out)

    def test_accepted_all_gates_pass(self):
        code, out = run_cli("check", FIXTURES / "accepted" / "TASK-A1")
        self.assertEqual(code, 0)
        self.assertIn("задача в составе", out)
        self.assertIn("ERR 0", out)
        self.assertNotIn("ретроспектива 01 не заполнена", out)   # ретроспектива закрыта

    def test_broken_task_reports_every_gate(self):
        code, out = run_cli("check", FIXTURES / "broken" / "TASK-B1")
        self.assertEqual(code, 1)
        # гейт 1→2: подтверждение не отмечено
        self.assertIn("инициатор не подтвердил", out)
        self.assertIn("критерии успеха без «Как проверим»", out)
        # гейт 2→3: пустая ячейка + строк меньше, чем критериев
        self.assertIn("пустая ячейка «Объект/код»", out)
        self.assertIn("меньше, чем критериев успеха", out)
        # гейт 3→4: входные проверки с ❌
        self.assertIn("Входные проверки разработки» содержит ❌", out)
        # гейт 4→5: красная строка и вердикт
        self.assertIn("красная строка 05", out)
        self.assertIn("вердикт 05 не завершён", out)
        # гейт 5→6: возврат без листа 06a
        self.assertIn("решение «Возврат» — гейт не пройден", out)
        self.assertIn("06a-rework-list.md не найден", out)

    def test_early_task_only_gate_1(self):
        code, out = run_cli("check", FIXTURES / "early" / "TASK-E1")
        self.assertEqual(code, 0)
        self.assertIn("подтверждение инициатора", out)
        self.assertNotIn("Гейт 2→3", out)


if __name__ == "__main__":
    unittest.main()
