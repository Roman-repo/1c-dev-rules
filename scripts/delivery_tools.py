#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
delivery_tools.py — состояние задачи конвейера разработки и механическая
проверка DoD-гейтов по артефактам 01–06 в каталоге задачи.

Зачем: состояние задачи рассыпано по 01–06 + каталогу релиза. Скрипт выводит
его из самих артефактов (без дублирующего файла состояния, который может
протухнуть) и проверяет те условия DoD-гейтов, которые сводятся к структуре
документа: наличие артефактов, пустые ячейки матрицы 04, красные строки 05,
решение протокола 06, «принято» в составе релиза. Код и ревью-прогоны
(гейт 3→4) скрипту недоступны — они фиксируются в шапке 05.

Использование (аргумент — каталог задачи, обычно docs/delivery/<ID-задачи>):
    python3 scripts/delivery_tools.py status <каталог-задачи>   # сводка состояния
    python3 scripts/delivery_tools.py check  <каталог-задачи>   # прогон DoD-гейтов
    python3 scripts/delivery_tools.py roadmap <каталог-доставки>  # все задачи и эпики

Выход: check — 0 нет ERR (WARN допустимы), 1 есть ERR. status — всегда 0
(1 только если каталог не похож на задачу конвейера). roadmap — 0 если найден
хотя бы один эпик или задача (1 — пустой/чужой каталог, 2 — каталог не найден).

Без зависимостей: только стандартная библиотека.
"""
from __future__ import annotations

import re
import sys
from dataclasses import dataclass, field
from datetime import date, datetime
from pathlib import Path
from typing import Dict, List, Optional, Tuple

# --- артефакты и этапы ------------------------------------------------------

ARTIFACTS = {
    1: "01-task-brief.md",
    2: "02-execution-scenario.md",
    3: "03-change-spec.md",
    4: "04-acceptance-criteria.md",
    5: "05-internal-acceptance.md",
}
PROTOCOL_GLOB = "06-acceptance-protocol*.md"
REWORK_LIST = "06a-rework-list.md"
EPIC_CARD = "00-epic-brief.md"
DESIGN_REVIEW = "04a-design-review.md"       # лист замечаний согласования (этап 3)
MODE_FILE = "_conveyor-mode.md"              # режим согласования каталога доставки (manual/auto)

STAGES = [
    "Планирование", "Проектирование", "Согласование", "Разработка",
    "Внутренняя приёмка", "Внешняя приёмка", "Релиз",
]

DECISIONS = ("Принято с замечаниями", "Принято", "Возврат", "Отложено")
APPROVAL_DECISIONS = ("Согласовано", "Доработать")  # штамп Оркестратора в 04 (гейт 2→3)

# --- разбор markdown --------------------------------------------------------

SEP_CELL_RE = re.compile(r"^:?-{3,}:?$")

# Сравнительные критерии и признаки зафиксированного базлайна (эвристики WARN,
# не истина в последней инстанции): сравнительный критерий ссылается на
# состояние «до» (не абсолютный порог) — без замера «до» он неверифицируем,
# состояние «до» после разработки не повторить.
COMPARATIVE_RE = re.compile(
    r"чем до (доработки|изменений|правки)|как до"
    r"|не (дольше|медленнее|хуже)[^.,;\n]*(текущ|прежн|стар|сейчас|до )"
    r"|сопоставимо с (текущ|прежн|стар)", re.I)
BASELINE_RE = re.compile(
    r"базлайн|замер до|до/после|до правки|\d{2}\.\d{2}\.\d{4}|\d{4}-\d{2}-\d{2}", re.I)


def comparative_without_baseline(criteria: List[Tuple[str, str, str]]) -> List[str]:
    """Номера сравнительных критериев без признака базлайна в «Как проверим»."""
    return [num for num, text, check in criteria
            if COMPARATIVE_RE.search(text) and not BASELINE_RE.search(check)]


def _parse_date(s: str) -> Optional[date]:
    for fmt in ("%Y-%m-%d", "%d.%m.%Y"):
        try:
            return datetime.strptime(s, fmt).date()
        except ValueError:
            continue
    return None


def md_sections(text: str) -> Dict[str, str]:
    """Текст → словарь «заголовок ## → содержимое» (шапка — ключ '(шапка)')."""
    secs: Dict[str, List[str]] = {}
    cur = "(шапка)"
    for line in text.splitlines():
        if line.startswith("## "):
            cur = line[3:].strip()
            secs.setdefault(cur, [])
        else:
            secs.setdefault(cur, []).append(line)
    return {k: "\n".join(v) for k, v in secs.items()}


def find_section(sections: Dict[str, str], *keywords: str) -> Optional[str]:
    """Секция, заголовок которой содержит все ключевые слова."""
    for header, body in sections.items():
        if all(kw.lower() in header.lower() for kw in keywords):
            return body
    return None


def split_tables(section_text: str) -> List[Tuple[List[str], List[List[str]]]]:
    """Все таблицы секции → список (заголовки, строки-ячейки).

    Блоки pipe-строк разделяются любой непустой непipe-строкой (заголовок
    «### Алфавит статусов», текст между таблицами). Строки-разделители
    пропускаются. Нужно, чтобы таблицы-легенды внутри секции «Матрица…»
    не попадали в парсинг критериев (ложные пустые ячейки, 0.17.1)."""
    tables: List[Tuple[List[str], List[List[str]]]] = []
    block: List[List[str]] = []
    for line in section_text.splitlines():
        s = line.strip()
        if s.startswith("|"):
            cells = [c.strip() for c in s.strip("|").split("|")]
            if cells and all(SEP_CELL_RE.match(c) for c in cells if c != ""):
                continue
            block.append(cells)
        elif block:
            tables.append((block[0], block[1:]))
            block = []
    if block:
        tables.append((block[0], block[1:]))
    return tables


def table_rows(section_text: str) -> Tuple[List[str], List[List[str]]]:
    """Первая таблица секции → (заголовки, строки-ячейки). Строки-разделители пропущены."""
    tables = split_tables(section_text)
    return tables[0] if tables else ([], [])


def col_index(headers: List[str], *keywords: str) -> Optional[int]:
    """Индекс первой колонки, заголовок которой содержит все ключевые слова."""
    for i, h in enumerate(headers):
        if all(kw.lower() in h.lower() for kw in keywords):
            return i
    return None


def cell(row: List[str], idx: Optional[int]) -> str:
    if idx is None or idx >= len(row):
        return ""
    return row[idx]


def checkboxes(section_text: Optional[str]) -> Tuple[int, int]:
    """(отмечено, не отмечено) по строкам '- [x]' / '- [ ]'."""
    if not section_text:
        return 0, 0
    checked = len(re.findall(r"^\s*-\s*\[[xX]\]", section_text, re.M))
    unchecked = len(re.findall(r"^\s*-\s*\[\s\]", section_text, re.M))
    return checked, unchecked


def read_text(path: Path) -> str:
    return path.read_text(encoding="utf-8")


# --- парсеры артефактов -----------------------------------------------------

@dataclass
class Brief:
    task_id: str = ""
    name: str = ""
    epic: str = ""                                              # ID эпика из поля «Эпик» (если задача в эпике)
    criteria: List[Tuple[str, str, str]] = field(default_factory=list)  # (№, критерий, как проверим)
    confirm_checked: int = 0
    confirm_unchecked: int = 0
    confirm_section_found: bool = False
    retrospective_filled: bool = False  # секция есть и без заглушек «<…>» — петля оценки закрыта


def parse_brief(path: Path) -> Brief:
    brief = Brief()
    text = read_text(path)
    sections = md_sections(text)
    m = re.match(r"#\s*Карточка задачи\s*—\s*(.+?):\s*(.+)", text.splitlines()[0])
    if m:
        brief.task_id, brief.name = m.group(1).strip(), m.group(2).strip()
    m = re.search(r"\*\*Эпик:\*\*\s*([A-Za-z0-9_\-]+)", text)
    if m and not m.group(1).startswith("<"):
        brief.epic = m.group(1)
    crit_sec = find_section(sections, "Критерии успеха")
    if crit_sec:
        headers, rows = table_rows(crit_sec)
        i_num, i_text, i_check = col_index(headers, "№"), col_index(headers, "Критерий"), col_index(headers, "проверим")
        for r in rows:
            text = cell(r, i_text)
            if text:  # пустые строки — заглушки шаблона
                brief.criteria.append((cell(r, i_num), text, cell(r, i_check)))
    conf = find_section(sections, "Подтверждение инициатора")
    brief.confirm_section_found = conf is not None
    brief.confirm_checked, brief.confirm_unchecked = checkboxes(conf)
    retro = find_section(sections, "Ретроспектив")
    brief.retrospective_filled = bool(retro) and "<" not in retro
    return brief


@dataclass
class MatrixRow:
    num: str
    criterion: str
    step: str
    obj: str
    check: str
    status: str

    def empty_trace_cols(self) -> List[str]:
        """Пустые (не «Статус») ячейки: '' → пустая, '—' отдельно не различаем тут."""
        out = []
        for title, val in (("Шаг сценария", self.step), ("Объект/код", self.obj), ("Проверка", self.check)):
            if not val:
                out.append(title)
        return out


@dataclass
class DesignReview:
    """Решение Оркестратора по пакету 02/03/04 (этап 3 «Согласование»).

    Источник — артефакт 04a «Лист замечаний» (0.19.0+); штамп в секции
    «Согласование Оркестратора» артефакта 04 — legacy-формат 0.18.0,
    читается как фолбэк, когда 04a нет."""

    source: str = ""                  # "04a" | "04 (0.18.0)"
    checked: List[str] = field(default_factory=list)
    decision: Optional[str] = None    # «Согласовано» / «Доработать»
    mode: str = ""                    # manual/auto из штампа (пусто — не указан)
    date: str = ""

    @classmethod
    def from_section(cls, section_text: str, source: str) -> "DesignReview":
        review = cls(source=source)
        for decision in APPROVAL_DECISIONS:
            if re.search(rf"^\s*-\s*\[[xX]\]\s*\*\*{re.escape(decision)}\*\*", section_text, re.M):
                review.checked.append(decision)
        if len(review.checked) == 1:
            review.decision = review.checked[0]
        m = re.search(r"Режим[^:\n]*:\s*\*{0,2}\s*(manual|auto)", section_text, re.I)
        if m:
            review.mode = m.group(1).lower()
        m = re.search(r"Дата[^:\n]*:\s*\*{0,2}\s*(\d{4}-\d{2}-\d{2}|\d{2}\.\d{2}\.\d{4})", section_text)
        if m:
            review.date = m.group(1)
        return review


def parse_design_review(path: Path) -> DesignReview:
    """04a-design-review.md → решение Оркестратора (секция «Решение»)."""
    sections = md_sections(read_text(path))
    sec = find_section(sections, "Решение")
    if sec is None:
        return DesignReview(source="04a")
    return DesignReview.from_section(sec, "04a")


@dataclass
class Matrix:
    rows: List[MatrixRow] = field(default_factory=list)
    parse_error: Optional[str] = None
    approval: Optional[DesignReview] = None   # legacy: штамп в 04 (формат 0.18.0)

    def status_counts(self) -> Dict[str, List[str]]:
        """Критерии по типу статуса: ok — прошедшие (✅ в 05 или 06; ❌ сильнее ✅),
        ok_static — подмножество ok со статическим evidence (✅с/«статич.»),
        deferred (⏳), red (❌), todo (☐/пусто)."""
        out: Dict[str, List[str]] = {"ok": [], "ok_static": [], "deferred": [], "red": [], "todo": []}
        for r in self.rows:
            if "❌" in r.status:
                out["red"].append(r.num)
            elif "✅" in r.status:
                out["ok"].append(r.num)
                # ✅с — компактный маркер 0.13.0, «статич.» — записи формата до 0.13.0
                if "✅с" in r.status or "статич" in r.status.lower():
                    out["ok_static"].append(r.num)
            if "⏳" in r.status:
                out["deferred"].append(r.num)
            if "❌" not in r.status and "✅" not in r.status and "⏳" not in r.status:
                out["todo"].append(r.num)
        return out


def parse_matrix(path: Path) -> Matrix:
    matrix = Matrix()
    sections = md_sections(read_text(path))
    # Legacy-штамп согласования (0.18.0 — секция в 04) разбираем до таблиц:
    # он валиден независимо от ошибок разбора самой матрицы. Основной источник
    # согласования с 0.19.0 — артефакт 04a.
    appr = find_section(sections, "Согласование", "Оркестратора")
    if appr is not None:
        matrix.approval = DesignReview.from_section(appr, "04 (0.18.0)")
    sec = find_section(sections, "Матрица")
    if sec is None:
        matrix.parse_error = "секция «Матрица трассировки и критерии приёмки» не найдена"
        return matrix
    # только таблицы с колонкой «Критерий»: легенда «Алфавит статусов» внутри
    # секции (### в шаблоне 0.13.0+) матрицей не является (0.17.1)
    matrix_tables = [
        (h, r) for h, r in split_tables(sec) if col_index(h, "Критерий") is not None
    ]
    if not matrix_tables:
        matrix.parse_error = "таблица критериев (колонка «Критерий») в секции не найдена"
        return matrix
    headers, rows = matrix_tables[0]
    idx = {
        "num": col_index(headers, "№"), "criterion": col_index(headers, "Критерий"),
        "step": col_index(headers, "Шаг"), "obj": col_index(headers, "Объект"),
        "check": col_index(headers, "Проверка"),
    }
    # статусы: одна колонка «Статус» (формат до 0.13.0) или две — «Статус 05»/«Статус 06»
    status_cols = [i for i, h in enumerate(headers) if "статус" in h.lower()]
    for r in rows:
        criterion = cell(r, idx["criterion"])
        if not criterion:  # заглушка шаблона
            continue
        matrix.rows.append(MatrixRow(
            num=cell(r, idx["num"]), criterion=criterion,
            step=cell(r, idx["step"]), obj=cell(r, idx["obj"]),
            check=cell(r, idx["check"]),
            status=" ".join(cell(r, i) for i in status_cols).strip(),
        ))
    return matrix


@dataclass
class InternalReport:
    red_rows: List[str] = field(default_factory=list)   # «Счастливый путь, шаг 3»
    input_checks_failed: bool = False                    # ❌ в «Входные проверки разработки»
    verdict_checked: int = 0
    verdict_unchecked: int = 0
    static_mode: bool = False


def parse_internal(path: Path) -> InternalReport:
    rep = InternalReport()
    sections = md_sections(read_text(path))
    for sec_name in ("Счастливый путь", "Отклонения", "Тесты"):
        sec = find_section(sections, sec_name)
        if not sec:
            continue
        headers, rows = table_rows(sec)
        i_status = col_index(headers, "Статус") if col_index(headers, "Статус") is not None else col_index(headers, "Результат")
        i_first = col_index(headers, "Шаг") if col_index(headers, "Шаг") is not None else 0
        for r in rows:
            if "❌" in cell(r, i_status):
                rep.red_rows.append(f"{sec_name}, строка «{cell(r, i_first)}»")
    header = sections.get("(шапка)", "")
    m = re.search(r"Входные проверки разработки:\s*(.+)", header)
    if m and "❌" in m.group(1):
        rep.input_checks_failed = True
    m = re.search(r"Среда:\s*(.+)", header)
    if m and "статическ" in m.group(1).lower():
        rep.static_mode = True
    verdict = find_section(sections, "Вердикт")
    rep.verdict_checked, rep.verdict_unchecked = checkboxes(verdict)
    return rep


@dataclass
class Protocol:
    path: Path
    round: int                    # 0 = базовый 06-acceptance-protocol.md
    date: str = ""
    decision: Optional[str] = None
    decisions_checked: List[str] = field(default_factory=list)
    resume_text: str = ""         # абзац «Возобновление приёмки» (при «Отложено»)
    review_date: Optional[str] = None  # дата пересмотра «Отложено» (1c-external-acceptance)
    remark_classes: List[str] = field(default_factory=list)
    failed_criteria: List[str] = field(default_factory=list)  # № критериев со статусом приёмщика ❌


def parse_protocol(path: Path) -> Protocol:
    proto = Protocol(path=path, round=0)
    m = re.search(r"\.r(\d+)$", path.stem)
    if m:
        proto.round = int(m.group(1))
    text = read_text(path)
    sections = md_sections(text)
    header = sections.get("(шапка)", "")
    m = re.search(r"Дата приёмки:\s*\*{0,2}\s*([^(*\n]+)", header)
    if m:
        proto.date = m.group(1).strip()
    decision_sec = find_section(sections, "Решение")
    if decision_sec:
        for decision in DECISIONS:  # порядок: длинные имена первыми («Принято с замечаниями»)
            if re.search(rf"^\s*-\s*\[[xX]\]\s*\*\*{re.escape(decision)}\*\*", decision_sec, re.M):
                proto.decisions_checked.append(decision)
        if len(proto.decisions_checked) == 1:
            proto.decision = proto.decisions_checked[0]
    m = re.search(r"\*\*Возобновление приёмки:\*\*\s*(.+?)(?:\n\n|\Z)", text, re.S)
    if m:
        proto.resume_text = re.sub(r"\s+", " ", m.group(1)).strip()
    m = re.search(r"дата пересмотра[^:\n]*:\s*[^0-9<]*(\d{4}-\d{2}-\d{2}|\d{2}\.\d{2}\.\d{4})", text, re.I)
    if m:
        proto.review_date = m.group(1)
    remarks = find_section(sections, "Замечания")
    if remarks:
        headers, rows = table_rows(remarks)
        i_class = col_index(headers, "Класс")
        for r in rows:
            c = cell(r, i_class).upper()
            if c in ("A", "B", "C", "D"):
                proto.remark_classes.append(c)
    results = find_section(sections, "Результаты по критериям")
    if results:
        headers, rows = table_rows(results)
        i_num = col_index(headers, "№")
        i_status = col_index(headers, "Статус приёмщика")
        for r in rows:
            if "❌" in cell(r, i_status):
                proto.failed_criteria.append(cell(r, i_num))
    return proto


def find_protocols(task_dir: Path) -> List[Protocol]:
    """Все раунды протокола, упорядочены по номеру (последний — актуальный)."""
    protos = [parse_protocol(p) for p in sorted(task_dir.glob(PROTOCOL_GLOB))]
    return sorted(protos, key=lambda p: p.round)


def repeated_failures(protocols: List[Protocol]) -> Dict[str, List[int]]:
    """Критерии, упавшие ❌ у приёмщика, с номерами раундов: {№: [раунды]}."""
    out: Dict[str, List[int]] = {}
    for p in protocols:
        for num in p.failed_criteria:
            out.setdefault(num, []).append(p.round)
    return out


@dataclass
class ReleaseRef:
    dir_name: str
    is_draft: bool
    task_included: bool                     # строка в «Задачи в составе»
    task_rejected: bool                     # строка в «Отбор отклонён»
    protocol_ref: Optional[str] = None      # имя файла протокола из строки задачи
    parse_note: Optional[str] = None


def find_releases(task_dir: Path, task_id: str) -> List[ReleaseRef]:
    """Каталоги _releases/*/, чей манифест упоминает задачу."""
    out: List[ReleaseRef] = []
    releases_root = task_dir.parent / "_releases"
    if not releases_root.is_dir():
        return out
    for rdir in sorted(releases_root.iterdir()):
        manifest = rdir / "release-manifest.md"
        if not manifest.is_file():
            continue
        text = read_text(manifest)
        if task_id not in text:
            continue
        is_draft = "-draft" in rdir.name or "ЧЕРНОВИК" in text.upper()
        sections = md_sections(text)
        included_sec = find_section(sections, "Задачи в составе")
        task_included, protocol_ref = False, None
        if included_sec:
            headers, rows = table_rows(included_sec)
            i_task = col_index(headers, "Задача")
            i_proto = col_index(headers, "Протокол")
            for r in rows:
                if task_id in cell(r, i_task):
                    task_included = True
                    m = re.search(r"06-acceptance-protocol(?:\.r\d+)?\.md", cell(r, i_proto))
                    if m:
                        protocol_ref = m.group(0)
                    break
        rejected_sec = find_section(sections, "Отбор отклонён") or ""
        out.append(ReleaseRef(
            dir_name=rdir.name, is_draft=is_draft, task_included=task_included,
            task_rejected=task_id in rejected_sec, protocol_ref=protocol_ref,
        ))
    return out


# --- состояние задачи (status) ----------------------------------------------

@dataclass
class TaskState:
    task_dir: Path
    brief: Optional[Brief] = None
    matrix: Optional[Matrix] = None
    design_review: Optional[DesignReview] = None
    internal: Optional[InternalReport] = None
    protocols: List[Protocol] = field(default_factory=list)
    rework_list: Optional[Path] = None
    releases: List[ReleaseRef] = field(default_factory=list)

    @property
    def latest_protocol(self) -> Optional[Protocol]:
        return self.protocols[-1] if self.protocols else None

    @property
    def approval(self) -> Optional[DesignReview]:
        """Согласование пакета 02/03/04: артефакт 04a (0.19.0+) либо
        legacy-штамп в секции 04 (0.18.0), если 04a нет."""
        if self.design_review is not None:
            return self.design_review
        return self.matrix.approval if self.matrix else None


def load_state(task_dir: Path) -> TaskState:
    state = TaskState(task_dir=task_dir)
    if (task_dir / ARTIFACTS[1]).is_file():
        state.brief = parse_brief(task_dir / ARTIFACTS[1])
    if (task_dir / ARTIFACTS[4]).is_file():
        state.matrix = parse_matrix(task_dir / ARTIFACTS[4])
    if (task_dir / DESIGN_REVIEW).is_file():
        state.design_review = parse_design_review(task_dir / DESIGN_REVIEW)
    if (task_dir / ARTIFACTS[5]).is_file():
        state.internal = parse_internal(task_dir / ARTIFACTS[5])
    state.protocols = find_protocols(task_dir)
    if (task_dir / REWORK_LIST).is_file():
        state.rework_list = task_dir / REWORK_LIST
    task_id = state.brief.task_id if state.brief else task_dir.name
    state.releases = find_releases(task_dir, task_id)
    return state


def fmt_nums(nums: List[str]) -> str:
    joined = ", ".join(n for n in nums if n)
    return joined if joined else "—"


def read_conveyor_mode(task_dir: Path) -> str:
    """Режим согласования каталога доставки: _conveyor-mode.md рядом с задачей.
    Нет файла или строки mode: — manual (безопасный дефолт, 1c-delivery-gate §0)."""
    mode_file = task_dir.parent / MODE_FILE
    if mode_file.is_file():
        m = re.search(r"^\s*mode:\s*(manual|auto)\s*$", read_text(mode_file), re.M | re.I)
        if m:
            return m.group(1).lower()
    return "manual"


def next_step(state: TaskState) -> List[str]:
    """Рекомендованные следующие шаги по лестнице состояний (эвристика, не решение)."""
    steps: List[str] = []
    if not state.brief:
        return ["создать 01-task-brief.md по шаблону 1c-planning"]
    if state.brief.confirm_section_found and state.brief.confirm_unchecked:
        steps.append("DoD 1→2: подтверждение инициатора не завершено (неотмеченные пункты в 01)")
    missing_design = [ARTIFACTS[n] for n in (2, 3, 4) if not (state.task_dir / ARTIFACTS[n]).is_file()]
    if missing_design:
        steps.append(f"Проектирование (1c-solution-design): отсутствуют {', '.join(missing_design)}")
    if state.matrix:
        empties = [r.num for r in state.matrix.rows if r.empty_trace_cols()]
        if empties:
            steps.append(f"матрица 04: пустые ячейки трассировки у критериев {fmt_nums(empties)} (DoD 2→3)")
    if not (state.task_dir / ARTIFACTS[5]).is_file() and not missing_design:
        approval = state.approval
        approved = approval is not None and approval.decision == "Согласовано"
        if not approved:
            mode = read_conveyor_mode(state.task_dir)
            steps.append(f"этап 3 Согласование (режим {mode}): предъявить пакет 02/03/04 Оркестратору → "
                         f"лист замечаний 04a; в manual код не начинается до «Согласовано»")
        else:
            steps.append("Разработка (1c-dispatch-gate) → внутренняя приёмка (артефакт 05)")
    internal = state.internal
    if internal:
        if internal.red_rows:
            steps.append(f"🔴 самовозврат в Разработку (класс A): красные строки 05 — {'; '.join(internal.red_rows)}")
        elif internal.verdict_unchecked:
            steps.append("вердикт 05 не завершён (неотмеченные пункты)")
    proto = state.latest_protocol
    if internal and not internal.red_rows and not proto:
        steps.append("внешняя приёмка (1c-external-acceptance): демонстрация по матрице 04 → протокол 06")
    if proto:
        d = proto.decision
        if d is None:
            steps.append("протокол 06: решение не отмечено (или отмечено несколько)")
        elif d == "Отложено":
            steps.append(f"приёмка отложена — возобновление новым протоколом 06-acceptance-protocol.r{proto.round + 1}.md "
                         f"(переход на Релиз запрещён, DoD 6→7)")
        elif d == "Возврат":
            base = "лист возврата 06a" + (" (не найден!)" if not state.rework_list else "")
            steps.append(f"возврат: {base}, классы {proto.remark_classes or '?'} → целевые этапы; после правки — повторная приёмка")
        elif d in ("Принято", "Принято с замечаниями"):
            if state.releases:
                drafts = [r for r in state.releases if r.is_draft]
                if drafts:
                    steps.append(f"принято — повысить черновик _releases/{drafts[0].dir_name} до боевого (протокол в «Отбор»)")
                else:
                    steps.append("принято — задача в релизе (этап 6 закрыт по артефактам)")
            else:
                steps.append("принято — этап 7 Релиз (1c-release): включить задачу в состав релиза")
            if state.brief and not state.brief.retrospective_filled and any(not r.is_draft for r in state.releases):
                steps.append("заполнить ретроспективу 01 (оценка/факт, возвраты, вывод) — закрытие задачи")
    return steps


def stage_reached(state: TaskState) -> int:
    """Номер достигнутого этапа (7-этапная модель, 0.19.0): 01→1; 02/03/04→2;
    04a→3 (Согласование); 05→5 (этап 4 «Разработка» файлового маркера не имеет —
    известный пробел, статусы ревью в шапке 05); протокол→6, «принято»→7.
    0 — артефактов нет."""
    reached = 0
    if (state.task_dir / ARTIFACTS[1]).is_file():
        reached = 1
    if any((state.task_dir / ARTIFACTS[n]).is_file() for n in (2, 3, 4)):
        reached = max(reached, 2)
    if (state.task_dir / DESIGN_REVIEW).is_file():
        reached = max(reached, 3)
    if (state.task_dir / ARTIFACTS[5]).is_file():
        reached = max(reached, 5)
    proto = state.latest_protocol
    if proto:
        reached = 6
        if proto.decision in ("Принято", "Принято с замечаниями"):
            reached = 7
    return reached


def cmd_status(task_dir: Path) -> int:
    state = load_state(task_dir)
    brief = state.brief
    if not brief:
        print(f"❌ {task_dir}: не найден 01-task-brief.md — это каталог задачи конвейера?")
        return 1
    print(f"Задача: {brief.task_id} — {brief.name}")
    if brief.epic:
        print(f"Эпик: {brief.epic}")
    print(f"Каталог: {task_dir}")

    arts = " ".join(
        f"{n:02d} ✓" if (task_dir / ARTIFACTS[n]).is_file() else f"{n:02d} —"
        for n in sorted(ARTIFACTS)
    )
    proto = state.latest_protocol
    proto_desc = "нет"
    if proto:
        proto_desc = f"{proto.path.name} (раунд {proto.round})"
    rework = "✓" if state.rework_list else "—"
    print(f"Артефакты: {arts} | 06: {proto_desc} | 06a: {rework}")

    # Этап: самый поздний существующий артефакт + решение протокола
    # (см. stage_reached).
    reached = stage_reached(state)
    proto = state.latest_protocol
    stage = STAGES[reached - 1] if reached else "—"
    line = f"Этап: {reached} «{stage}»" if reached else "Этап: — (артефактов нет)"
    if proto and proto.decision:
        line += f" — решение «{proto.decision}»" + (f", {proto.date}" if proto.date else "")
    print(line)

    if state.matrix and state.matrix.rows:
        c = state.matrix.status_counts()
        print(f"Матрица 04: {len(state.matrix.rows)} критериев — "
              f"✅ {len(c['ok'])} (статич. {len(c['ok_static'])}), "
              f"⏳ {len(c['deferred'])} ({fmt_nums(c['deferred'])}), "
              f"❌ {len(c['red'])} ({fmt_nums(c['red'])}), "
              f"☐ {len(c['todo'])} ({fmt_nums(c['todo'])})")
    approval = state.approval
    if state.matrix or approval:
        mode = read_conveyor_mode(task_dir)
        if approval is None:
            print(f"Согласование: нет листа замечаний 04a (этап 3; режим каталога: {mode})")
        elif approval.decision == "Согласовано":
            stamp = approval.mode or mode
            when = f", {approval.date}" if approval.date else ""
            print(f"Согласование: ✅ Согласовано{when} (режим {stamp}; {approval.source})")
        elif approval.decision == "Доработать":
            print(f"Согласование: ❌ Доработать — возврат на проектирование/требования, "
                  f"без кода до повторного согласования ({approval.source})")
        else:
            print(f"Согласование: решение не отмечено в {approval.source} (режим каталога: {mode})")
    if state.internal:
        red = "🔴 " + "; ".join(state.internal.red_rows) if state.internal.red_rows else "нет"
        static = ", статический режим" if state.internal.static_mode else ""
        print(f"Отчёт 05: красные строки — {red}{static}")
    repeated = {n: rs for n, rs in repeated_failures(state.protocols).items() if len(rs) >= 2}
    if repeated:
        details = "; ".join(f"критерий {n} (раунды {', '.join(map(str, rs))})" for n, rs in sorted(repeated.items()))
        print(f"Дважды ❌ у приёмщика: {details} — автоматизация unit/BDD обязательна")
    if state.releases:
        for r in state.releases:
            kind = "ЧЕРНОВИК" if r.is_draft else "боевой"
            where = "в составе" if r.task_included else ("в «Отбор отклонён»" if r.task_rejected else "упомянут")
            print(f"Релиз: _releases/{r.dir_name} — {kind}, задача {where}")
    elif proto and proto.decision in ("Принято", "Принято с замечаниями"):
        print("Релиз: задача не включена ни в один релиз")

    if proto and proto.decision == "Отложено":
        if proto.review_date:
            overdue = ""
            rd = _parse_date(proto.review_date)
            if rd and rd < date.today():
                overdue = " — 🔴 срок истёк, решение за РП"
            print(f"Пересмотр «Отложено»: {proto.review_date}{overdue}")
        if proto.resume_text:
            print(f"Возобновление (из {proto.path.name}): {proto.resume_text}")

    print("Следующий шаг:")
    for s in next_step(state):
        print(f"  • {s}")
    return 0


# --- сводка каталога доставки (roadmap) --------------------------------------

def parse_epic_card(path: Path) -> Tuple[str, str]:
    """Карточка эпика → (ID, наименование) из заголовка «# Карточка эпика — ID: имя»."""
    m = re.match(r"#\s*Карточка эпика\s*—\s*(.+?):\s*(.+)", read_text(path).splitlines()[0])
    if m:
        return m.group(1).strip(), m.group(2).strip()
    return path.parent.name, ""


def cmd_roadmap(delivery_root: Path) -> int:
    """Сводка всех задач и эпиков каталога доставки: статусы вместо памяти.

    Задача — подкаталог с 01-task-brief.md; эпик — подкаталог с 00-epic-brief.md
    (уровень над конвейером, 1c-epic-planning). Каталоги с «_» (например,
    _releases) пропускаются.
    """
    if not delivery_root.is_dir():
        print(f"❌ каталог не найден: {delivery_root}")
        return 2
    task_dirs: List[Path] = []
    epics: Dict[str, str] = {}
    for d in sorted(delivery_root.iterdir()):
        if not d.is_dir() or d.name.startswith("_"):
            continue
        if (d / EPIC_CARD).is_file():
            epic_id, epic_name = parse_epic_card(d / EPIC_CARD)
            epics[epic_id] = epic_name
        elif (d / ARTIFACTS[1]).is_file():
            task_dirs.append(d)
    if not task_dirs and not epics:
        print(f"❌ {delivery_root}: нет ни задач (01-task-brief.md), ни эпиков (00-epic-brief.md)")
        return 1

    print(f"Каталог доставки: {delivery_root}")
    tasks_by_epic: Dict[str, List[str]] = {}
    for epic_id, epic_name in epics.items():
        print(f"Эпик {epic_id} — {epic_name or '(имя не распознано)'}")
    print(f"Задач: {len(task_dirs)}" + (f", эпиков: {len(epics)}" if epics else "") + "\n")
    print("| Задача | Наименование | Эпик | Этап | Решение 06 | ✅/⏳/❌ |")
    print("|---|---|---|---|---|---|")
    for d in task_dirs:
        state = load_state(d)
        brief = state.brief
        task_id = brief.task_id if brief else d.name
        name = (brief.name if brief else "") or "(карточка не читается)"
        if len(name) > 48:
            name = name[:47] + "…"
        epic = brief.epic if brief else ""
        if epic:
            tasks_by_epic.setdefault(epic, []).append(task_id)
        reached = stage_reached(state)
        stage = f"{reached} {STAGES[reached - 1]}" if reached else "—"
        proto = state.latest_protocol
        decision = (proto.decision or "—") if proto else "—"
        counts = state.matrix.status_counts() if state.matrix else None
        crit = (f"{len(counts['ok'])}/{len(counts['deferred'])}/{len(counts['red'])}"
                if counts and state.matrix and state.matrix.rows else "—")
        print(f"| {task_id} | {name} | {epic or '—'} | {stage} | {decision} | {crit} |")

    # задачи ссылаются на эпик, карточки которого нет в каталоге, — сигнал рассинхрона
    orphan_epics = sorted(e for e in tasks_by_epic if e not in epics) if tasks_by_epic else []
    for e in orphan_epics:
        print(f"\n🟡 задач с полем «Эпик: {e}»: {len(tasks_by_epic[e])}, но карточка эпика {e} не найдена в этом каталоге")
    return 0


# --- проверка DoD-гейтов (check) --------------------------------------------

@dataclass
class Finding:
    level: str   # ERR | WARN | INFO
    gate: str
    message: str


def check_gate_1_2(state: TaskState, out: List[Finding]) -> None:
    gate = "1→2"
    brief = state.brief
    if not brief:
        if any((state.task_dir / ARTIFACTS[n]).is_file() for n in (2, 3, 4, 5)) or state.protocols:
            out.append(Finding("ERR", gate, "01-task-brief.md отсутствует, но later-артефакты есть — этап пройден без карточки"))
        return
    if not brief.criteria:
        out.append(Finding("ERR", gate, "в 01 не заполнен ни один критерий успеха"))
    no_check = [c[0] for c in brief.criteria if not c[2]]
    if no_check:
        out.append(Finding("WARN", gate, f"критерии успеха без «Как проверим»: {fmt_nums(no_check)}"))
    comparative = comparative_without_baseline(brief.criteria)
    if comparative:
        out.append(Finding("WARN", gate,
                           f"сравнительные критерии без базлайна «до» в «Как проверим»: {fmt_nums(comparative)} — "
                           f"состояние «до» после разработки не повторить (1c-planning)"))
    if not brief.confirm_section_found:
        out.append(Finding("ERR", gate, "в 01 нет секции «Подтверждение инициатора» (DoD-гейт)"))
    elif brief.confirm_unchecked:
        out.append(Finding("ERR", gate, f"инициатор не подтвердил карточку: не отмечено пунктов — {brief.confirm_unchecked}"))
    elif brief.confirm_checked:
        out.append(Finding("INFO", gate, f"подтверждение инициатора: {brief.confirm_checked} ✔"))


def check_gate_2_3(state: TaskState, out: List[Finding]) -> None:
    gate = "2→3"
    has_matrix = state.matrix is not None
    if not has_matrix:
        return
    missing = [ARTIFACTS[n] for n in (2, 3) if not (state.task_dir / ARTIFACTS[n]).is_file()]
    if missing:
        out.append(Finding("ERR", gate, f"матрица 04 есть, но отсутствуют {', '.join(missing)}"))
    matrix = state.matrix
    if matrix.parse_error:
        out.append(Finding("ERR", gate, f"04: {matrix.parse_error}"))
        return
    if not matrix.rows:
        out.append(Finding("ERR", gate, "матрица 04 пуста (нет строк с критериями)"))
        return
    for r in matrix.rows:
        for col in r.empty_trace_cols():
            out.append(Finding("ERR", gate, f"04, критерий {r.num}: пустая ячейка «{col}» (матрица без пустых ячеек — DoD)"))
        for title, val in (("Шаг сценария", r.step), ("Объект/код", r.obj), ("Проверка", r.check)):
            if val == "—":
                out.append(Finding("WARN", gate, f"04, критерий {r.num}: «{title}» = «—» — проверить, что трассировка не дырявая"))
    if state.brief:
        n_crit = len(state.brief.criteria)
        if n_crit and len(matrix.rows) < n_crit:
            out.append(Finding("WARN", gate,
                               f"в 04 строк ({len(matrix.rows)}) меньше, чем критериев успеха в 01 ({n_crit}) — "
                               f"возможен критерий без строки трассировки"))
        elif n_crit and len(matrix.rows) > n_crit:
            out.append(Finding("INFO", gate, f"строк 04 больше критериев успеха 01 на {len(matrix.rows) - n_crit} (например, [Откл])"))
    n_deferred_src = sum(1 for r in matrix.rows if "[Откл]" in r.criterion)
    if n_deferred_src:
        out.append(Finding("INFO", gate, f"критериев из отклонений 02 ([Откл]): {n_deferred_src}"))


def check_gate_3_4(state: TaskState, out: List[Finding]) -> None:
    """Согласование пакета 02/03/04 Оркестратором — этап 3 (1c-delivery-gate §0).

    Основной источник — артефакт 04a «Лист замечаний» (0.19.0+); штамп в 04 —
    legacy 0.18.0. Отсутствие согласования — WARN, не ERR: задачи, начатые
    до введения этапа, прожиты без него, задним числом не блокируем."""
    gate = "3→4"
    approval = state.approval
    mode = read_conveyor_mode(state.task_dir)
    dev_started = (state.task_dir / ARTIFACTS[5]).is_file() or bool(state.protocols)
    if approval is None:
        out.append(Finding("WARN", gate,
                           "нет согласования пакета 02/03/04 (лист замечаний 04a или legacy-штамп в 04) — "
                           f"задача начата до 0.19.0 или согласование пропущено; режим каталога: {mode}"))
        return
    if len(approval.checked) > 1:
        out.append(Finding("ERR", gate, f"в {approval.source} отмечено несколько решений согласования: {approval.checked}"))
    elif approval.decision is None:
        out.append(Finding("WARN", gate, f"решение согласования в {approval.source} не отмечено (Согласовано / Доработать)"))
    elif approval.decision == "Согласовано":
        if not approval.date:
            out.append(Finding("WARN", gate, f"согласование без даты в {approval.source}"))
        else:
            out.append(Finding("INFO", gate,
                               f"пакет 02/03/04 согласован ({approval.date}, режим {approval.mode or mode}; {approval.source})"))
    elif approval.decision == "Доработать":
        if dev_started:
            out.append(Finding("ERR", gate, "разработка начата при решении «Доработать» — возврат на проектирование/требования, без кода до «Согласовано»"))
        else:
            out.append(Finding("WARN", gate, "пакет отправлен на доработку — без кода до повторного согласования"))


def check_gate_4_5(state: TaskState, out: List[Finding]) -> None:
    if state.internal and state.internal.input_checks_failed:
        out.append(Finding("ERR", "4→5", "в шапке 05 «Входные проверки разработки» содержит ❌ — ревью-прогоны не зелёные"))
    elif state.internal:
        out.append(Finding("INFO", "4→5", "код и ревью-прогоны вне досягаемости скрипта; статусы зафиксированы в шапке 05"))


def check_gate_5_6(state: TaskState, out: List[Finding]) -> None:
    gate = "5→6"
    rep = state.internal
    if not rep:
        return
    for red in rep.red_rows:
        out.append(Finding("ERR", gate, f"красная строка 05: {red} — самовозврат в Разработку (класс A)"))
    if rep.verdict_unchecked:
        out.append(Finding("ERR", gate, f"вердикт 05 не завершён: не отмечено пунктов — {rep.verdict_unchecked}"))
    if rep.red_rows or rep.verdict_unchecked:
        return
    if rep.verdict_checked:
        out.append(Finding("INFO", gate, "05 без красных строк, вердикт отмечен" + (" (статический режим)" if rep.static_mode else "")))


def check_gate_6_7(state: TaskState, out: List[Finding]) -> None:
    gate = "6→7"
    proto = state.latest_protocol
    if not proto:
        return
    if len(proto.decisions_checked) > 1:
        out.append(Finding("ERR", gate, f"в 06 отмечено несколько решений: {proto.decisions_checked}"))
        return
    d = proto.decision
    if d is None:
        out.append(Finding("ERR", gate, "в 06 не отмечено решение (Принято / Возврат / Отложено)"))
    elif d == "Отложено":
        out.append(Finding("INFO", gate,
                           f"легальная пауза конвейера ({proto.path.name}) — переход на Релиз запрещён до возобновления "
                           f"(протокол .r{proto.round + 1})"))
        if not proto.review_date:
            out.append(Finding("WARN", gate,
                               "«Отложено» без даты пересмотра — пауза вне контроля (правило 1c-external-acceptance)"))
        else:
            rd = _parse_date(proto.review_date)
            if rd and rd < date.today():
                out.append(Finding("WARN", gate,
                                   f"срок пересмотра «Отложено» истёк ({proto.review_date}) — РП решает судьбу задачи "
                                   f"и черновика релиза (расконсервация / возврат в бэклог / закрытие)"))
    elif d == "Возврат":
        out.append(Finding("ERR", gate, "решение «Возврат» — гейт не пройден"))
        if not state.rework_list:
            out.append(Finding("ERR", gate, "лист возврата 06a-rework-list.md не найден"))
        else:
            out.append(Finding("INFO", gate, f"лист возврата 06a есть; классы замечаний: {proto.remark_classes or '?'}"))
    else:
        out.append(Finding("INFO", gate, f"решение «{d}» ({proto.date or 'дата не указана'}) — гейт пройден"))
    # Дважды упавший критерий — обязательная автоматизация (rework-rules):
    # ручная проверка дважды пропустила одно и то же, третья попытка бессмысленна.
    matrix_by_num = {r.num: r for r in state.matrix.rows} if state.matrix else {}
    for num, rounds in sorted(repeated_failures(state.protocols).items()):
        if len(rounds) < 2:
            continue
        rounds_str = ", ".join(str(x) for x in rounds)
        row = matrix_by_num.get(num)
        if row is not None and re.search(r"unit|bdd", row.check, re.I):
            out.append(Finding("INFO", gate,
                               f"критерий {num} упал ❌ в раундах ({rounds_str}) — проверка переведена на unit/BDD"))
        else:
            out.append(Finding("WARN", gate,
                               f"критерий {num} упал ❌ в раундах ({rounds_str}) — следующая итерация закрывает его "
                               f"unit/BDD-тестом, а не ручной проверкой (rework-rules)"))


def check_gate_7(state: TaskState, out: List[Finding]) -> None:
    gate = "7"
    proto = state.latest_protocol
    decision = proto.decision if proto else None
    if not state.releases:
        if decision in ("Принято", "Принято с замечаниями"):
            out.append(Finding("INFO", gate, "задача принята, но не включена ни в один релиз (1c-release)"))
        return
    for r in state.releases:
        label = f"_releases/{r.dir_name}"
        if r.is_draft:
            if decision in ("Принято", "Принято с замечаниями"):
                out.append(Finding("WARN", gate, f"{label} (черновик): приёмка уже закрыта «принято» — повысить до боевого"))
            else:
                out.append(Finding("INFO", gate, f"{label} (черновик) — легален при «Отложено»/«Возврат»; в поставку не включять"))
        else:
            if decision not in ("Принято", "Принято с замечаниями"):
                out.append(Finding("ERR", gate,
                                   f"{label} (боевой): задача в релизе без протокола «принято» "
                                   f"(решение: {decision or 'нет'})"))
            elif not r.task_included:
                out.append(Finding("WARN", gate, f"{label}: задача упомянута, но отсутствует в «Задачи в составе»"))
            else:
                out.append(Finding("INFO", gate, f"{label}: задача в составе, протокол — {r.protocol_ref or 'ссылка не распознана'}"))
                if state.brief and not state.brief.retrospective_filled:
                    out.append(Finding("WARN", gate,
                                       "ретроспектива 01 не заполнена (оценка/факт, возвраты, вывод) — "
                                       "петля оценки не закрыта (DoD «7 → закрытие»)"))


def cmd_check(task_dir: Path) -> int:
    state = load_state(task_dir)
    if not state.brief and not state.matrix and not state.internal and not state.protocols:
        print(f"❌ {task_dir}: не найдено артефактов конвейера (01–06)")
        return 1
    out: List[Finding] = []
    check_gate_1_2(state, out)
    check_gate_2_3(state, out)
    check_gate_3_4(state, out)
    check_gate_4_5(state, out)
    check_gate_5_6(state, out)
    check_gate_6_7(state, out)
    check_gate_7(state, out)

    icons = {"ERR": "❌", "WARN": "🟡", "INFO": "✅"}
    for gate in ("1→2", "2→3", "3→4", "4→5", "5→6", "6→7", "7"):
        gate_findings = [f for f in out if f.gate == gate]
        if not gate_findings:
            continue
        print(f"Гейт {gate}:")
        for f in gate_findings:
            print(f"  {icons[f.level]} {f.message}")
    n_err = sum(1 for f in out if f.level == "ERR")
    n_warn = sum(1 for f in out if f.level == "WARN")
    summary = f"Итог: ERR {n_err}, WARN {n_warn}"
    print(summary + (" — есть блокирующие нарушения DoD" if n_err else " — блокирующих нарушений нет"))
    return 1 if n_err else 0


# --- CLI ---------------------------------------------------------------------

USAGE = """Использование:
  python3 scripts/delivery_tools.py status <каталог-задачи>
  python3 scripts/delivery_tools.py check  <каталог-задачи>
  python3 scripts/delivery_tools.py roadmap <каталог-доставки>

status  — сводка состояния задачи по артефактам 01–06 (этап, статусы матрицы,
          согласование Оркестратора, красные строки, решение протокола,
          релиз, следующий шаг).
check   — механическая проверка DoD-гейтов (exit 1 при ERR). Каталог задачи —
          обычно docs/delivery/<ID-задачи> проекта.
roadmap — сводка всех задач и эпиков каталога доставки (обычно docs/delivery):
          этап конвейера и решение протокола каждой задачи, связь с эпиками
          (1c-epic-planning). Статусы вместо памяти."""


def main(argv: List[str]) -> int:
    if len(argv) != 3 or argv[1] not in ("status", "check", "roadmap"):
        print(USAGE)
        return 2
    task_dir = Path(argv[2])
    if not task_dir.is_dir():
        print(f"❌ каталог не найден: {task_dir}")
        return 2
    if argv[1] == "status":
        return cmd_status(task_dir)
    if argv[1] == "roadmap":
        return cmd_roadmap(task_dir)
    return cmd_check(task_dir)


if __name__ == "__main__":
    sys.exit(main(sys.argv))
