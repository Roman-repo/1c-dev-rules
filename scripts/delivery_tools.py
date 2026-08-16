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

Выход: check — 0 нет ERR (WARN допустимы), 1 есть ERR. status — всегда 0
(1 только если каталог не похож на задачу конвейера).

Без зависимостей: только стандартная библиотека.
"""
from __future__ import annotations

import re
import sys
from dataclasses import dataclass, field
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

STAGES = [
    "Планирование", "Проектирование", "Разработка",
    "Внутренняя приёмка", "Внешняя приёмка", "Релиз",
]

DECISIONS = ("Принято с замечаниями", "Принято", "Возврат", "Отложено")

# --- разбор markdown --------------------------------------------------------

SEP_CELL_RE = re.compile(r"^:?-{3,}:?$")


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


def table_rows(section_text: str) -> Tuple[List[str], List[List[str]]]:
    """Таблица секции → (заголовки, строки-ячейки). Строки-разделители пропущены."""
    rows: List[List[str]] = []
    for line in section_text.splitlines():
        s = line.strip()
        if not s.startswith("|"):
            continue
        cells = [c.strip() for c in s.strip("|").split("|")]
        if cells and all(SEP_CELL_RE.match(c) for c in cells if c != ""):
            continue
        rows.append(cells)
    if not rows:
        return [], []
    return rows[0], rows[1:]


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
    criteria: List[Tuple[str, str, str]] = field(default_factory=list)  # (№, критерий, как проверим)
    confirm_checked: int = 0
    confirm_unchecked: int = 0
    confirm_section_found: bool = False


def parse_brief(path: Path) -> Brief:
    brief = Brief()
    sections = md_sections(read_text(path))
    m = re.match(r"#\s*Карточка задачи\s*—\s*(.+?):\s*(.+)", read_text(path).splitlines()[0])
    if m:
        brief.task_id, brief.name = m.group(1).strip(), m.group(2).strip()
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
class Matrix:
    rows: List[MatrixRow] = field(default_factory=list)
    parse_error: Optional[str] = None

    def status_counts(self) -> Dict[str, List[str]]:
        """Критерии по типу статуса: ok (✅), ok_static, deferred (⏳), red (❌), todo (☐/пусто)."""
        out = {"ok": [], "ok_static": [], "deferred": [], "red": [], "todo": []}
        for r in self.rows:
            if "❌" in r.status:
                out["red"].append(r.num)
            if "⏳" in r.status:
                out["deferred"].append(r.num)
            if "✅" in r.status:
                out["ok_static" if "статич" in r.status.lower() else "ok"].append(r.num)
            if "❌" not in r.status and "✅" not in r.status and "⏳" not in r.status:
                out["todo"].append(r.num)
        return out


def parse_matrix(path: Path) -> Matrix:
    matrix = Matrix()
    sections = md_sections(read_text(path))
    sec = find_section(sections, "Матрица")
    if sec is None:
        matrix.parse_error = "секция «Матрица трассировки и критерии приёмки» не найдена"
        return matrix
    headers, rows = table_rows(sec)
    idx = {
        "num": col_index(headers, "№"), "criterion": col_index(headers, "Критерий"),
        "step": col_index(headers, "Шаг"), "obj": col_index(headers, "Объект"),
        "check": col_index(headers, "Проверка"), "status": col_index(headers, "Статус"),
    }
    for r in rows:
        criterion = cell(r, idx["criterion"])
        if not criterion:  # заглушка шаблона
            continue
        matrix.rows.append(MatrixRow(
            num=cell(r, idx["num"]), criterion=criterion,
            step=cell(r, idx["step"]), obj=cell(r, idx["obj"]),
            check=cell(r, idx["check"]), status=cell(r, idx["status"]),
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
    remark_classes: List[str] = field(default_factory=list)


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
    remarks = find_section(sections, "Замечания")
    if remarks:
        headers, rows = table_rows(remarks)
        i_class = col_index(headers, "Класс")
        for r in rows:
            c = cell(r, i_class).upper()
            if c in ("A", "B", "C", "D"):
                proto.remark_classes.append(c)
    return proto


def find_protocols(task_dir: Path) -> List[Protocol]:
    """Все раунды протокола, упорядочены по номеру (последний — актуальный)."""
    protos = [parse_protocol(p) for p in sorted(task_dir.glob(PROTOCOL_GLOB))]
    return sorted(protos, key=lambda p: p.round)


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
    internal: Optional[InternalReport] = None
    protocols: List[Protocol] = field(default_factory=list)
    rework_list: Optional[Path] = None
    releases: List[ReleaseRef] = field(default_factory=list)

    @property
    def latest_protocol(self) -> Optional[Protocol]:
        return self.protocols[-1] if self.protocols else None


def load_state(task_dir: Path) -> TaskState:
    state = TaskState(task_dir=task_dir)
    if (task_dir / ARTIFACTS[1]).is_file():
        state.brief = parse_brief(task_dir / ARTIFACTS[1])
    if (task_dir / ARTIFACTS[4]).is_file():
        state.matrix = parse_matrix(task_dir / ARTIFACTS[4])
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
                         f"(переход на Релиз запрещён, DoD 5→6)")
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
                steps.append("принято — этап 6 Релиз (1c-release): включить задачу в состав релиза")
    return steps


def cmd_status(task_dir: Path) -> int:
    state = load_state(task_dir)
    brief = state.brief
    if not brief:
        print(f"❌ {task_dir}: не найден 01-task-brief.md — это каталог задачи конвейера?")
        return 1
    print(f"Задача: {brief.task_id} — {brief.name}")
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

    # Этап: самый поздний существующий артефакт + решение протокола.
    # Артефакт N принадлежит этапу N (01→1 … 05→4), протокол — этапу 5;
    # «принято» открывает этап 6 «Релиз».
    reached = max((n for n in ARTIFACTS if (task_dir / ARTIFACTS[n]).is_file()), default=0)
    if proto:
        reached = 5
        if proto.decision in ("Принято", "Принято с замечаниями"):
            reached = 6
    stage = STAGES[reached - 1]
    line = f"Этап: {reached} «{stage}»"
    if proto and proto.decision:
        line += f" — решение «{proto.decision}»" + (f", {proto.date}" if proto.date else "")
    print(line)

    if state.matrix and state.matrix.rows:
        c = state.matrix.status_counts()
        print(f"Матрица 04: {len(state.matrix.rows)} критериев — "
              f"✅ {len(c['ok']) + len(c['ok_static'])} (статич. {len(c['ok_static'])}), "
              f"⏳ {len(c['deferred'])} ({fmt_nums(c['deferred'])}), "
              f"❌ {len(c['red'])} ({fmt_nums(c['red'])}), "
              f"☐ {len(c['todo'])} ({fmt_nums(c['todo'])})")
    if state.internal:
        red = "🔴 " + "; ".join(state.internal.red_rows) if state.internal.red_rows else "нет"
        static = ", статический режим" if state.internal.static_mode else ""
        print(f"Отчёт 05: красные строки — {red}{static}")
    if state.releases:
        for r in state.releases:
            kind = "ЧЕРНОВИК" if r.is_draft else "боевой"
            where = "в составе" if r.task_included else ("в «Отбор отклонён»" if r.task_rejected else "упомянут")
            print(f"Релиз: _releases/{r.dir_name} — {kind}, задача {where}")
    elif proto and proto.decision in ("Принято", "Принято с замечаниями"):
        print("Релиз: задача не включена ни в один релиз")

    if proto and proto.decision == "Отложено" and proto.resume_text:
        print(f"Возобновление (из {proto.path.name}): {proto.resume_text}")

    print("Следующий шаг:")
    for s in next_step(state):
        print(f"  • {s}")
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
    if state.internal and state.internal.input_checks_failed:
        out.append(Finding("ERR", "3→4", "в шапке 05 «Входные проверки разработки» содержит ❌ — ревью-прогоны не зелёные"))
    elif state.internal:
        out.append(Finding("INFO", "3→4", "код и ревью-прогоны вне досягаемости скрипта; статусы зафиксированы в шапке 05"))


def check_gate_4_5(state: TaskState, out: List[Finding]) -> None:
    gate = "4→5"
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


def check_gate_5_6(state: TaskState, out: List[Finding]) -> None:
    gate = "5→6"
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
    elif d == "Возврат":
        out.append(Finding("ERR", gate, "решение «Возврат» — гейт не пройден"))
        if not state.rework_list:
            out.append(Finding("ERR", gate, "лист возврата 06a-rework-list.md не найден"))
        else:
            out.append(Finding("INFO", gate, f"лист возврата 06a есть; классы замечаний: {proto.remark_classes or '?'}"))
    else:
        out.append(Finding("INFO", gate, f"решение «{d}» ({proto.date or 'дата не указана'}) — гейт пройден"))


def check_gate_6(state: TaskState, out: List[Finding]) -> None:
    gate = "6"
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
    check_gate_6(state, out)

    icons = {"ERR": "❌", "WARN": "🟡", "INFO": "✅"}
    for gate in ("1→2", "2→3", "3→4", "4→5", "5→6", "6"):
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

status — сводка состояния задачи по артефактам 01–06 (этап, статусы матрицы,
         красные строки, решение протокола, релиз, следующий шаг).
check  — механическая проверка DoD-гейтов (exit 1 при ERR). Каталог задачи —
         обычно docs/delivery/<ID-задачи> проекта."""


def main(argv: List[str]) -> int:
    if len(argv) != 3 or argv[1] not in ("status", "check"):
        print(USAGE)
        return 2
    task_dir = Path(argv[2])
    if not task_dir.is_dir():
        print(f"❌ каталог не найден: {task_dir}")
        return 2
    if argv[1] == "status":
        return cmd_status(task_dir)
    return cmd_check(task_dir)


if __name__ == "__main__":
    sys.exit(main(sys.argv))
