#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
bsl_ls_analyze.py — ядро полноты пакета checkbsl: bsl-language-server CLI
в режиме analyze, средний слой этапа «Код ревью» (1c-code-review) между
regex-сканером checkbsl_scan.py и ручным проходом Ревьюера по каталогу.

Зачем: сканер (слой 1) детерминированно закрывает ~22 текстовых ключа каталога,
ИИ-проход по каталогу вероятностен (~60–70% номинально). BSL LS даёт ~85–95%
детерминированно (AST: запросы, транзакции, устаревшие методы, сложность,
области, метаданные). Слои: сканер → BSL LS (этот скрипт) → чек-лист/каталог.
На машинах без Java остаётся слой 1.

Ключи BSL LS и каталога checkbsl — разные системы имён (совпадают напрямую
только ~23): соответствие даёт таблица ALIAS ниже (сверена по формулировкам
обеих документаций), остальное — прямые совпадения. Ключ без каталога
показывается со ссылкой на документацию BSL LS.

Установка (один раз):
    brew install openjdk                    # macOS; нужна Java 11+
    curl -L -o ~/.local/share/1c-dev-rules/bsl-language-server.jar \
      https://github.com/1c-syntax/bsl-language-server/releases/latest/download/bsl-language-server-exec.jar
Поиск java: $BSL_LS_JAVA, PATH, /opt/homebrew/opt/openjdk (brew, keg-only);
jar: $BSL_LS_JAR, .tools/ репо, ~/.local/share/1c-dev-rules/.

Использование (из каталога целевого проекта для --diff):
    python3 scripts/bsl_ls_analyze.py <файлы/каталог>            # md-таблица
    python3 scripts/bsl_ls_analyze.py --diff main               # дифф: файлы и строки
    python3 scripts/bsl_ls_analyze.py . --format json           # машиночитаемо
    python3 scripts/bsl_ls_analyze.py --diff main --src-root Project/Toir/src

srcDir для BSL LS по умолчанию — общий родитель входов; --src-root задаёт
корень исходников конфигурации (EDT src с Configuration.mdo) — анализ полнее,
findings фильтруются по входным файлам.

Стоимость петли на больших конфигурациях: BSL LS разбирает всё дерево srcDir
на каждой итерации «правка → прогон» (это требование AST-анализа — контекст
метаданных и межмодульных вызовов). Ускорение: --slim-config (анализ только
диагностик каталога∪ALIAS), --cache-dir (повтор без правок — мгновенно),
тюнинг --timeout / --no-line-filter (см. --help).

Выход: 0 — 🔴 нет, 1 — есть 🔴, 2 — ошибка, 3 — слой недоступен (нет Java/jar —
работайте слоем 1: scripts/checkbsl_scan.py). Срабатывание — кандидат в
findings 05a: серьёзность — чек-лист сканера → важность BSL LS; решение за
Ревьюером.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import subprocess
import sys
import tempfile
from functools import lru_cache
from pathlib import Path
from shutil import which
from typing import Dict, List, Optional, Set, Tuple
from urllib.parse import unquote

sys.path.insert(0, str(Path(__file__).resolve().parent))
import checkbsl_scan as scan  # noqa: E402  (общий слой: Finding, RULES, diff_files, read_text)

REPO_ROOT = Path(__file__).resolve().parent.parent
CATALOG_DIR = REPO_ROOT / "skills" / "1c-code-review" / "references" / "checkbsl"
LS_TABLE = Path(__file__).resolve().parent / "bsl_ls_diagnostics.json"
FIXES = Path(__file__).resolve().parent / "bsl_ls_fixes.json"

# Ключ BSL LS → ключ каталога checkbsl (разные системы имён). Сверено по
# формулировкам docs.checkbsl.org ↔ 1c-syntax.github.io/bsl-language-server;
# сомнительные пары сюда НЕ входят (ключ остаётся со ссылкой на доки BSL LS).
ALIAS: Dict[str, str] = {
    # high-risk чек-листа §8 (запрос в цикле, ВТ без отбора, транзакции)
    "CreateQueryInCycle": "UseQueryInALoop",
    "VirtualTableCallWithoutParameters": "VirtualTablesWithoutInnerFilter",
    "BeginTransactionBeforeTryCatch": "BeginTransactionInTryBlock",
    "CommitTransactionOutsideTryCatch": "CommitTransactionOutsideTry",
    "MissingCodeTryCatchEx": "TransactionWithoutExceptCode",
    "PairingBrokenTransaction": "PairBeginCommitTransactionCall",
    "WrongUseOfRollbackTransactionMethod": "WrongUsageOfRollbackTransactionMethod",
    "DataExchangeLoading": "DataExchangeLoad",
    # устаревшие/запретные методы
    "DeprecatedMessage": "DeprecatedMethodMessage",
    "UsingThisForm": "DeprecatedThisForm",
    "GetFormMethod": "DeprecatedMethodGetForm",
    "IsInRoleMethod": "DeprecatedMethodIsInRole",
    "FormDataToValue": "DeprecatedMethodFormDataToValue",
    "DeprecatedTypeManagedForm": "DeprecatedManagedForm",
    "DeprecatedCurrentDate": "ForbiddenMethodCurrentDate",
    "UsingSynchronousCalls": "SynchronousMethods",
    "UsingCancelParameter": "CancelUse",
    "UsingGoto": "GoTo",
    "OSUsersMethod": "UseUsersOS",
    # хардкод
    "UsingHardcodePath": "HardcodedPaths",
    "UsingHardcodeNetworkAddress": "HardcodeIpAddress",
    # структура модуля и модулей
    "CodeOutOfRegion": "CodeOutsideRegion",
    "ConsecutiveEmptyLines": "ConsecutiveBlankLines",
    "CommonModuleNameFullAccess": "CommonModuleNamePrivileged",
    "CommonModuleNameCached": "CommonModuleNameReuseValue",
    "CommonModuleNameWords": "CommonModuleInvalidName",
    "NonExportMethodsInApiRegion": "NonExportMethodInInterfaceRegion",
    "CommandModuleExportMethods": "ExportMethodInCommandModule",
    "ExportVariables": "ExportVariable",
    "PublicMethodsDescription": "UndocumentedPublicApi",
    "MissingVariablesDescription": "VariableWithoutDescription",
    "MissingParameterDescription": "DocumentationParameters",
    "MissingReturnedValueDescription": "DocumentationReturnValue",
    "OrderOfParams": "SequenceArguments",
    "ReservedParameterNames": "UsageOfReservedWordParams",
    "NumberOfParams": "QuantityArguments",
    "NumberOfOptionalParams": "QuantityOptionalArguments",
    # объявления, сложности, размеры
    "CyclomaticComplexity": "MethodCyclomaticComplexity",
    "CognitiveComplexity": "MethodCognitiveComplexity",
    "NestedStatements": "NestedControlFlowDepth",
    "FunctionShouldHaveReturn": "FunctionReturn",
    "AllFunctionPathMustHaveReturn": "FunctionReturn",
    "ProcedureReturnsValue": "ProcedureAsFunction",
    "FunctionReturnsSamePrimitive": "EqualReturns",
    "FunctionNameStartsWithGet": "FunctionNameStartsWithVerb",
    "TooManyReturns": "ExcessiveReturns",
    "NestedTernaryOperator": "NestedTernary",
    "IfConditionComplexity": "BulkyConditions",
    "IfElseDuplicatedCondition": "EqualConditions",
    "CompareWithBoolean": "BooleanLiteral",
    "UnusedLocalVariable": "UnusedVariable",
    "UnusedLocalMethod": "UnusedMethod",
    "UnusedParameters": "UnusedParameter",
    "RewriteMethodParameter": "RewritingMethodParameters",
    "SelfAssign": "SelfAssignment",
    "TryNumber": "CastInTry",
    "AssignToReadOnlyProperty": "ReadOnlyProperty",
    "CanonicalSpellingKeywords": "KeyWordNotCanonical",
    "SpaceAtStartComment": "SpaceAfterCommentSymbols",
    "ExcessiveAutoTestCheck": "AutoTestUsage",
    "DeletingCollectionItem": "IllegalDeletionFromCollection",
    "NestedConstructorsInStructureDeclaration": "ConstructorIntoStructureConstructor",
    "NumberOfValuesInStructureConstructor": "StructureConstructorParameters",
    "NestedFunctionInParameters": "NestedFunctionCalls",
    "DuplicateStringLiteral": "DuplicatedStringConstants",
    "EmptyCodeBlock": "EmptyBlock",
    "UnaryPlusInConcatenation": "StringUnaryExpr",
    "StyleElementConstructors": "StyleConstructors",
    "MissingTemporaryFileDeletion": "DeletingTempFile",
    "InvalidCharacterInFile": "BadSymbol",
    "LatinAndCyrillicSymbolInWord": "LatinC",
    "UsingObjectNotAvailableUnix": "UsingNotCrossPlatformObjects",
    "UsingFindElementByString": "FindByDescriptionStatement",
    "UnsafeFindByCode": "FindByCodeStatement",
    "MissedRequiredParameter": "UseRequiredParameters",
    "QueryToMissingMetadata": "VerifyMetadataInQuery",
    "JoinWithSubQuery": "SubqueryJoin",
    "JoinWithVirtualTable": "SubqueryJoin",
    "FullOuterJoinQuery": "UsingFullOuterJoin",
    "LogicalOrInJoinQuerySection": "UsingLogicalOrInOn",
    "LogicalOrInTheWhereSectionOfQuery": "UsingLogicalOrInWhere",
    "AssignAliasFieldsInQuery": "FieldMustHaveAlias",
    "ExternalAppStarting": "CallingExternalApplication",
    "TimeoutsInExternalResources": "ExternalResourceTimeout",
    "UsageWriteLogEvent": "WriteLogEventWrongUsage",
    "SeveralCompilerDirectives": "SeveralCompilationDirectives",
}

# Важность BSL LS (индекс диагностик) → серьёзность findings, когда ключ не
# покрыт чек-листом сканера и каталогом. Приоритет всегда за чек-листом.
IMPORTANCE_SEV = {"Блокирующий": "red", "Критичный": "red",
                  "Важный": "yellow", "Информационный": "green",
                  "Незначительный": "green"}

RULE_SEV = {r.key: (r.sev, r.std) for r in scan.RULES}


# --- справочники -----------------------------------------------------------------


@lru_cache(maxsize=1)
def load_catalog() -> Dict[str, Tuple[str, str, str]]:
    """Ключ каталога checkbsl → (название, №№ стандартов, секция) из references/checkbsl/."""
    out: Dict[str, Tuple[str, str, str]] = {}
    for md in sorted(CATALOG_DIR.glob("*.md")):
        for line in md.read_text(encoding="utf-8").splitlines():
            if not line.startswith("| `"):
                continue
            cells = [c.strip() for c in line.strip().strip("|").split("|")]
            if len(cells) < 6 or not (cells[0].startswith("`") and cells[0].endswith("`")):
                continue
            key = cells[0].strip("`")
            std = ",".join(re.findall(r"№(\d+)", cells[3]))
            m = re.search(r"/checks/(\w+)/", cells[5])
            out[key] = (cells[1], std, m.group(1) if m else "overall")
    return out


@lru_cache(maxsize=1)
def load_ls_table() -> Dict[str, dict]:
    """Имя диагностики BSL LS → {title, importance, ...} из bsl_ls_diagnostics.json."""
    data = json.loads(LS_TABLE.read_text(encoding="utf-8"))
    return data.get("diagnostics", {})


@lru_cache(maxsize=1)
def load_fixes() -> Dict[str, dict]:
    """Ключ каталога → {why, good}: «что не так» и пример «как правильно»
    для отчёта ревью. Пополняется по находкам живых ревью."""
    data = json.loads(FIXES.read_text(encoding="utf-8"))
    return {k: v for k, v in data.items() if not k.startswith("_")}


# --- поиск java и jar -------------------------------------------------------------


def find_java(explicit: Optional[str] = None) -> Optional[Path]:
    def alive(c: Path) -> bool:
        # /usr/bin/java на macOS — заглушка: существует, но без JVM падает
        if not c.is_file():
            return False
        return subprocess.run([str(c), "-version"], capture_output=True,
                              timeout=30).returncode == 0

    if explicit:  # явный путь авторитетен: невалидный — не подменяем найденным
        return Path(explicit) if alive(Path(explicit)) else None
    cands: List[Path] = []
    if os.environ.get("BSL_LS_JAVA"):
        cands.append(Path(os.environ["BSL_LS_JAVA"]))
    w = which("java")
    if w:
        cands.append(Path(w))
    # brew openjdk — keg-only: в PATH не попадает, но это самый частый способ установки
    cands.append(Path("/opt/homebrew/opt/openjdk/bin/java"))
    for c in cands:
        if alive(c):
            return c
    return None


def find_jar(explicit: Optional[str] = None) -> Optional[Path]:
    if explicit:  # см. find_java: явный путь авторитетен
        p = Path(explicit)
        return p if p.is_file() else None
    cands: List[Path] = []
    if os.environ.get("BSL_LS_JAR"):
        cands.append(Path(os.environ["BSL_LS_JAR"]))
    cands.extend(sorted((REPO_ROOT / ".tools").glob("bsl-language-server*.jar")))
    cands.extend(sorted((Path.home() / ".local" / "share" / "1c-dev-rules")
                        .glob("bsl-language-server*.jar")))
    for c in cands:
        if c.is_file():
            return c
    return None


# --- ускорение петли: узкий конфиг и кэш отчёта -------------------------------------

CACHE_VERSION = 1  # бамп при смене логики формирования/разбора отчёта


def slim_config(path: Path) -> Path:
    """Временный конфиг BSL LS: диагностики вне каталога ∪ ALIAS отключены.

    Обёртка всё равно маппит на каталог checkbsl: диагностики без покрытия
    получают дефолтную 🟡 и ссылку на доки BSL LS, но не № стандарта и не
    запись в базе fixes. Отключение их на старте ускоряет анализ больших
    конфигураций и убирает шум из отчёта петли (флаг --slim-config).
    """
    covered = set(load_catalog()) | set(ALIAS)
    off = {name: False for name in load_ls_table() if name not in covered}
    path.write_text(json.dumps({"diagnostics": off}, ensure_ascii=False),
                    encoding="utf-8")
    return path


def cache_key(src_root: Path, jar: Path, config: Optional[Path]) -> str:
    """Хэш входов анализа: дерево src_root (путь/размер/mtime), jar, конфиг.

    mtime+size вместо содержимого: на 2–3 тыс. модулей хэширование файлов
    занимало бы заметную долю выигрыша; коллизия mtime+size практически
    невозможна в петле «правка → прогон» (правка меняет mtime).
    """
    h = hashlib.sha256()
    h.update(f"v{CACHE_VERSION}".encode())
    st = jar.stat()
    h.update(f"{jar.name}:{st.st_size}:{st.st_mtime_ns}".encode())
    h.update(config.read_bytes() if config else b"<no-config>")
    for dirpath, dirnames, filenames in os.walk(src_root):
        dirnames[:] = sorted(d for d in dirnames if d not in (".git", "node_modules"))
        for fn in sorted(filenames):
            p = Path(dirpath) / fn
            try:
                s = p.stat()
            except OSError:
                continue
            h.update(f"{p.relative_to(src_root)}:{s.st_size}:{s.st_mtime_ns}\n"
                     .encode())
    return h.hexdigest()


# --- запуск CLI и разбор отчёта ----------------------------------------------------


def run_bsl_ls(java: Path, jar: Path, src_root: Path, config: Optional[Path],
               timeout: int) -> dict:
    """Запускает analyze, возвращает разобранный bsl-json.json.

    cwd = каталогу вывода: часть репортеров пишет отчёт в рабочую директорию,
    а не в --outputDir (наблюдено на v1.0.7).
    """
    with tempfile.TemporaryDirectory(prefix="bsl_ls_") as tmp:
        out = Path(tmp)
        cmd = [str(java), "-jar", str(jar), "--analyze", "--srcDir", str(src_root),
               "--reporter", "json", "--outputDir", str(out), "--silent"]
        if config:
            cmd += ["--configuration", str(config)]
        try:
            proc = subprocess.run(cmd, cwd=out, capture_output=True, text=True,
                                  timeout=timeout)
        except subprocess.TimeoutExpired:
            raise RuntimeError(f"bsl-language-server не завершился за {timeout} с "
                               f"(увеличьте --timeout или сузьте srcDir)")
        report = out / "bsl-json.json"
        if not report.is_file():
            noise = [l for l in (proc.stderr or proc.stdout or "").splitlines()
                     if l.strip() and not l.startswith(("WARNING", "INFO"))]
            tail = noise[-5:]
            raise RuntimeError("bsl-language-server не создал отчёт (exit "
                               f"{proc.returncode}): " + " | ".join(tail))
        data = json.loads(report.read_text(encoding="utf-8"))
        # пути в отчёте: процентно-кодированы (кириллица) и бывают относительными —
        # якорь относительных: cwd java-процесса (= out); если там файла нет —
        # src_root (так пути ведут на реальные исходники, а не в удаляемый tmp —
        # это же делает безопасным кэш отчёта --cache-dir между прогонами)
        for fi in data.get("fileinfos", []):
            p = unquote(fi.get("path", "")).removeprefix("file://")
            if not p:
                continue
            if p.startswith("/"):
                fi["path"] = p
            else:
                cand = (out / p).resolve()
                fi["path"] = str(cand) if cand.exists() \
                    else str((src_root / p).resolve())
        return data


def resolve_key(code: str) -> str:
    """Ключ каталога для кода диагностики (алиас → прямое совпадение → сам код)."""
    catalog = load_catalog()
    if code in catalog:
        return code
    return ALIAS.get(code, code)


def sev_std(key: str, ls_code: str) -> Tuple[str, str]:
    """Серьёзность и № стандарта: чек-лист сканера → каталог → важность BSL LS."""
    if key in RULE_SEV:
        sev, std = RULE_SEV[key]
        return sev, std
    catalog = load_catalog()
    if key in catalog and catalog[key][1]:
        sev = IMPORTANCE_SEV.get(load_ls_table().get(ls_code, {}).get("importance", ""),
                                 "yellow")
        return sev, catalog[key][1]
    sev = IMPORTANCE_SEV.get(load_ls_table().get(ls_code, {}).get("importance", ""),
                             "yellow")
    return sev, ""


def display_path(p: Path) -> str:
    """Путь относительно cwd процесса, если он под ним, иначе абсолютный."""
    try:
        return str(p.relative_to(Path.cwd()))
    except ValueError:
        return str(p)


def parse_report(report: dict, targets: Optional[Set[Path]],
                 line_ranges: Optional[Dict[Path, List[Tuple[int, int]]]] = None
                 ) -> Tuple[List[scan.Finding], int, List[dict]]:
    """Диагностики отчёта → findings; фильтры по файлам (targets) и строкам.

    Возвращает (findings, число разобранных файлов, расширенные словари для json).
    """
    catalog = load_catalog()
    ls_table = load_ls_table()
    findings: List[scan.Finding] = []
    extra: List[dict] = []
    src_lines: Dict[Path, List[str]] = {}
    for fi in report.get("fileinfos", []):
        path = Path(unquote(fi.get("path", "")).removeprefix("file://"))
        try:
            resolved = path.resolve()
        except OSError:
            resolved = path
        if targets is not None and resolved not in targets:
            continue
        ranges = line_ranges.get(resolved) if line_ranges is not None else None
        if resolved not in src_lines:
            try:
                src_lines[resolved] = scan.read_text(resolved).splitlines()
            except OSError:
                src_lines[resolved] = []
        lines = src_lines[resolved]
        for d in fi.get("diagnostics") or []:
            line = (d.get("range", {}).get("start", {}).get("line", 0)) + 1  # LSP: с нуля
            if ranges is not None and not any(a <= line <= b for a, b in ranges):
                continue
            code = d.get("code") or "?"
            if code.endswith("Diagnostic"):  # суффикс в старых версиях отчёта
                code = code[:-len("Diagnostic")]
            key = resolve_key(code)
            in_catalog = key in catalog
            title, std, section = catalog.get(key, ("", "", "overall"))
            if not in_catalog:
                title = ls_table.get(code, {}).get("title") or d.get("message", "")[:80]
            sev, std2 = sev_std(key, code)
            std = std2 or std
            fragment = lines[line - 1].strip()[:80] if 0 < line <= len(lines) \
                else (d.get("message", "")[:80])
            fragment = fragment.replace("|", "/")
            docs = (f"https://docs.checkbsl.org/checks/{section}/{key}/" if in_catalog
                    else (d.get("codeDescription") or {}).get("href", ""))
            findings.append(scan.Finding(key, sev, title, std, display_path(resolved),
                                         line, fragment, section))
            extra.append({"docs": docs, "message": d.get("message", ""),
                          "ls_code": code, "ls_severity": d.get("severity", "")})
    # сортируем пары, а не findings отдельно: extra (ls_code/message) должен
    # оставаться выровнен по индексам
    pairs = sorted(zip(findings, extra),
                   key=lambda p: (scan.SEV_RANK[p[0].sev], p[0].file, p[0].line))
    return ([p[0] for p in pairs], len(report.get("fileinfos", [])),
            [p[1] for p in pairs])


# --- дифф-фильтрация ----------------------------------------------------------------


def added_line_ranges(ref: str, files: List[Path]) -> Dict[Path, List[Tuple[int, int]]]:
    """Добавленные строки каждого файла относительно ref: git diff -U0 → диапазоны.

    Корень репо определяется по первому файлу (не по cwd вызова).
    """
    out: Dict[Path, List[Tuple[int, int]]] = {}
    if not files:
        return out
    root = subprocess.run(["git", "-C", str(files[0].parent), "rev-parse",
                           "--show-toplevel"], capture_output=True, text=True)
    if root.returncode != 0:
        raise RuntimeError("не в git-репозитории — git rev-parse не выполнен")
    repo = Path(root.stdout.strip())
    hunk_rx = re.compile(r"^@@ -\d+(?:,\d+)? \+(\d+)(?:,(\d+))? @@", re.M)
    for f in files:
        res = subprocess.run(["git", "-C", str(repo), "diff", "-U0", ref,
                              "--", str(f.resolve().relative_to(repo))],
                             capture_output=True, text=True)
        if res.returncode != 0:
            raise RuntimeError(f"git diff {ref} -- {f.name}: {res.stderr.strip()}")
        ranges = []
        for m in hunk_rx.finditer(res.stdout):
            start = int(m.group(1))
            count = int(m.group(2)) if m.group(2) is not None else 1
            if count:
                ranges.append((start, start + count - 1))
        out[f.resolve()] = ranges
    return out


# --- отчёт ревью (--report) ----------------------------------------------------------


def snippet(file: str, line: int, context: int = 2) -> str:
    """Код с замечанием: строки вокруг (номера + маркер на строке замечания)."""
    try:
        path = Path(file)
        if not path.is_absolute():
            path = Path.cwd() / path
        lines = scan.read_text(path).splitlines()
    except OSError:
        return "(источник недоступен)"
    lo, hi = max(1, line - context), min(len(lines), line + context)
    out = []
    for n in range(lo, hi + 1):
        mark = "  ← замечание" if n == line else ""
        out.append(f"{n:4d} | {lines[n - 1].rstrip()[:110]}{mark}")
    return "\n".join(out)


def build_report(findings: List[scan.Finding], extra: List[dict], nfiles: int,
                 meta: dict) -> str:
    """Md-отчёт по замечаниям: код + что не так + как правильно + вердикт петли.

    Хранится в каталоге задачи (docs/delivery/<TASK>/code-review/); следующая
    итерация петли «правка → повторный прогон» пишет новый файл r<N+1>.
    """
    from datetime import datetime

    fixes = load_fixes()
    counts = {s: sum(1 for f in findings if f.sev == s)
              for s in ("red", "yellow", "green")}
    lines: List[str] = [
        "# Отчёт код-ревью — BSL LS (`bsl_ls_analyze.py`)",
        "",
        f"- Дата: {datetime.now().strftime('%Y-%m-%d %H:%M')}",
        f"- Слой: bsl-language-server (AST), файлов разобрано: {nfiles}",
        f"- Вход: {meta.get('inputs', '—')}"
        + (f"; дифф: `{meta.get('diff')}` (только изменённые строки)"
           if meta.get("diff") else ""),
        f"- Замечания: **{len(findings)}** — 🔴 {counts['red']},"
        f" 🟡 {counts['yellow']}, 🟢 {counts['green']}",
    ]
    if counts["red"]:
        lines.append(f"- **Вердикт: 🔴 есть — возврат на этап «Разработка»,"
                     f" правка, повторный прогон (следующий отчёт r{int(meta.get('round', 0)) + 1}).**")
    elif findings:
        lines.append("- Вердикт: 🔴 нет — 🟡/🟢 на решение Ревьюера (05a).")
    else:
        lines.append("- Вердикт: чисто — петля закрыта, код на ревью Ревьюеру.")

    section_titles = {"red": "🔴 Блокирующие (исправить до повторного прогона)",
                      "yellow": "🟡 Важные (править или обосновать в 05a)",
                      "green": "🟢 Рекомендации (на усмотрение автора)"}
    n = 0
    for sev in ("red", "yellow", "green"):
        group = [(f, e) for f, e in zip(findings, extra) if f.sev == sev]
        if not group:
            continue
        lines += ["", f"## {section_titles[sev]}", ""]
        for f, e in group:
            n += 1
            std = f" (№{f.std})" if f.std else ""
            fix = fixes.get(f.key)
            lines += [
                f"### {n}. `{f.key}` — {f.title}{std}",
                "",
                f"**Файл:** `{f.file}:{f.line}` · диагностика BSL LS:"
                f" `{e['ls_code']}` · [карточка]({e['docs']})",
                "",
                "```bsl",
                snippet(f.file, f.line),
                "```",
                "",
                f"**Что не так:** {fix['why'] if fix else e['message']}",
                "",
            ]
            if fix:
                lines += ["**Как правильно:**", "", "```bsl", fix["good"], "```", ""]
            else:
                lines += [f"**Как правильно:** пример — по карточке правила"
                          f" ({e['docs']}).", ""]
    lines += ["---", "",
              "Карточки: docs.checkbsl.org/checks/<section>/<Ключ>/; ключи без"
              " каталога — 1c-syntax.github.io/bsl-language-server/diagnostics/<Код>/.",
              "Срабатывания — кандидаты в findings 05a, решение за Ревьюером."]
    return "\n".join(lines) + "\n"


# --- вывод ----------------------------------------------------------------------------


def format_md(findings: List[scan.Finding], files: int) -> str:
    counts = {s: sum(1 for f in findings if f.sev == s)
              for s in ("red", "yellow", "green")}
    head = (f"# bsl_ls_analyze — {len(findings)} замечаний "
            f"(🔴 {counts['red']}, 🟡 {counts['yellow']}, 🟢 {counts['green']}); "
            f"файлов разобрано: {files}\n\n"
            "Слой: bsl-language-server (детерминированный AST-анализ). "
            "Срабатывания — кандидаты в findings 05a, решение за Ревьюером.\n")
    if not findings:
        return head + "\nЧисто: слой BSL LS нарушений не нашёл (правила, требующие " \
               "контекста задачи, — за чек-листом и каталогом).\n"
    rows = ["\n| # | Серьёзность | Ключ | № ст. | Файл:строка | Фрагмент |",
            "|---|---|---|---|---|---|"]
    for i, f in enumerate(findings, start=1):
        rows.append(f"| {i} | {scan.SEV_MARK[f.sev]} | `{f.key}` | {f.std or '—'} | "
                    f"{f.file}:{f.line} | `{f.fragment}` |")
    rows.append("\nКарточки: docs.checkbsl.org/checks/<section>/<Ключ>/; ключи без "
                "каталога — 1c-syntax.github.io/bsl-language-server/diagnostics/<Ключ>/ "
                "(ссылки — в json-выводе).")
    return head + "\n".join(rows) + "\n"


def format_json(findings: List[scan.Finding], files: int, extra: List[dict]) -> str:
    counts = {s: sum(1 for f in findings if f.sev == s)
              for s in ("red", "yellow", "green")}
    items = []
    for f, e in zip(findings, extra):
        items.append({"key": f.key, "severity": f.sev, "title": f.title,
                      "std": f.std, "file": f.file, "line": f.line,
                      "fragment": f.fragment, "message": e["message"],
                      "ls_code": e["ls_code"], "ls_severity": e["ls_severity"],
                      "docs": e["docs"]})
    return json.dumps({"files": files, "counts": counts, "findings": items},
                      ensure_ascii=False, indent=2)


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(
        description="Средний слой checkbsl: анализ bsl-language-server CLI "
                    "с маппингом на каталог и дифф-фильтрацией")
    ap.add_argument("paths", nargs="*", help="файлы или каталоги с кодом 1С")
    ap.add_argument("--diff", metavar="REF",
                    help="анализировать только изменённые относительно REF .bsl/.os (git)")
    ap.add_argument("--format", choices=("md", "json"), default="md")
    ap.add_argument("--src-root", metavar="DIR",
                    help="корень исходников конфигурации для BSL LS (дефолт: общий родитель входов)")
    ap.add_argument("--config", metavar="FILE",
                    help="конфиг bsl-language-server.json (дефолт: <src-root>/.bsl-language-server.json)")
    ap.add_argument("--java", help="путь к java (дефолт: $BSL_LS_JAVA, PATH, brew)")
    ap.add_argument("--jar", help="путь к jar BSL LS (дефолт: $BSL_LS_JAR, .tools/, ~/.local/share)")
    ap.add_argument("--timeout", type=int, default=900, help="таймаут анализа, сек (дефолт 900; "
                    "на больших конфигурациях при нехватке — увеличить или сузить srcDir)")
    ap.add_argument("--no-line-filter", action="store_true",
                    help="в --diff фильтровать только по файлам, не по строкам "
                         "(шире: находки на соседних строках тоже попадут в отчёт)")
    ap.add_argument("--slim-config", action="store_true",
                    help="временный конфиг BSL LS: диагностики вне каталога∪ALIAS "
                         "отключены — анализ быстрее, шум 🟡 без каталога уходит; "
                         "игнорируется, если задан --config или есть "
                         "<src-root>/.bsl-language-server.json")
    ap.add_argument("--cache-dir", metavar="DIR",
                    help="кэш отчёта bsl-json.json по хэшу входов (дерево src_root, "
                         "jar, конфиг): повторный прогон петли без правок — мгновенно")
    ap.add_argument("--save-report", metavar="FILE",
                    help="сохранить сырой отчёт bsl-json.json в файл (отладка)")
    ap.add_argument("--report", metavar="FILE",
                    help="md-отчёт ревью: код с замечаниями + что не так + как правильно "
                         "+ вердикт петли; каталог задачи создаётся (напр. "
                         "docs/delivery/TASK-XXX/code-review/bsl-ls-r1.md)")
    args = ap.parse_args(argv)

    java, jar = find_java(args.java), find_jar(args.jar)
    if not (java and jar):
        miss = []
        if not java:
            miss.append("java (brew install openjdk или --java/$BSL_LS_JAVA)")
        if not jar:
            miss.append("jar bsl-language-server (см. установку в докстринге)")
        print("❌ Слой BSL LS недоступен, не найдено: " + "; ".join(miss) + "\n"
              "   Работайте слоем 1: python3 scripts/checkbsl_scan.py <те же пути>",
              file=sys.stderr)
        return 3

    try:
        if args.diff:
            files = scan.diff_files(args.diff)
            if not files:
                print(format_md([], 0) if args.format == "md" else format_json([], 0, []))
                return 0
            targets = {f.resolve() for f in files}
            src_root = Path(args.src_root) if args.src_root else Path(
                os.path.commonpath([str(f) for f in files]))
            ranges = None if args.no_line_filter else added_line_ranges(args.diff, files)
        else:
            if not args.paths:
                ap.error("укажите файл/каталог или --diff REF")
            file_inputs, dir_inputs, ok = [], [], True
            for p in map(Path, args.paths):
                if p.is_file():
                    file_inputs.append(p)
                elif p.is_dir():
                    dir_inputs.append(p)
                else:
                    print(f"❌ не найден {p}", file=sys.stderr)
                    ok = False
            if not ok:
                return 2
            targets = {f.resolve() for f in file_inputs} or None
            entries = [str(f) for f in file_inputs + dir_inputs]
            src_root = Path(args.src_root) if args.src_root else Path(os.path.commonpath(entries))
            ranges = None

        config = Path(args.config) if args.config else None
        if not config:
            auto = src_root / ".bsl-language-server.json"
            config = auto if auto.is_file() else None
        if not config and args.slim_config:
            # свой конфиг не задан и не найден — генерируем узкий во временный файл
            tmp_cfg = tempfile.NamedTemporaryFile(
                mode="w", prefix="bsl_ls_slim_", suffix=".json",
                delete=False, encoding="utf-8")
            config = slim_config(Path(tmp_cfg.name))
            tmp_cfg.close()

        cache_file = None
        if args.cache_dir:
            cache_dir = Path(args.cache_dir)
            cache_dir.mkdir(parents=True, exist_ok=True)
            cache_file = cache_dir / f"{cache_key(src_root.resolve(), jar, config)}.json"

        if cache_file and cache_file.is_file():
            report = json.loads(cache_file.read_text(encoding="utf-8"))
            print(f"⚡ кэш отчёта: {cache_file.name} (входы не менялись, "
                  f"java-прогон пропущен)", file=sys.stderr)
        else:
            report = run_bsl_ls(java, jar, src_root.resolve(), config, args.timeout)
            if cache_file:
                cache_file.write_text(json.dumps(report, ensure_ascii=False),
                                      encoding="utf-8")
        if args.save_report:
            Path(args.save_report).write_text(
                json.dumps(report, ensure_ascii=False, indent=1), encoding="utf-8")
        findings, nfiles, extra = parse_report(report, targets, ranges)
    except (FileNotFoundError, RuntimeError, json.JSONDecodeError) as e:
        print(f"❌ {e}", file=sys.stderr)
        return 2

    print(format_json(findings, nfiles, extra) if args.format == "json"
          else format_md(findings, nfiles))

    if args.report:
        inputs = (f"--diff {args.diff}" if args.diff
                  else ", ".join(str(p) for p in args.paths))
        meta = {"inputs": inputs, "diff": args.diff, "round": 0}
        report_path = Path(args.report)
        # номер раунда петли: bsl-ls-rN.md в каталоге отчёта
        m = re.search(r"-r(\d+)\.md$", report_path.name)
        if m:
            meta["round"] = int(m.group(1))
        report_path.parent.mkdir(parents=True, exist_ok=True)
        report_path.write_text(build_report(findings, extra, nfiles, meta),
                               encoding="utf-8")
        print(f"\n📄 Отчёт ревью: {report_path}")
    return 1 if any(f.sev == "red" for f in findings) else 0


if __name__ == "__main__":
    sys.exit(main())
