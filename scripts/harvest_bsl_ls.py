#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
harvest_bsl_ls.py — регенерация scripts/bsl_ls_diagnostics.json из индекса
диагностик bsl-language-server (https://1c-syntax.github.io/bsl-language-server/diagnostics/).

Таблица диагностик (имя, русский заголовок, включённость по умолчанию,
важность, тип) нужна обёртке scripts/bsl_ls_analyze.py: важность — fallback
для серьёзности findings, когда ключ не покрыт чек-листом сканера и каталогом.

Запуск (нужна сеть):
    python3 scripts/harvest_bsl_ls.py
"""
from __future__ import annotations

import json
import re
import ssl
import urllib.request
from pathlib import Path

URL = "https://1c-syntax.github.io/bsl-language-server/diagnostics/"
OUT = Path(__file__).resolve().parent / "bsl_ls_diagnostics.json"

ROW_RX = re.compile(
    r'<tr>\s*<td><a href="([A-Za-z][A-Za-z0-9]+)/?">[^<]+</a></td>\s*'
    r"<td>(.*?)</td>\s*"          # заголовок
    r'<td[^>]*>(.*?)</td>\s*'     # включена
    r"<td>(.*?)</td>\s*"          # важность
    r"<td>(.*?)</td>",            # тип
    re.S,
)


def fetch(url: str) -> str:
    # корпоративные машины нередко без системного пула CA — ssl-контекст по умолчанию
    with urllib.request.urlopen(url, context=ssl.create_default_context(), timeout=60) as r:
        return r.read().decode("utf-8")


def main() -> int:
    html = fetch(URL)
    rows = {}
    for name, title, enabled, importance, kind in ROW_RX.findall(html):
        strip = lambda s: re.sub(r"<[^>]+>", "", s).replace("&nbsp;", " ").strip()
        rows[name] = {
            "title": strip(title),
            "enabled": strip(enabled) == "Да",
            "importance": strip(importance),
            "kind": strip(kind),
        }
    if len(rows) < 150:  # резко упавший счётчик = поменяли разметку страницы
        raise SystemExit(f"❌ распознано только {len(rows)} диагностик — разметка индекса изменилась?")
    OUT.write_text(
        json.dumps({"source": URL, "count": len(rows), "diagnostics": rows},
                   ensure_ascii=False, indent=1) + "\n",
        encoding="utf-8")
    print(f"✅ {len(rows)} диагностик → {OUT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
