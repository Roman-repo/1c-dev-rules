#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
review_suppress.py — ведёт suppress.json: контур подавления ложных
срабатываний этапа «Код ревью» (1c-code-review).

Замкнутая петля качества: решение Ревьюера «не баг» из листа 05a ранее жило
только в таблице и умирало на следующем прогоне — MagicNumber и
CommentedOutCodeLine спорили при каждом раунде. Теперь решение фиксируется
записью в suppress.json (каталог задачи, рядом с 05a и отчётами слоёв), а
оба детерминированных слоя (--suppress у checkbsl_scan.py и
bsl_ls_analyze.py) исключают находку из вывода; подавление показывается
счётчиком и секцией «Подавленные» в md-отчёте — не молчаливое.

Формат suppress.json — список записей (валидация — checkbsl_scan.load_suppress):
    [{"key": "CommentedOutCodeLine", "file": "Module.bsl", "line": 269,
      "reason": "описательный комментарий, не код", "author": "Roman",
      "date": "2026-08-31"}]
line может отсутствовать — тогда ключ подавлен в файле целиком.

Использование:
    python3 scripts/review_suppress.py add suppress.json CommentedOutCodeLine \
        Module.bsl --line 269 --reason "описательный комментарий, не код" \
        [--author Roman]
    python3 scripts/review_suppress.py check suppress.json

Выход: 0 — ок, 2 — ошибка (нет reason, битый файл, дубликат).
Правило: подавление 🔴-находки допускается, но причина обязана быть
аргументированной — она попадает в md-отчёт и доступна Оркестратору.
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import date
from pathlib import Path
from typing import Optional

sys.path.insert(0, str(Path(__file__).resolve().parent))
import checkbsl_scan as scan  # noqa: E402  (load_suppress — единая валидация)


def _load(path: Path) -> list:
    if not path.exists():
        return []
    return scan.load_suppress(path)  # заодно валидирует существующее


def cmd_add(path: Path, key: str, file: str, line: Optional[int],
            reason: str, author: str) -> int:
    entries = _load(path)
    dup = any(e["key"] == key and scan._norm_path(e["file"]) == scan._norm_path(file)
              and e.get("line") == line for e in entries)
    if dup:
        print(f"❌ дубликат: {key} × {file}"
              + (f":{line}" if line else " (весь файл)") + " уже подавлена")
        return 2
    entries.append({"key": key, "file": file, "line": line,
                    "reason": reason, "author": author,
                    "date": date.today().isoformat()})
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(entries, ensure_ascii=False, indent=2) + "\n",
                    encoding="utf-8")
    where = f"{file}:{line}" if line else f"{file} (весь файл)"
    print(f"✅ подавлена: {key} × {where} → {path}")
    print("   не забудьте отметить решение в 05a (статус ✅ с обоснованием)")
    return 0


def cmd_check(path: Path) -> int:
    entries = _load(path)
    if not entries:
        print(f"{path}: файл отсутствует или пуст — подавлений нет")
        return 0
    print(f"{path}: {len(entries)} подавлений")
    for e in entries:
        where = (f"{e['file']}:{e['line']}" if e.get("line") is not None
                 else f"{e['file']} (весь файл)")
        print(f"  {e['key']:<28} {where}\n      причина: {e['reason']}"
              f" ({e.get('author', '—')}, {e.get('date', '—')})")
    return 0


def main(argv: Optional[list] = None) -> int:
    ap = argparse.ArgumentParser(
        description="Ведение suppress.json — подавления ложных срабатываний"
                    " ревью (решения Ревьюера «не баг» из 05a)")
    sub = ap.add_subparsers(dest="cmd", required=True)
    p_add = sub.add_parser("add", help="добавить подавление")
    p_add.add_argument("file", type=Path, help="suppress.json (создаётся при отсутствии)")
    p_add.add_argument("key", help="ключ каталога checkbsl (например CommentedOutCodeLine)")
    p_add.add_argument("module", help="файл с находкой (как в выводе слоя)")
    p_add.add_argument("--line", type=int, default=None,
                       help="строка находки; без неё — ключ подавлен в файле целиком")
    p_add.add_argument("--reason", required=True,
                       help="почему «не баг» — обязательно, попадает в отчёт")
    p_add.add_argument("--author", default="", help="кто подавил (Ревьюер)")
    p_chk = sub.add_parser("check", help="проверить и показать подавления")
    p_chk.add_argument("file", type=Path, help="suppress.json")
    args = ap.parse_args(argv)

    try:
        if args.cmd == "add":
            return cmd_add(args.file, args.key, args.module, args.line,
                           args.reason, args.author)
        return cmd_check(args.file)
    except (RuntimeError, json.JSONDecodeError) as e:
        print(f"❌ {e}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
