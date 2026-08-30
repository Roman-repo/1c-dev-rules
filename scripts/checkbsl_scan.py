#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
checkbsl_scan.py — детерминированный regex-слой применения пакета правил
checkbsl (docs.checkbsl.org) к коду 1С (.bsl/.os): нижний слой этапа
«Код ревью» (1c-code-review) и самопрогонов разработки.

Зачем: каталог references/checkbsl/ (322 правила) применяется Ревьюером/ИИ
вручную — вероятностно, с пропусками между прогонами. Сканер детерминированно
закрывает текстовую часть пакета (~22 ключа каталога): одинаковый вход →
одинаковые findings. Правила, требующие AST и контекста конфигурации
(несуществующие элементы форм, права, метаданные, межмодульный анализ),
сканеру недоступны — их закрывает слой bsl-language-server CLI (обёртка
scripts/bsl_ls_analyze.py) и ИИ-проход по каталогу. Сканер остаётся дешёвым
предфильтром и слоем для машин без Java.

Использование:
    python3 scripts/checkbsl_scan.py ФАЙЛ|КАТАЛОГ [...]           # md-таблица findings
    python3 scripts/checkbsl_scan.py . --format json              # машиночитаемо
    python3 scripts/checkbsl_scan.py --diff main                  # только изменённые .bsl/.os
    python3 scripts/checkbsl_scan.py . --allow-number 100         # исключение для MagicNumber

Выход: 0 — 🔴 нет (🟡/🟢 допустимы, как WARN у delivery_tools), 1 — есть 🔴,
2 — ошибка использования (нет входа, файл/каталог не найдены, git недоступен).
Срабатывание — кандидат в findings: Ревьюер/ИИ отсекает ложные (например,
MagicNumber на осмысленном индексе) и переносит остальное в 05a со ссылкой
на ключ каталога и № стандарта. Без зависимостей: только стандартная библиотека.

Ограничения эвристик (сознательные): трекинг циклов считает заголовки
«… Цикл» однострочными; строки внутри запросов ищутся как текст (включая
строковые литералы); режим сервера определяется по «#Если Сервер» в файле.
"""
from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, List, Optional, Tuple

# --- правила ------------------------------------------------------------------
#
# Поля Rule:
#   key      — ключ каталога checkbsl (docs.checkbsl.org/checks/<section>/<key>/)
#   sev      — red 🔴 / yellow 🟡 / green 🟢 (приоритет — по чек-листу 1c-code-review)
#   title    — краткая формулировка для отчёта
#   kind     — line (regex по строке), comment (regex по тексту комментария),
#              magic (числовые литералы), loop (regex только внутри цикла)
#   where    — code (комментарий срезан, строки замаскированы) | raw (комментарий
#              срезан, строки целые — для запросов и поиска внутри литералов)
#   scope    — any | server (файл содержит «#Если Сервер») | form (путь Forms/Form)
#   std      — № стандарта v8std, если он установлен чек-листом/скилами; иначе ""


@dataclass(frozen=True)
class Rule:
    key: str
    sev: str
    title: str
    pattern: str = ""
    kind: str = "line"
    where: str = "code"
    scope: str = "any"
    section: str = "overall"
    std: str = ""
    nocase: bool = True  # False — регистр значим (стилевые правила вида «строчная перем»)


RULES: List[Rule] = [
    # 🔴 — блокирующие high-risk (чек-лист 1c-code-review, секции 2 и §8)
    Rule("UseQueryInALoop", "red",
         "Запрос/выгрузка выполняется в цикле",
         r"\.(Выполнить|Выгрузить)\s*\(", kind="loop", std="436"),
    Rule("VirtualTablesWithoutInnerFilter", "red",
         "Виртуальная таблица (срез/остатки/обороты) без отбора или без периода",
         r"(?<!КАК )\b(СрезПоследних|СрезПервых|Остатки|Обороты|ОстаткиОбороты)\b\s*\(\s*\)"
         r"|(?<!КАК )\b(СрезПоследних|СрезПервых)\b(?!\s*\()"
         # пустой первый параметр: «СрезПоследних(, Отбор)» — отбор есть, а
         # периода нет, ВТ вычисляется по всей истории регистра (живой тест 0.27)
         r"|(?<!КАК )\b(СрезПоследних|СрезПервых|Остатки|Обороты|ОстаткиОбороты)\s*\(\s*,",
         where="raw", section="query", std="733"),

    # 🟡 — важные
    Rule("DeprecatedMethodMessage", "yellow",
         "Устаревший метод Сообщить() — вместо него СообщитьПользователю()",
         r"\bСообщить\s*\("),
    Rule("DeprecatedThisForm", "yellow",
         "Устаревшее свойство ЭтаФорма — вместо него ЭтотОбъект",
         r"\bЭтаФорма\b"),
    Rule("DeprecatedFind", "yellow",
         "Устаревший метод Найти() — вместо него СтрНайти()",
         r"(?<![\w.])Найти\s*\("),
    Rule("DeprecatedMethodGetForm", "yellow",
         "Устаревший метод ПолучитьФорму() — вместо него ОткрытьФорму()",
         r"\bПолучитьФорму\s*\("),
    Rule("DeprecatedMethodIsInRole", "yellow",
         "Нерекомендуемый метод РольДоступна()",
         r"\bРольДоступна\s*\("),
    Rule("DeprecatedMethodFormDataToValue", "yellow",
         "Конструкция ДанныеФормыВЗначение() (модуль формы)",
         r"\bДанныеФормыВЗначение\s*\(", scope="form"),
    Rule("SynchronousMethods", "yellow",
         "Синхронный (модальный) метод",
         r"\b(Предупреждение|Вопрос|ОткрытьЗначение|ОткрытьФормуМодально|ВвестиЧисло"
         r"|ВвестиСтроку|ВвестиДату|ВвестиЗначение)\s*\(", std="703"),
    Rule("HardcodedGUID", "yellow",
         "GUID захардкожен в коде",
         r"\b[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}\b",
         where="raw"),
    Rule("HardcodedPaths", "yellow",
         "Путь захардкожен в строковом литерале",
         r"\"[^\"]*(/home/|/var/|/tmp/|/opt/|/usr/|[A-Za-z]:\\\\)",
         where="raw"),
    Rule("MagicNumber", "yellow",
         "Магическое число (вынести в именованную константу или обосновать)",
         r"\d+(?:\.\d+)?", kind="magic"),
    Rule("TempFilesDir", "yellow",
         "КаталогВременныхФайлов() — для файлов использовать ПолучитьИмяВременногоФайла()",
         r"\bКаталогВременныхФайлов\s*\("),
    Rule("OneSymbolVariable", "yellow",
         "Имя переменной из одного символа",
         r"^\s*(?:Перем|перем)\s+\w\s*[,;]|\bДля\s+(?:Каждого\s+)?\w\s+Из\b"),
    Rule("GoTo", "yellow",
         "Оператор Перейти (усложняет поток выполнения)",
         r"\bПерейти\b\s*~?\w"),
    Rule("ExecuteExport", "yellow",
         "Выполнить()/Вычислить() в серверном модуле — ограничение стандарта "
         "(динамический код; вызов метода запроса .Выполнить() не в счёт)",
         r"(?<![\w.])(?:Выполнить|Вычислить)\s*\(", scope="server"),

    # 🟢 — рекомендации
    Rule("HardcodeEmail", "green",
         "E-mail захардкожен в коде",
         r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}", where="raw"),
    Rule("TodoTagPresence", "green",
         "Комментарий с тегом TODO",
         r"\bTODO\b", kind="comment"),
    Rule("FixmeTagPresence", "green",
         "Комментарий с тегом FIXME",
         r"\bFIXME\b", kind="comment"),
    Rule("CommentedOutCodeLine", "green",
         "Похоже на закомментированный код",
         r"^\s*(Если|ИначеЕсли|Иначе|КонецЕсли|Для|Пока|КонецЦикла|Попытка|Исключение"
         r"|КонецПопытки|Процедура|Функция|КонецПроцедуры|КонецФункции|Возврат|Продолжить"
         r"|Прервать)\b|\w+\s*=[^=]", kind="comment"),
    Rule("StyleLowercaseПерем", "green",
         "Ключевое слово «перем» со строчной буквы (стиль AGENTS.md)",
         r"^[ \t]*перем(?:\s|$)", nocase=False),
    Rule("StyleSpaceBeforeParen", "green",
         "Пробел перед скобкой в Тип ( (стиль AGENTS.md)",
         r"\bТип\s+\("),
]

SEV_MARK = {"red": "🔴", "yellow": "🟡", "green": "🟢"}
SEV_RANK = {"red": 0, "yellow": 1, "green": 2}
MAGIC_WHITELIST = {"0", "1", "2", "-1", "0.5", "1.5"}

# --- разбор строки BSL ---------------------------------------------------------


def split_comment(line: str) -> Tuple[str, Optional[str]]:
    """Делит строку на (код, текст_комментария|None). «//» внутри строк не трогает."""
    in_str = False
    i, n = 0, len(line)
    while i < n:
        ch = line[i]
        if in_str:
            if ch == '"':
                if i + 1 < n and line[i + 1] == '"':  # удвоенная кавычка
                    i += 2
                    continue
                in_str = False
        else:
            if ch == '"':
                in_str = True
            elif ch == "/" and i + 1 < n and line[i + 1] == "/":
                return line[:i], line[i + 2:]
        i += 1
    return line, None


def mask_strings(code: str) -> str:
    """Заменяет содержимое строковых литералов на точки (длину сохраняем)."""
    out = []
    in_str = False
    i, n = 0, len(code)
    while i < n:
        ch = code[i]
        if in_str:
            if ch == '"':
                if i + 1 < n and code[i + 1] == '"':  # удвоенная кавычка
                    out.append("..")
                    i += 2
                    continue
                in_str = False
                out.append('"')
            else:
                out.append(".")
        else:
            out.append(ch)
            if ch == '"':
                in_str = True
        i += 1
    return "".join(out)


def read_text(path: Path) -> str:
    """UTF-8 (с BOM), падение на cp1251 (выгрузки конфигуратора), затем замена."""
    raw = path.read_bytes()
    for enc in ("utf-8-sig", "cp1251"):
        try:
            return raw.decode(enc)
        except UnicodeDecodeError:
            continue
    return raw.decode("utf-8", errors="replace")


# --- сканер --------------------------------------------------------------------


@dataclass
class Finding:
    key: str
    sev: str
    title: str
    std: str
    file: str
    line: int
    fragment: str
    section: str

    def as_dict(self) -> dict:
        return {
            "key": self.key, "severity": self.sev, "title": self.title,
            "std": self.std, "file": self.file, "line": self.line,
            "fragment": self.fragment,
            "catalog": f"https://docs.checkbsl.org/checks/{self.section}/{self.key}/",
        }


def _compiled(rule: Rule) -> "re.Pattern[str]":
    flags = re.IGNORECASE if rule.nocase else 0
    return re.compile(rule.pattern, flags)


def scan_text(text: str, file: str, allow_numbers: Iterable[str] = ()) -> List[Finding]:
    """Сканирует один модуль. file — отображаемое имя (для scope-правил важен путь)."""
    is_server = "#Если Сервер" in text
    posix = file.replace("\\", "/")
    is_form = any(part in ("Forms", "Form") for part in posix.split("/"))
    allowed = MAGIC_WHITELIST | set(allow_numbers)

    findings: List[Finding] = []
    loop_depth = 0
    for lineno, line in enumerate(text.splitlines(), start=1):
        code, comment = split_comment(line)
        code_masked = mask_strings(code)

        # трекинг циклов: «… Цикл» открывает, КонецЦикла закрывает
        if re.search(r"\bКонецЦикла\b", code_masked):
            loop_depth -= 1

        for rule in RULES:
            if rule.scope == "server" and not is_server:
                continue
            if rule.scope == "form" and not is_form:
                continue
            subject = None
            if rule.kind in ("line", "magic"):
                subject = code_masked if rule.where == "code" else code
            elif rule.kind == "loop":
                if loop_depth > 0:
                    subject = code_masked if rule.where == "code" else code
            elif rule.kind == "comment":
                subject = comment
            if subject is None:
                continue
            rx = _compiled(rule)
            if rule.kind == "magic":
                for m in rx.finditer(subject):
                    if m.group(0) not in allowed:
                        findings.append(Finding(rule.key, rule.sev, rule.title,
                                                rule.std, file, lineno,
                                                line.strip()[:80], rule.section))
                        break  # одно замечание на строку
            elif rx.search(subject):
                findings.append(Finding(rule.key, rule.sev, rule.title, rule.std,
                                        file, lineno, line.strip()[:80], rule.section))

        if re.search(r"\bЦикл\s*$", code_masked):
            loop_depth += 1

    return findings


def scan_paths(paths: List[Path], allow_numbers: Iterable[str] = ()) -> Tuple[List[Finding], int]:
    """Сканирует файлы/каталоги (.bsl/.os). Возвращает (findings, число файлов)."""
    files: List[Path] = []
    for p in paths:
        if p.is_dir():
            files.extend(sorted(q for q in p.rglob("*") if q.suffix.lower() in (".bsl", ".os")))
        elif p.is_file():
            files.append(p)
        else:
            raise FileNotFoundError(str(p))
    findings: List[Finding] = []
    for f in files:
        findings.extend(scan_text(read_text(f), str(f), allow_numbers))
    findings.sort(key=lambda x: (SEV_RANK[x.sev], x.file, x.line))
    return findings, len(files)


def diff_files(ref: str, cwd: Optional[Path] = None) -> List[Path]:
    """Изменённые относительно ref файлы .bsl/.os в git-репо каталога cwd.

    Новые незакоммиченные файлы должны быть в индексе (git add) — untracked
    файлы git diff не показывает."""
    root = subprocess.run(["git", "rev-parse", "--show-toplevel"],
                          capture_output=True, text=True, cwd=cwd)
    if root.returncode != 0:
        raise RuntimeError("не в git-репозитории — git rev-parse не выполнен")
    repo = Path(root.stdout.strip())
    res = subprocess.run(
        ["git", "-C", str(repo), "diff", "--name-only", "--diff-filter=ACMR", ref],
        capture_output=True, text=True)
    if res.returncode != 0:
        raise RuntimeError(f"git diff {ref}: {res.stderr.strip()}")
    out = []
    for rel in res.stdout.splitlines():
        p = repo / rel
        if p.suffix.lower() in (".bsl", ".os") and p.exists():
            out.append(p)
    return out


# --- вывод ---------------------------------------------------------------------


def format_md(findings: List[Finding], files_scanned: int) -> str:
    counts = {s: sum(1 for f in findings if f.sev == s) for s in ("red", "yellow", "green")}
    head = (f"# checkbsl_scan — {len(findings)} замечаний "
            f"(🔴 {counts['red']}, 🟡 {counts['yellow']}, 🟢 {counts['green']}); "
            f"файлов: {files_scanned}\n")
    if not findings:
        return head + "\nЧисто: детерминированный слой нарушений не нашёл " \
               "(остальные правила пакета — за ручным проходом по каталогу).\n"
    rows = ["\n| # | Серьёзность | Ключ | № ст. | Файл:строка | Фрагмент |",
            "|---|---|---|---|---|---|"]
    for i, f in enumerate(findings, start=1):
        std = f.std or "—"
        rows.append(f"| {i} | {SEV_MARK[f.sev]} | `{f.key}` | {std} | "
                    f"{f.file}:{f.line} | `{f.fragment}` |")
    rows.append("\nКарточки правил: docs.checkbsl.org/checks/<section>/<Ключ>/ "
                "(соответствующая ссылка — в json-выводе).")
    return head + "\n".join(rows) + "\n"


def format_json(findings: List[Finding], files_scanned: int) -> str:
    return json.dumps(
        {"files": files_scanned,
         "counts": {s: sum(1 for f in findings if f.sev == s)
                    for s in ("red", "yellow", "green")},
         "findings": [f.as_dict() for f in findings]},
        ensure_ascii=False, indent=2)


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(
        description="Детерминированный regex-слой пакета checkbsl для кода 1С (.bsl/.os)")
    ap.add_argument("paths", nargs="*", help="файлы или каталоги с кодом 1С")
    ap.add_argument("--diff", metavar="REF",
                    help="сканировать только изменённые относительно REF .bsl/.os (git)")
    ap.add_argument("--format", choices=("md", "json"), default="md")
    ap.add_argument("--allow-number", action="append", default=[],
                    metavar="N", help="исключить число N из MagicNumber (повторяемый)")
    args = ap.parse_args(argv)

    try:
        if args.diff:
            files = diff_files(args.diff)
            if not files:
                paths: List[Path] = []
            else:
                paths = files
        else:
            paths = [Path(p) for p in args.paths]
        if not args.diff and not paths:
            ap.error("укажите файл/каталог или --diff REF")
        findings, nfiles = scan_paths(paths, args.allow_number) if paths else ([], 0)
    except (FileNotFoundError, RuntimeError) as e:
        print(f"❌ {e}", file=sys.stderr)
        return 2

    print(format_json(findings, nfiles) if args.format == "json"
          else format_md(findings, nfiles))
    return 1 if any(f.sev == "red" for f in findings) else 0


if __name__ == "__main__":
    sys.exit(main())
