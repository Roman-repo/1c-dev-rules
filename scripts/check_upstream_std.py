#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
check_upstream_std.py — сверка оглавления std/ с upstream zeegin/v8std.

Локальный слой std/*.md — оглавления стандартов 1С со ссылками на
github.com/zeegin/v8std/blob/main/docs/std/NNN.md. Upstream периодически
добавляет/удаляет стандарты; ручная сверка «раз в квартал» протухает.
Этот скрипт выгружает актуальный список docs/std/*.md из GitHub API и
сравнивает множества номеров:

  - upstream \\ local — новые стандарты, которых нет в наших оглавлениях;
  - local \\ upstream — номера, на которые мы ссылаемся, но файл удалён
    (переименован/слит) — наши ссылки битые.

Использование:
    python3 scripts/check_upstream_std.py [--markdown]

    GITHUB_TOKEN (опционально) — повышает rate limit GitHub API.

Exit code: 0 — синхронно; 1 — есть расхождение; 2 — ошибка загрузки upstream.
Без зависимостей: только стандартная библиотека.
"""
from __future__ import annotations

import json
import os
import re
import sys
import urllib.request
from pathlib import Path
from typing import List, Set

API_URL = "https://api.github.com/repos/zeegin/v8std/contents/docs/std"
STD_LINK_RE = re.compile(r"docs/std/(\d+)\.md")


def fetch_upstream_numbers() -> Set[str]:
    """Множество номеров стандартов из docs/std/ upstream-репозитория."""
    req = urllib.request.Request(
        API_URL,
        headers={
            "User-Agent": "1c-dev-rules-upstream-check",
            "Accept": "application/vnd.github+json",
        },
    )
    token = os.environ.get("GITHUB_TOKEN")
    if token:
        req.add_header("Authorization", f"Bearer {token}")
    with urllib.request.urlopen(req, timeout=60) as resp:
        data = json.loads(resp.read().decode("utf-8"))
    if not isinstance(data, list):
        raise RuntimeError(f"неожиданный ответ GitHub API: {data!r}"[:200])
    return {f["name"][:-3] for f in data
            if isinstance(f, dict) and f.get("name", "").endswith(".md")
            and f["name"][:-3].isdigit()}


def local_numbers(root: Path) -> Set[str]:
    """Множество номеров, на которые ссылаются локальные std/*.md."""
    nums: Set[str] = set()
    for md in sorted((root / "std").glob("*.md")):
        nums.update(STD_LINK_RE.findall(md.read_text(encoding="utf-8")))
    return nums


def fmt_list(nums: List[str]) -> str:
    return ", ".join(f"[№{n}](https://github.com/zeegin/v8std/blob/main/docs/std/{n}.md)"
                     for n in nums)


def main(argv: List[str]) -> int:
    markdown = "--markdown" in argv
    root = Path(__file__).resolve().parent.parent

    local = local_numbers(root)
    try:
        upstream = fetch_upstream_numbers()
    except Exception as exc:
        print(f"✗ не удалось загрузить upstream {API_URL}: {exc}", file=sys.stderr)
        return 2

    new_upstream = sorted(upstream - local, key=int)   # есть upstream, нет у нас
    gone_upstream = sorted(local - upstream, key=int)  # есть у нас, удалено upstream

    print(f"Локально ссылок на стандарты: {len(local)}; upstream docs/std: {len(upstream)}")

    if not new_upstream and not gone_upstream:
        print("✓ Оглавление std/ синхронно с upstream v8std")
        return 0

    if markdown:
        print("## Расхождение std/ с upstream zeegin/v8std\n")
        print(f"Локально ссылок: **{len(local)}**, upstream `docs/std/`: **{len(upstream)}**.\n")
        if new_upstream:
            print(f"### Новые в upstream, отсутствуют в std/ ({len(new_upstream)})\n")
            print(fmt_list(new_upstream) + "\n")
        if gone_upstream:
            print(f"### Удалены/переименованы в upstream — локальные ссылки битые ({len(gone_upstream)})\n")
            print(fmt_list(gone_upstream) + "\n")
        print("### Что сделать\n")
        print("1. Прочитать diff изменённых стандартов в v8std.")
        print("2. Обновить `std/<раздел>.md` (добавить/убрать строки) и счётчики в `std/index.md`.")
        print("3. При затронутых правилах — внести правки в карточки `skills/*/references/`.")
        print("4. Записать в `docs/CHANGELOG.md` со ссылкой на коммит upstream.")
        print("5. Закрыть этот Issue после синхронизации.")
    else:
        if new_upstream:
            print(f"✗ Новые в upstream, отсутствуют в std/ ({len(new_upstream)}): "
                  + ", ".join(f"№{n}" for n in new_upstream))
        if gone_upstream:
            print(f"✗ Удалены/переименованы в upstream, локальные ссылки битые ({len(gone_upstream)}): "
                  + ", ".join(f"№{n}" for n in gone_upstream))
    return 1


if __name__ == "__main__":
    sys.exit(main(sys.argv))
