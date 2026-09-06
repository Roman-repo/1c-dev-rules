#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
metadata_diagram.py — генератор Mermaid-диаграмм метаданных (issue #96):
граф объектов области задачи + соседей + потоков движения данных из XML
EDT-выгрузки (.mdo). Диаграмма — ПРЕДСТАВЛЕНИЕ, не проверка (ADR-002):
не влияет на вердикты и exit-коды этапа «Код ревью», источник истины о
связях — те же .mdo, вывод — артефакт разработки docs/delivery/<ЗАДАЧА>/
diagrams/metadata-r<N>.md (НЕ номерной артефакт конвейера: delivery_tools
не расширяется, решение инициатора №2 по issue #96).

Зачем: связи объектов и потоки данных конвейер держит текстом 03 и в голове
исполнителя; impact-анализ и секция «Кросс-влияние» — ручной grep по XML.
Диаграмма собирается детерминированно одной командой: изменённые объекты +
соседи 1-го уровня + рёбра (кто ссылается, куда идут движения) — картина
для согласования (04a) и ревью (05a); тот же дифф → бит-в-бит тот же файл.

Использование:
    python3 scripts/metadata_diagram.py <пути|каталоги>            # в stdout
    python3 scripts/metadata_diagram.py --diff main --src-root src
        --out docs/delivery/<ЗАДАЧА>/diagrams/metadata-r1.md
    python3 scripts/metadata_diagram.py src/Documents/Д1 --src-root src
        [--mode objects|dataflow|both] [--depth N] [--max-nodes N]
        [--cache-dir DIR]

Источники рёбер (декларативный срез, заземлено по toir2):
    <registerRecords>   документ → регистр (движение при проведении)   [dataflow]
    <type><types>       объект → объект ссылки (реквизиты/ТЧ/измерения)[objects]
    <content><mdObject> план обмена → объект (состав)                  [objects]
    <content>текст      подсистема → объект (кластер; только когда
                        подсистема сама в области)                     [objects]
    <owners>            подчинённый справочник/ПВХ → владелец          [objects]

Выход: 0 — граф построен (в т.ч. пустая заглушка «объектов нет» — правки
только в .bsl/.form/.rights); 2 — ошибка использования (нет входов, путь
не найден, git недоступен). Битый XML узла — WARN в шапке и узел без рёбер
(конвенция слоя: битый XML не валит прогон).

Переиспользует metadata_scan через import (load_object/diff_paths +
XML-хелперы) — свой обход дерева НЕ пишем; исключение — индекс рёбер
(данных, которых в индексе слоя нет) с кэшем по mtime-хэшу дерева по
паттерну Context: кэш — ускоритель, не источник истины, несовпадение
хэша/битый файл — обход заново.

Потолок (честно в легенде каждой диаграммы): записи регистров из кода
модулей (.bsl), связи через запросы и подписки на события НЕ видны — это
декларативный срез .mdo.
"""
from __future__ import annotations

import argparse
import datetime
import hashlib
import json
import os
import shlex
import subprocess
import sys
from pathlib import Path
from typing import Dict, List, Optional, Set, Tuple

sys.path.insert(0, str(Path(__file__).resolve().parent))
from metadata_scan import (DIR_TYPES, L, MdoObject, diff_paths,  # noqa: E402
                           infer_src_root, kid, kids, load_object)

Edge = Tuple[str, str, str]  # (исток, сток, вид: reference/movement/…)

# --- виды объектов ---------------------------------------------------------------

# Каталог выгрузки → вид (расширение DIR_TYPES слоя: Subsystems/ExchangePlans/
# Enums/… нужны диаграмме, слою — нет; общий префикс берём из DIR_TYPES)
DIR_KINDS = dict(DIR_TYPES)
DIR_KINDS.update({
    "Subsystems": "Subsystem", "ExchangePlans": "ExchangePlan",
    "Enums": "Enum", "AccountingRegisters": "AccountingRegister",
    "ChartsOfCalculationTypes": "ChartOfCalculationTypes",
    "Tasks": "Task", "BusinessProcesses": "BusinessProcess",
})
KNOWN_KINDS = set(DIR_KINDS.values())

# вид → (надпись в узле, форма Mermaid: rect/cyl/rhomb/hex)
KIND_VIEW: Dict[str, Tuple[str, str]] = {
    "Document": ("Документ", "rect"),
    "Catalog": ("Справочник", "rect"),
    "Enum": ("Перечисление", "rect"),
    "ChartOfCharacteristicTypes": ("План видов характеристик", "rect"),
    "ChartOfCalculationTypes": ("План видов расчёта", "rect"),
    "Task": ("Задача", "rect"),
    "BusinessProcess": ("Бизнес-процесс", "rect"),
    "Constant": ("Константа", "rect"),
    "Report": ("Отчёт", "rect"),
    "DataProcessor": ("Обработка", "rect"),
    "InformationRegister": ("Регистр сведений", "cyl"),
    "AccumulationRegister": ("Регистр накопления", "cyl"),
    "AccountingRegister": ("Регистр бухгалтерии", "cyl"),
    "ExchangePlan": ("План обмена", "rhomb"),
    "Subsystem": ("Подсистема", "hex"),
}

EDGE_KIND_RU = {"reference": "ссылка", "movement": "движение",
                "exchange": "обмен", "ownership": "владение",
                "composition": "состав"}
MODE_KINDS = {
    "objects": frozenset(("reference", "ownership", "exchange")),
    "dataflow": frozenset(("movement",)),
    "both": frozenset(("reference", "ownership", "exchange", "movement")),
}

# <types>CatalogRef.X</types> → Catalog.X; суффиксы значения типа (ссылка,
# объект, набор записей, менеджер значения, строка ТЧ) снимаются
_TYPE_SUFFIXES = ("Ref", "Object", "RecordSet", "ValueManager", "Row")


def ref_token(value: str) -> Optional[str]:
    """Токен вида из значения <types>: CatalogRef.X/CatalogObject.X/
    InformationRegisterRecordSet.X/Characteristic.X/… → Catalog.X;
    примитив (String/Number/…), определяемый тип и «любая ссылка» → None."""
    v = value.strip()
    if not v or " " in v:
        return None
    if ":" in v:  # ns-префикс вида d5p1:CatalogRef.X
        head, rest = v.split(":", 1)
        if "." not in head:
            v = rest
    if "." not in v:
        return None
    pfx, name = v.split(".", 1)
    if pfx == "Characteristic":
        pfx = "ChartOfCharacteristicTypes"
    elif pfx not in KNOWN_KINDS:
        for suf in _TYPE_SUFFIXES:
            if pfx.endswith(suf) and len(pfx) > len(suf):
                pfx = pfx[:-len(suf)]
                break
    if pfx in KNOWN_KINDS and name:
        return f"{pfx}.{name}"
    return None


def direct_token(value: str) -> Optional[str]:
    """Токен из прямых значений (registerRecords/owners/content): вид уже
    голый — Catalog.X; Role.*/мусор → None (без снятия суффиксов)."""
    v = value.strip()
    if not v or " " in v:
        return None
    if ":" in v:
        head, rest = v.split(":", 1)
        if "." not in head:
            v = rest
    if "." not in v:
        return None
    pfx, name = v.split(".", 1)
    if pfx in KNOWN_KINDS and name:
        return f"{pfx}.{name}"
    return None


# --- объекты и рёбра ---------------------------------------------------------------


def object_kind(obj: MdoObject) -> Optional[str]:
    """Вид объекта: тег корня (новый формат EDT), иначе каталог выгрузки."""
    if obj.root is not None:
        t = L(obj.root)
        if t in KNOWN_KINDS or t == "Configuration":
            return t
    return DIR_KINDS.get(obj.dir.parent.name)


def object_token(obj: MdoObject) -> Optional[str]:
    kind = object_kind(obj)
    if not kind or kind == "Configuration" or not obj.name:
        return None
    return f"{kind}.{obj.name}"


def extract_edges(obj: MdoObject) -> List[Edge]:
    """Рёбра объекта по декларативным тегам .mdo (чистая функция — по одному
    источнику на вид; дубли и петли схлопнуты, сортировка детерминированная)."""
    if obj.root is None:
        return []
    src = object_token(obj)
    if not src:
        return []
    out: Set[Edge] = set()
    for el in kids(obj.root, "registerRecords"):
        t = direct_token(el.text or "")
        if t and t != src:
            out.add((src, t, "movement"))
    for el in kids(obj.root, "owners"):
        t = direct_token(el.text or "")
        if t and t != src:
            out.add((src, t, "ownership"))
    kind = object_kind(obj)
    if kind in ("ExchangePlan", "Subsystem"):
        edge_kind = "exchange" if kind == "ExchangePlan" else "composition"
        for c in kids(obj.root, "content"):
            md = kid(c, "mdObject")
            raw = (md.text if md is not None else c.text) or ""
            t = direct_token(raw)
            if t and t != src:
                out.add((src, t, edge_kind))
    for el in obj.root.iter():
        if L(el) != "type":
            continue
        for t_el in kids(el, "types"):
            t = ref_token(t_el.text or "")
            if t and t != src:
                out.add((src, t, "reference"))
    return sorted(out)


class EdgeIndex:
    """Индекс рёбер полного дерева src: один проход по .mdo, кэш по
    mtime-хэшу (паттерн Context слоя; кэш не источник истины)."""

    VERSION = 1
    MARKERS = (b"registerRecords", b"<owners>", b"<content>", b"<types>")

    def __init__(self, src_root: Path, cache_dir: Optional[Path]):
        self.src_root = src_root
        self.cache_dir = cache_dir
        self.nodes: Dict[str, str] = {}   # токен → вид (для формы узла)
        self.edges: List[Edge] = []
        self.broken: List[str] = []       # relpath битых XML
        self._build()

    def _build(self) -> None:
        cache_file = None
        if self.cache_dir is not None:
            cache_file = self.cache_dir / f"diagram-edges-{self._tree_key()[:24]}.json"
            if self._load(cache_file):
                return
        for dirpath, dirnames, filenames in os.walk(self.src_root):
            dirnames[:] = sorted(d for d in dirnames if d != ".git")
            for fn in sorted(filenames):
                if not fn.endswith(".mdo"):
                    continue
                p = Path(dirpath) / fn
                try:
                    data = p.read_bytes()
                except OSError:
                    continue
                # дешёвый фильтр до ET: файлы без источников рёбер не парсим
                if not any(m in data for m in self.MARKERS):
                    continue
                obj = load_object(p)
                tok = object_token(obj)
                if not tok:
                    continue
                if obj.root is None:
                    self.broken.append(str(p.relative_to(self.src_root)))
                    continue
                self.nodes[tok] = tok.split(".", 1)[0]
                self.edges.extend(extract_edges(obj))
        self.edges = sorted(set(self.edges))
        self.broken = sorted(set(self.broken))
        if cache_file is not None:
            self._save(cache_file)

    def _tree_key(self) -> str:
        """Хэш входов индекса: дерево src по .mdo (путь/размер/mtime)."""
        h = hashlib.sha256()
        h.update(f"v{self.VERSION}".encode())
        h.update(str(self.src_root.resolve()).encode())
        for dirpath, dirnames, filenames in os.walk(self.src_root):
            dirnames[:] = sorted(d for d in dirnames if d != ".git")
            for fn in sorted(filenames):
                if not fn.endswith(".mdo"):
                    continue
                p = Path(dirpath) / fn
                try:
                    s = p.stat()
                except OSError:
                    continue
                h.update(f"{p.relative_to(self.src_root)}:{s.st_size}:"
                         f"{s.st_mtime_ns}\n".encode())
        return h.hexdigest()

    def _load(self, path: Path) -> bool:
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            if data.get("version") != self.VERSION:
                return False
            self.nodes = {t: k for t, k in data["nodes"].items()}
            self.edges = sorted(set(tuple(e) for e in data["edges"]))
            self.broken = sorted(set(data["broken"]))
        except (OSError, ValueError, KeyError, TypeError):
            return False
        return True

    def _save(self, path: Path) -> None:
        try:  # best-effort: кэш — ускоритель, ошибка не фатальна
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(json.dumps({
                "version": self.VERSION, "nodes": self.nodes,
                "edges": [list(e) for e in self.edges],
                "broken": self.broken,
            }, ensure_ascii=False), encoding="utf-8")
        except OSError:
            pass


# --- компоновка --------------------------------------------------------------------


def build_graph(area: Set[str], edges: List[Edge], mode: str, depth: int,
                max_nodes: int) -> Tuple[Set[str], List[Edge], List[str]]:
    """Область + соседи до depth по рёбрам режима; обрезание дальних при
    превышении max_nodes (область не обрезается). Возвращает (узлы, рёбра,
    отброшенные). Сортировки детерминированы — тот же вход даёт тот же файл."""
    mode_kinds = MODE_KINDS[mode]
    area_subsystems = {t for t in area if t.startswith("Subsystem.")}

    def usable(e: Edge) -> bool:
        # состав подсистем — кластер, а не impact-ребро: участвует, только
        # когда подсистема сама в области (иначе каждая диаграмма тащила бы
        # сотни узлов интерфейсной группировки); composition не входит в
        # MODE_KINDS — иначе он включался бы и для чужих подсистем
        if e[2] == "composition":
            return e[0] in area_subsystems and mode in ("objects", "both")
        return e[2] in mode_kinds

    adj: Dict[str, Set[str]] = {}
    for s, d, _ in filter(usable, edges):
        adj.setdefault(s, set()).add(d)
        adj.setdefault(d, set()).add(s)

    dist: Dict[str, int] = {t: 0 for t in area}
    frontier = set(area)
    for step in range(1, depth + 1):
        nxt: Set[str] = set()
        for t in frontier:
            for n in adj.get(t, ()):
                if n not in dist:
                    dist[n] = step
                    nxt.add(n)
        frontier = nxt
        if not frontier:
            break

    included = set(dist)
    dropped: List[str] = []
    if len(included) > max_nodes:
        keep = set(area)
        for d_, t in sorted((d_, t) for t, d_ in dist.items() if d_ > 0):
            if len(keep) >= max_nodes:
                break
            keep.add(t)
        dropped = sorted(included - keep)
        included = keep
    kept_edges = sorted(e for e in filter(usable, edges)
                        if e[0] in included and e[1] in included)
    return included, kept_edges, dropped


# --- mermaid-рендер ----------------------------------------------------------------


def mermaid_escape(label: str) -> str:
    """Центральное экранирование подписей Mermaid (кириллица допускается,
    ломаются кавычки и обратный слэш)."""
    return label.replace("\\", "\\\\").replace('"', '\\"')


def node_line(nid: str, token: str) -> str:
    kind = token.split(".", 1)[0]
    _, name = token.split(".", 1)
    ru, shape = KIND_VIEW.get(kind, (kind, "rect"))
    lb = mermaid_escape(f"{ru} {name}")
    if shape == "cyl":
        return f'    {nid}[("{lb}")]'
    if shape == "rhomb":
        return f'    {nid}{{"{lb}"}}'
    if shape == "hex":
        return f'    {nid}{{{{"{lb}"}}}}'
    return f'    {nid}["{lb}"]'


def slice_date(args: argparse.Namespace,
               area_files: List[Path]) -> str:
    """Детерминированная «дата среза»: от стеночных часов НЕ зависит (тот же
    дифф → бит-в-бит тот же файл) — коммит REF либо mtime входов."""
    if args.diff:
        try:
            res = subprocess.run(
                ["git", "show", "-s", "--format=%cI", args.diff],
                capture_output=True, text=True, check=True)
            return f"{res.stdout.strip()} (по дате коммита {args.diff})"
        except (subprocess.CalledProcessError, OSError):
            pass
    if area_files:
        ts = max(p.stat().st_mtime for p in area_files if p.exists())
        return (datetime.datetime.fromtimestamp(ts)
                .isoformat(timespec="seconds") + " (по mtime входов)")
    return "неизвестно"


def render(area: Set[str], included: Set[str], edges: List[Edge],
           dropped: List[str], warnings: List[str], args: argparse.Namespace,
           area_files: List[Path], effective_args: List[str]) -> str:
    cmd = "python3 scripts/metadata_diagram.py " + shlex.join(effective_args)
    lines: List[str] = []
    lines.append("# Диаграмма метаданных — impact-карта области\n")
    lines.append(f"> Воспроизведение: `{cmd}`")
    lines.append(f"> Дата среза: {slice_date(args, area_files)}")
    ru_kinds = "/".join(sorted(EDGE_KIND_RU[k] for k in MODE_KINDS[args.mode]))
    lines.append(f"> Режим: {args.mode} ({ru_kinds}); глубина соседей: "
                 f"{args.depth}; лимит узлов: {args.max_nodes}\n")
    lines.append("- Область: " + ", ".join(f"`{t}`" for t in sorted(area)))
    lines.append(f"- Узлов: {len(included)} (область: {len(area)}, соседи: "
                 f"{len(included) - len(area)}); рёбер: {len(edges)}")
    for w in warnings:
        lines.append(f"- ⚠ {w}")
    if dropped:
        head = ", ".join(f"`{t}`" for t in dropped[:10])
        more = f"… и ещё {len(dropped) - 10}" if len(dropped) > 10 else ""
        lines.append(f"- ✂ обрезано до лимита {args.max_nodes}: "
                     f"{len(dropped)} дальних соседей ({head}{more}) — "
                     "поднимите --max-nodes или сузьте область")

    ids = {t: f"n{i}" for i, t in enumerate(sorted(included), start=1)}
    lines.append("")
    lines.append("```mermaid")
    lines.append("flowchart LR")
    if area & included:
        lines.append("    classDef area stroke-width:3px")
    for t in sorted(included):
        lines.append(node_line(ids[t], t))
    for s, d, k in edges:
        lines.append(f'    {ids[s]} -->|"{EDGE_KIND_RU[k]}"| {ids[d]}')
    if area & included:
        lines.append("    class " + ",".join(ids[t] for t in sorted(area & included))
                     + " area")
    lines.append("```\n")
    lines.append("**Легенда.** Формы: `[…]` — объекты (документ, справочник, "
                 "перечисление, ПВХ/ПВР, константа, отчёт, обработка), "
                 "`[(…)]` — регистры, `{…}` — планы обмена, `{{…}}` — "
                 "подсистемы; толстая рамка — объекты области задачи. "
                 "Подписи рёбер: «ссылка» — реквизит/измерение ссылочного "
                 "типа, «движение» — registerRecords (движения при "
                 "проведении), «обмен» — состав плана обмена, «владение» — "
                 "подчинённый справочник/ПВХ, «состав» — содержимое "
                 "подсистемы.")
    lines.append("\n**Границы декларативного среза.** Записи регистров из "
                 "кода модулей (`.bsl`), связи через запросы и подписки на "
                 "события НЕ видны — источник только декларативные связи "
                 "`.mdo`. Таблица «Кросс-влияние» в 03 — что проверяем; "
                 "эта диаграмма — картина целого.")
    return "\n".join(lines) + "\n"


def render_stub(effective_args: List[str]) -> str:
    return ("# Диаграмма метаданных\n\n"
            "Объектов метаданных (.mdo) в области нет — диаграмма не "
            "строилась (правки только в модулях/формах/.rights или пустой "
            "дифф).\n\n"
            "> Воспроизведение: `python3 scripts/metadata_diagram.py "
            + shlex.join(effective_args) + "`\n")


# --- CLI ---------------------------------------------------------------------------


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(
        description="Mermaid-диаграмма метаданных: объекты области задачи + "
                    "соседи + рёбра потоков (ссылки/движения/обмен/владение) "
                    "из XML EDT-выгрузки (.mdo); артефакт разработки, не "
                    "проверка (ADR-002)")
    ap.add_argument("paths", nargs="*",
                    help="файлы .mdo или каталоги объектов (каталог — рекурсивно)")
    ap.add_argument("--diff", metavar="REF",
                    help="область = объекты, чьи .mdo изменены относительно REF (git)")
    ap.add_argument("--src-root", metavar="DIR",
                    help="корень src EDT-выгрузки: индекс полного дерева даёт "
                         "соседей (входящих ссылок); без него — только рёбра "
                         "самих объектов области")
    ap.add_argument("--mode", choices=("objects", "dataflow", "both"),
                    default="both", help="виды рёбер (default: both)")
    ap.add_argument("--depth", type=int, default=1,
                    help="глубина обхода соседей (default: 1)")
    ap.add_argument("--max-nodes", dest="max_nodes", type=int, default=60,
                    help="лимит узлов, дальние соседи обрезаются (default: 60)")
    ap.add_argument("--out", metavar="FILE",
                    help="записать диаграмму в файл (иначе — stdout)")
    ap.add_argument("--cache-dir", metavar="DIR",
                    help="кэш индекса рёбер по mtime-хэшу дерева "
                         "(повторный прогон не читает дерево заново)")
    args = ap.parse_args(argv)
    effective = list(argv) if argv is not None else sys.argv[1:]

    if args.depth < 0 or args.max_nodes < 1:
        print("❌ --depth ≥ 0, --max-nodes ≥ 1", file=sys.stderr)
        return 2

    mdo_files: List[Path] = []
    if args.diff:
        try:
            inputs = diff_paths(args.diff)
        except RuntimeError as e:
            print(f"❌ {e}", file=sys.stderr)
            return 2
        mdo_files = [p for p in inputs if p.suffix == ".mdo"]
    else:
        if not args.paths:
            print("❌ укажите файл/каталог или --diff REF", file=sys.stderr)
            return 2
        for p in map(Path, args.paths):
            if not p.exists():
                print(f"❌ путь не найден: {p}", file=sys.stderr)
                return 2
            if p.is_file() and p.suffix == ".mdo":
                mdo_files.append(p)
            elif p.is_dir():
                mdo_files.extend(sorted(p.rglob("*.mdo")))
    if not mdo_files:
        stub = render_stub(effective)
        if args.out:
            Path(args.out).parent.mkdir(parents=True, exist_ok=True)
            Path(args.out).write_text(stub, encoding="utf-8")
            print(f"✅ {args.out}: объектов метаданных нет (заглушка)")
        else:
            print(stub, end="")
        return 0

    area_objs = [load_object(p) for p in mdo_files]
    area: Set[str] = set()
    warnings: List[str] = []
    for o in area_objs:
        tok = object_token(o)
        if tok is None:
            warnings.append(f"объект не распознан, пропущен: {o.path.name}")
        else:
            area.add(tok)
            if o.root is None:
                warnings.append(f"пропущен (XML не разобран, узел без рёбер): "
                                f"{o.path}")
    if not area:
        stub = render_stub(effective)
        print(stub, end="")
        return 0

    src_root = infer_src_root(
        area_objs, Path(args.src_root) if args.src_root else None)
    edges: List[Edge] = []
    if src_root:
        idx = EdgeIndex(Path(src_root),
                        Path(args.cache_dir) if args.cache_dir else None)
        edges = idx.edges
        if idx.broken:
            b = ", ".join(idx.broken[:5]) + ("…" if len(idx.broken) > 5 else "")
            warnings.append(f"в индексе дерева пропущены битые XML: "
                            f"{len(idx.broken)} файл(ов) ({b})")
    else:
        warnings.append("соседи недоступны: --src-root не передан — граф "
                        "только по объектам области")
        for o in area_objs:
            edges.extend(extract_edges(o))

    included, kept, dropped = build_graph(area, edges, args.mode,
                                          args.depth, args.max_nodes)
    md = render(area, included, kept, dropped, warnings, args,
                [o.path for o in area_objs], effective)
    if args.out:
        Path(args.out).parent.mkdir(parents=True, exist_ok=True)
        Path(args.out).write_text(md, encoding="utf-8")
        print(f"✅ {args.out}: узлов {len(included)}, рёбер {len(kept)}")
    else:
        print(md, end="")
    return 0


if __name__ == "__main__":
    sys.exit(main())
