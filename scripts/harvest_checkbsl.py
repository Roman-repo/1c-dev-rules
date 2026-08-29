#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Генератор справочника правил checkbsl.org для этапа «Код ревью» (1c-code-review).

Источники:
  1. docs.checkbsl.org — документация SonarQube 1C (BSL) Plugin (статический MkDocs):
     индексные страницы разделов /checks/<раздел>/ → ссылки на страницы правил
     /checks/{overall|query|metadata}/<Ключ>/. Со страницы правила берутся название
     и первый абзац описания («суть»); из «См. также» — номера стандартов v8std.
  2. Индекс раздела «Аудит безопасности» — ключи правил помечаются 🔐.

Важность правил НЕ подтягивается из BSL Language Server: названия одного правила
в источниках систематически расходятся, фаззи-джойн даёт ложные пары (проверено
2026-08-29: точных совпадений названий — 16/322, при пороге 0.72 — ложные пары
вида AutoTestUsage↔DeprecatedFind). Приоритет нарушения определяет чек-лист
скила (🔴/🟡/🟢) и стандарт, на который ссылается правило.

Выход: skills/1c-code-review/references/checkbsl/{index,code,query,metadata}.md.
Файлы помечены генератором и датой; правятся только перегенерацией
(python3 scripts/harvest_checkbsl.py). Сеть обязательна.
"""

import re
import sys
import time
import urllib.parse
import urllib.request
from datetime import date
from pathlib import Path

BASE = "https://docs.checkbsl.org"

# slug индексной страницы → (файл выхода, namespace ссылок, заголовок раздела)
SECTIONS = [
    ("ОписанияПравилПроверкиКода", "code",
     "Правила проверки кода", "overall"),
    ("ОписанияПравилаЯзыкаЗапросов", "query",
     "Правила языка запросов", "query"),
    ("ОписаниеПравилПроверкиМетаданных", "metadata",
     "Правила проверки метаданных", "metadata"),
]
SECURITY_SLUG = "ОписанияПравилАудитБезопасности"

OUT_DIR = (Path(__file__).resolve().parent.parent
           / "skills" / "1c-code-review" / "references" / "checkbsl")

ESSENCE_LIMIT = 280  # «суть» — первый абзац, не более
PAUSE_SEC = 0.15     # пауза между запросами к docs.checkbsl.org


def fetch(url: str) -> str:
    last = None
    for attempt in range(3):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "1c-dev-rules-harvester"})
            with urllib.request.urlopen(req, timeout=30) as resp:
                return resp.read().decode("utf-8", errors="replace")
        except Exception as exc:  # noqa: BLE001 — ретрай любой сетевой ошибки
            last = exc
            time.sleep(1.0 + attempt)
    raise RuntimeError(f"не удалось загрузить {url}: {last}")


def strip_tags(html: str) -> str:
    text = re.sub(r"<[^>]+>", " ", html)
    return unescape(re.sub(r"\s+", " ", text)).strip()


def unescape(s: str) -> str:
    return (s.replace("&quot;", '"').replace("&amp;", "&")
             .replace("&lt;", "<").replace("&gt;", ">").replace("&nbsp;", " "))


def article(html: str) -> str:
    m = re.search(r"<article[^>]*>(.*?)</article>", html, re.S)
    return m.group(1) if m else ""


def parse_index(slug: str):
    """Индексная страница раздела → [(ключ, название, href)]."""
    url = f"{BASE}/checks/{urllib.parse.quote(slug)}/"
    html = fetch(url)
    # MkDocs-minified: атрибуты без кавычек — href=../../overall/Key/
    links = re.findall(r"<a href=([^ >]+)[^>]*>(.*?)</a>", article(html))
    rules = []
    for href, text in links:
        m = re.search(r"([^/]+)/([^/]+)/?$", href.strip("/"))
        if not m:
            continue
        rules.append((m.group(2), strip_tags(text), href))
    return rules


def rule_url(href: str) -> str:
    """Относительная ссылка индекса ('../overall/Key/') → абсолютный URL страницы."""
    return urllib.parse.urljoin(f"{BASE}/checks/x/", href)


def parse_rule_page(href: str):
    """Страница правила → (суть, [№ стандартов v8std из «См. также»])."""
    html = fetch(rule_url(href))
    art = article(html)
    paragraphs = re.findall(r"<p>(.*?)</p>", art, re.S)
    essence_parts = []
    for p in paragraphs:
        text = strip_tags(p)
        if text in ("Неправильно:", "Правильно:") or not text:
            break
        essence_parts.append(text)
        if sum(len(t) for t in essence_parts) > ESSENCE_LIMIT:
            break
    essence = " ".join(essence_parts)
    if len(essence) > ESSENCE_LIMIT:
        essence = essence[: ESSENCE_LIMIT - 1].rstrip() + "…"
    stds = sorted({int(n) for n in
                   re.findall(r"its\.1c\.ru/db/v8std/content/(\d+)", art)})
    return essence, stds


def md_cell(text: str) -> str:
    return text.replace("|", "\\|").replace("\n", " ").strip() or "—"


def std_link(num: int) -> str:
    # зеркало zeegin — та же конвенция ссылок на стандарты, что в таблицах скила
    return f"[№{num}](https://github.com/zeegin/v8std/blob/main/docs/std/{num}.md)"


def main() -> int:
    security_keys = set()
    sec_idx = parse_index(SECURITY_SLUG)
    for key, _, _ in sec_idx:
        security_keys.add(key)
    print(f"Раздел аудита безопасности: {len(security_keys)} правил (подмножество)")

    stats = {}
    for slug, fname, title, namespace in SECTIONS:
        rules = parse_index(slug)
        # только правила своего namespace (индекс аудита ссылается на overall/metadata)
        rules = [r for r in rules if f"/{namespace}/" in r[2]]
        print(f"{title}: {len(rules)} правил, загрузка страниц…")
        rows = []
        for i, (key, name, href) in enumerate(rules, 1):
            try:
                essence, stds = parse_rule_page(href)
            except RuntimeError as exc:
                print(f"  !! {key}: {exc}", file=sys.stderr)
                essence, stds = "", []
            sec = "🔐" if key in security_keys else ""
            std = ", ".join(std_link(n) for n in stds)
            rows.append({
                "key": key, "name": name, "essence": essence,
                "std": std, "sec": sec,
                "url": rule_url(href),
            })
            if i % 40 == 0:
                print(f"  … {i}/{len(rules)}")
            time.sleep(PAUSE_SEC)
        stats[fname] = (title, rows)

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    today = date.today().isoformat()
    total = sum(len(rows) for _, rows in stats.values())
    sec_total = sum(1 for _, rows in stats.values()
                    for r in rows if r["sec"])

    for fname, (title, rows) in stats.items():
        lines = [
            f"# Каталог checkbsl — {title} ({len(rows)})",
            "",
            f"> Сгенерировано `scripts/harvest_checkbsl.py` {today} из"
            f" docs.checkbsl.org (документация SonarQube 1C (BSL) Plugin).",
            "> «Суть» — первый абзац описания правила на сайте; полное описание,"
            " примеры «неправильно/правильно» и параметры — по ссылке.",
            "> Приоритет нарушения (🔴/🟡/🟢) определяет чек-лист `1c-code-review`"
            " и стандарт из колонки «Стандарт», не сам каталог.",
            "> 🔐 — правило входит в раздел «Аудит безопасности»."
            " «Стандарт» — ссылки на v8std из «См. также» правила.",
            "",
            "| Ключ | Правило | Суть | Стандарт | 🔐 | Документация |",
            "|---|---|---|---|---|---|",
        ]
        for r in sorted(rows, key=lambda x: x["key"].lower()):
            lines.append(
                f"| `{r['key']}` | {md_cell(r['name'])} | {md_cell(r['essence'])}"
                f" | {r['std'] or '—'} | {r['sec'] or ''}"
                f" | [docs]({r['url']}) |"
            )
        (OUT_DIR / f"{fname}.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
        print(f"  → {OUT_DIR / fname} ({len(rows)} правил)")

    index = [
        "# Каталог правил checkbsl — рабочая сеть этапа «Код ревью»",
        "",
        f"> Сгенерировано `scripts/harvest_checkbsl.py` {today}. Источник:"
        " [docs.checkbsl.org](https://docs.checkbsl.org/) — правила проверки"
        " кода SonarQube 1C (BSL) Plugin. Перегенерация:"
        " `python3 scripts/harvest_checkbsl.py`.",
        "",
        "Полный свод правил проверки кода 1С для этапа 6 «Код ревью»"
        " (1c-code-review). Чек-лист скила — приоритетная программа проверки по"
        " секциям изменённого кода; этот каталог — полная сеть на случай,"
        " когда чек-лист секции не покрывает найденный паттерн.",
        "",
        "| Раздел | Файл | Правил |",
        "|---|---|---|",
    ]
    for fname, (title, rows) in stats.items():
        index.append(f"| {title} | [{fname}.md]({fname}.md) | {len(rows)} |")
    index += [
        f"| из них аудит безопасности (🔐) | — | {sec_total} |",
        "",
        "## Как пользоваться",
        "",
        "1. Определите затронутые типы объектов (запросы — `query.md`, метаданные"
        " — `metadata.md`, остальной код — `code.md`; правки часто задевают"
        " несколько разделов).",
        "2. Пройдите секции чек-листа `1c-code-review` по типу изменения —"
        " они дают целенаправленные проверки и приоритеты (🔴/🟡/🟢).",
        "3. Сверьте сомнительные места с каталогом: суть правила и связанный"
        " стандарт (№NNN) — в карточке; приоритет нарушения — по чек-листу"
        " и стандарту.",
        "4. Замечание в `05a-code-review.md` ссылается на ключ правила"
        " (колонка «Ключ») или номер стандарта (№NNN) — как в чек-листе.",
        "",
        f"Счётчики на дату генерации: всего правил — {total},"
        f" аудит безопасности — {sec_total}.",
    ]
    (OUT_DIR / "index.md").write_text("\n".join(index) + "\n", encoding="utf-8")
    print(f"  → {OUT_DIR / 'index.md'}")
    print(f"Итого: {total} правил, аудит-Б {sec_total}.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
