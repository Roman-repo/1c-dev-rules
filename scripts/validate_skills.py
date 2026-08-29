#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
validate_skills.py — автопроверка структуры скилов плагина 1c-dev-rules.

Проверяет каждый skills/<name>/SKILL.md и его references/:
  - frontmatter валиден и содержит обязательные поля;
  - name совпадает с каталогом, kebab-case, префикс 1c-;
  - description <=1024 симв (хард-лимит ZCode); >250 — предупреждение
    (первые ~250 символов критичны для триггера модели);
  - license присутствует (= MIT);
  - тело SKILL.md <=500 строк;
  - нет упоминаний проектной специфики (торо_, гкс_, Project/Toir);
  - ссылки на references/*.md из тела ведут на существующие файлы
    (нет «битых» и нет «мёртвых» reference-файлов).

Использование:
    python3 scripts/validate_skills.py [PATH_TO_PLUGIN_ROOT]

Exit code: 0 — нет ошибок (могут быть warnings); 1 — есть хотя бы одна ошибка.
Без зависимостей: только стандартная библиотека.
"""
from __future__ import annotations

import re
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional, Tuple

# --- лимиты и константы ---------------------------------------------------

DESC_HARD_LIMIT = 1024          # ZCode: хард-лимит длины description
DESC_SOFT_LIMIT = 250           # рекомендация: главное — в первых ~250 симв
# DESC_SOFT_LIMIT — эмпирическая граница обрезки description в system-reminder
# ZCode (см. docs/ARCHITECTURE.md, «Триггеры скилов»); из плагина не настраивается.
# Перепроверять при мажорных обновлениях клиента — поднимать вместе с окном.
BODY_LINE_LIMIT = 500           # тело SKILL.md
REQUIRED_FIELDS = ("name", "description", "license")
NAME_PREFIX = "1c-"
KEBAB_RE = re.compile(r"^[a-z0-9]+(-[a-z0-9]+)*$")
FORBIDDEN_TOKENS = ("торо_", "гкс_", "Project/Toir")
REF_LINK_RE = re.compile(r"references/([A-Za-z0-9_.\-]+\.md)")
# Все markdown-ссылки [text](target) — для проверки относительных путей «наружу»
# (http/https/mailto/#anchor отсеиваются отдельно; references/ уже покрывает REF_LINK_RE)
MD_LINK_RE = re.compile(r"\]\(([^)]+)\)")


# --- результат проверки ---------------------------------------------------

@dataclass
class Finding:
    severity: str  # "ERR" | "WARN" | "OK"
    message: str


@dataclass
class SkillReport:
    name: str
    findings: List[Finding] = field(default_factory=list)

    def err(self, msg: str) -> None:
        self.findings.append(Finding("ERR", msg))

    def warn(self, msg: str) -> None:
        self.findings.append(Finding("WARN", msg))

    def ok(self, msg: str) -> None:
        self.findings.append(Finding("OK", msg))

    @property
    def errors(self) -> List[Finding]:
        return [f for f in self.findings if f.severity == "ERR"]

    @property
    def warnings(self) -> List[Finding]:
        return [f for f in self.findings if f.severity == "WARN"]


# --- парсер frontmatter (без pyyaml) -------------------------------------
# Формат простой: плоские ключи, значения могут быть inline или YAML block
# scalar ('>' — folded, '|' — literal). Нам важно собрать текст description.

def parse_frontmatter(text: str) -> Tuple[Optional[Dict[str, str]], Optional[str]]:
    """Вернёт (dict полей, ошибка_или_None). Только плоский YAML без вложенностей."""
    lines = text.splitlines()
    if not lines or lines[0].strip() != "---":
        return None, "нет открывающего '---' в первой строке"
    # найдём закрывающий ---
    end = None
    for i in range(1, len(lines)):
        if lines[i].strip() == "---":
            end = i
            break
    if end is None:
        return None, "нет закрывающего '---'"
    fm_lines = lines[1:end]

    fields: Dict[str, str] = {}
    i = 0
    while i < len(fm_lines):
        line = fm_lines[i]
        m = re.match(r"^([A-Za-z_][A-Za-z0-9_]*):\s*(.*)$", line)
        if not m:
            i += 1
            continue
        key, val = m.group(1), m.group(2)
        if val in (">", "|"):
            # block scalar: собираем строки с отступом
            buf: List[str] = []
            i += 1
            while i < len(fm_lines) and (
                fm_lines[i].startswith(" ") or fm_lines[i].startswith("\t")
            ):
                buf.append(fm_lines[i].strip())
                i += 1
            sep = " " if val == ">" else "\n"
            fields[key] = sep.join(buf).strip()
        else:
            fields[key] = val.strip()
            i += 1
    return fields, None


# --- проверки -------------------------------------------------------------

def validate_skill(
    skill_dir: Path,
    global_refs: Optional[Dict[str, str]] = None,
    global_md: Optional[Dict[str, List[Path]]] = None,
) -> SkillReport:
    name = skill_dir.name
    rep = SkillReport(name=name)
    skill_md = skill_dir / "SKILL.md"

    if not skill_md.is_file():
        rep.err(f"нет skills/{name}/SKILL.md")
        return rep

    text = skill_md.read_text(encoding="utf-8")
    fields, fm_err = parse_frontmatter(text)
    if fm_err is not None:
        rep.err(f"frontmatter: {fm_err}")
        return rep

    # обязательные поля
    for f in REQUIRED_FIELDS:
        if f not in fields or not fields[f]:
            rep.err(f"frontmatter: отсутствует обязательное поле '{f}'")

    # name
    fname = fields.get("name", "")
    if fname != name:
        rep.err(f"name='{fname}' не совпадает с каталогом '{name}'")
    if not fname.startswith(NAME_PREFIX):
        rep.err(f"name='{fname}' должен начинаться с '{NAME_PREFIX}'")
    if fname and not KEBAB_RE.match(fname):
        rep.err(f"name='{fname}' не kebab-case")

    # description
    desc = fields.get("description", "")
    if desc:
        dlen = len(desc)
        if dlen > DESC_HARD_LIMIT:
            rep.err(f"description: {dlen} симв > хард-лимита {DESC_HARD_LIMIT}")
        elif dlen > DESC_SOFT_LIMIT:
            rep.warn(
                f"description: {dlen} симв > {DESC_SOFT_LIMIT} — "
                f"главное должно быть в первых ~250 (риск under-trigger)"
            )
        else:
            rep.ok(f"description: {dlen} симв")
        if not re.search(r"Use when|Use after|При |Триггер", desc):
            rep.warn("description: нет явного сигнала 'Use when/При/Триггер' — риск under-trigger")
    else:
        rep.err("description пустой")

    # license
    lic = fields.get("license", "")
    if lic and lic != "MIT":
        rep.warn(f"license='{lic}' — ожидался MIT")

    # when_to_use
    if not fields.get("when_to_use"):
        rep.warn("нет when_to_use — подстраховка триггеров отсутствует")

    # тело: число строк (выкинем блок frontmatter)
    body_lines = text.splitlines()
    # найдём закрывающий --- и считаем строки после него
    body_start = 0
    if len(body_lines) > 1 and body_lines[0].strip() == "---":
        for i in range(1, len(body_lines)):
            if body_lines[i].strip() == "---":
                body_start = i + 1
                break
    body = body_lines[body_start:]
    body_len = len([l for l in body if l.strip() != ""])  # непустые строки
    if body_len > BODY_LINE_LIMIT:
        rep.err(f"тело SKILL.md: {body_len} непустых строк > лимита {BODY_LINE_LIMIT}")

    # проектная специфика (запрещена в универсальном наборе)
    for token in FORBIDDEN_TOKENS:
        if token in text:
            # найдём номер строки для удобной навигации
            for ln, line in enumerate(text.splitlines(), 1):
                if token in line:
                    rep.err(f"проектная специфика '{token}' в строке {ln}: {line.strip()[:80]}")
                    break

    # references: ссылки из тела ↔ файлы в references/
    # Учитываем cross-skill ссылки: файл может жить в references/ другого скила
    # набора (текст рядом обычно указывает владельца, напр. «скил 1c-queries»).
    referenced = set(REF_LINK_RE.findall(text))
    refs_dir = skill_dir / "references"
    actual = set()
    if refs_dir.is_dir():
        actual = {p.name for p in refs_dir.glob("*.md")}
    global_refs = global_refs or {}
    # битые ссылки: нет ни локально, ни в другом скиле набора
    for ref in sorted(referenced - actual):
        owner = global_refs.get(ref)
        if owner:
            rep.ok(f"references/{ref} — cross-skill, живёт в скиле {owner}")
        else:
            rep.err(f"ссылка на несуществующий references/{ref}")
    # мёртвые файлы (есть локально, но не упоминаются в SKILL.md)
    for ref in sorted(actual - referenced):
        rep.warn(f"references/{ref} не упоминается в SKILL.md (мёртвый файл?)")

    # внешние относительные markdown-ссылки во ВСЕХ .md каталога скила
    # (SKILL.md + references/*.md): ../../std/foo.md, ./sibling.md, ../forms.md —
    # должны существовать. Ловит класс багов «standarts/std/ → std/», неверная глубина.
    #
    # Резолвинг в 3 захода (набор скилов по-разному ссылается на одно и то же):
    #   1) точный относительный путь, как написано (../../std/queries.md);
    #   2) basename в том же каталоге (соседние references: ./foo.md, ../foo.md);
    #   3) basename в std/ (оглавления v8std: ../queries.md → std/queries.md).
    repo_root = skill_dir.parent.parent  # skills/../ = корень плагина
    global_md = global_md or {}  # {basename: [абсолютные пути]} по всему репо
    all_md = sorted(skill_dir.rglob("*.md"))
    for md_path in all_md:
        md_text = md_path.read_text(encoding="utf-8")
        for link in MD_LINK_RE.findall(md_text):
            target = link.split("#")[0].split(" ")[0]  # отбрасываем anchor и title
            if not target or target.startswith(("http://", "https://", "mailto:")):
                continue
            if target.startswith("references/"):
                continue  # уже проверено блоком выше
            if not target.endswith(".md"):
                continue  # не markdown — не наше (картинки, .bsl и пр.)

            basename = Path(target).name
            candidates = [
                (md_path.parent / target).resolve(),           # как написано
            ]
            # + файлы с тем же basename по всему репо (cross-skill, std/)
            for abs_path in global_md.get(basename, []):
                candidates.append(abs_path.resolve())
            # также пробуем basename в std/ явно (если ../foo.md — частый паттерн)
            std_candidate = repo_root / "std" / basename
            if std_candidate not in candidates:
                candidates.append(std_candidate)

            if not any(c.is_file() for c in candidates):
                rep.err(f"{md_path.relative_to(skill_dir)}: битая ссылка '{link}' → {target}")

    return rep


# --- вывод ----------------------------------------------------------------

def fmt(report: SkillReport) -> List[str]:
    out = [f"\n● {report.name}"]
    if not report.findings:
        out.append("   (нет проверок)")
        return out
    for f in report.findings:
        icon = {"ERR": "✗", "WARN": "⚠", "OK": "✓"}[f.severity]
        out.append(f"   {icon} [{f.severity}] {f.message}")
    return out


def main(argv: List[str]) -> int:
    root = Path(argv[1]).resolve() if len(argv) > 1 else Path(__file__).resolve().parent.parent
    skills_dir = root / "skills"
    if not skills_dir.is_dir():
        print(f"✗ каталог скилов не найден: {skills_dir}", file=sys.stderr)
        return 2

    skill_dirs = sorted(d for d in skills_dir.iterdir() if d.is_dir())
    if not skill_dirs:
        print(f"✗ в {skills_dir} нет подкаталогов скилов", file=sys.stderr)
        return 2

    # Глобальный индекс reference-файлов набора: {filename -> skill-owner}.
    # Нужен для распознавания cross-skill ссылок (файл в references/ другого скила).
    global_refs: Dict[str, str] = {}
    for d in skill_dirs:
        refs_dir = d / "references"
        if refs_dir.is_dir():
            for p in refs_dir.glob("*.md"):
                # если имя дублируется в нескольких скилах — фиксируем первого
                global_refs.setdefault(p.name, d.name)

    # Глобальный индекс ВСЕХ .md репозитория: {basename -> [пути]}.
    # Нужен для умного резолвинга ссылок между карточками и в std/.
    global_md: Dict[str, List[Path]] = {}
    for p in root.rglob("*.md"):
        if ".git" in p.parts:
            continue
        global_md.setdefault(p.name, []).append(p)

    print(f"Проверка {len(skill_dirs)} скилов в {skills_dir}")
    total_err = 0
    total_warn = 0
    for d in skill_dirs:
        rep = validate_skill(d, global_refs=global_refs, global_md=global_md)
        for line in fmt(rep):
            print(line)
        total_err += len(rep.errors)
        total_warn += len(rep.warnings)

    print("\n" + "=" * 60)
    status = "PASS" if total_err == 0 else "FAIL"
    print(f"Итог: {status}  |  ошибок: {total_err}  |  предупреждений: {total_warn}")
    return 0 if total_err == 0 else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv))
