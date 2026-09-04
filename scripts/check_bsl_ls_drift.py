#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
check_bsl_ls_drift.py — сверка scripts/bsl_ls_diagnostics.json с актуальным
индексом диагностик bsl-language-server (квартальный автомат, workflow
upstream-sync, джоба check-bsl-ls-drift).

Зачем: обёртка bsl_ls_analyze.py опирается на таблицу диагностик (важность →
серьёзность) и таблицу алиасов ALIAS. Новый релиз BSL LS добавляет/удаляет
диагностики и меняет важность — таблица в репо тихо устаревает, а мост имён
не покрывает новые ключи. Скрипт фиксирует дрейф до того, как он укусит.

Коды выхода: 0 — синхронно; 1 — дрейф (новые/удалённые диагностики, смена
важности); 2 — ошибка загрузки индекса (сеть/разметка).

    python3 scripts/check_bsl_ls_drift.py             # краткий вывод
    python3 scripts/check_bsl_ls_drift.py --markdown  # отчёт для Issue
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import List

sys.path.insert(0, str(Path(__file__).resolve().parent))
import bsl_ls_analyze as wrapper  # noqa: E402  (каталог + ALIAS)
import harvest_bsl_ls as harvest  # noqa: E402  (fetch + ROW_RX)

TABLE = Path(__file__).resolve().parent / "bsl_ls_diagnostics.json"

RED_IMPORTANCE = ("Блокирующий", "Критичный")  # → 🔴 в IMPORTANCE_SEV обёртки


def local_cards_check() -> List[str]:
    """Замок локальных карточек (без сети, для CI и квартальной сверки).

    Каждая немапленная 🔴-диагностика BSL LS (важность Блокирующий/Критичный,
    вне harvest-каталога и ALIAS) должна иметь локальную карточку
    LOCAL_CATALOG и запись «что не так / как правильно» в bsl_ls_fixes.json —
    иначе в полном режиме находка без №/fixes, а в slim — слепота по 🔴
    (диагностика пользователя 2026-08-31). Обратное тоже замок: карточка
    без диагностики, без fixes или дублирующая мост — рассинхрон.
    """
    problems: List[str] = []
    ls_table = wrapper.load_ls_table()
    harvest = wrapper.load_harvest_catalog()
    fixes = wrapper.load_fixes()
    bridged = set(harvest) | set(wrapper.ALIAS)
    for name, meta in ls_table.items():
        if name in bridged or name in wrapper.LOCAL_CATALOG:
            continue
        if meta.get("importance") in RED_IMPORTANCE:
            problems.append(
                f"диагностика `{name}` ({meta.get('importance')}) немапленная и"
                f" без локальной карточки: добавьте в LOCAL_CATALOG"
                f" (scripts/bsl_ls_analyze.py) + запись в bsl_ls_fixes.json")
    for name in wrapper.LOCAL_CATALOG:
        if name not in ls_table:
            problems.append(f"локальная карточка `{name}` без диагностики"
                            f" в таблице BSL LS (устарела?)")
        elif name not in fixes:
            problems.append(f"локальная карточка `{name}` без записи"
                            f" в bsl_ls_fixes.json («что не так» + «как правильно»)")
        if name in harvest or name in wrapper.ALIAS:
            problems.append(f"локальная карточка `{name}` дублирует мост"
                            f" (карточка каталога/ALIAS) — удалите локальную")
    return problems


def fetch_remote() -> dict:
    html = harvest.fetch(harvest.URL)
    rows = {}
    for name, title, enabled, importance, kind in harvest.ROW_RX.findall(html):
        strip = lambda s: __import__("re").sub(r"<[^>]+>", "", s).replace("&nbsp;", " ").strip()
        rows[name] = {"title": strip(title), "importance": strip(importance)}
    if len(rows) < 150:  # как в harvest: обвал счётчика = смена разметки
        raise RuntimeError(f"распознано только {len(rows)} диагностик — разметка индекса изменилась?")
    return rows


def main() -> int:
    ap = argparse.ArgumentParser(description="Сверка таблицы диагностик BSL LS с индексом")
    ap.add_argument("--markdown", action="store_true", help="markdown-отчёт для Issue")
    args = ap.parse_args()

    # замок локальных карточек — без сети: красный прогон и в CI, и в квартальной джобе
    card_problems = local_cards_check()
    if card_problems:
        print("❌ Замок локальных карточек:")
        for p in card_problems:
            print(f"  — {p}")
        return 1

    local = json.loads(TABLE.read_text(encoding="utf-8")).get("diagnostics", {})
    try:
        remote = fetch_remote()
    except Exception as e:  # сеть/разметка — красный прогон, Issue не открываем
        print(f"❌ индекс BSL LS не загружен: {e}", file=sys.stderr)
        return 2

    added = sorted(set(remote) - set(local))
    removed = sorted(set(local) - set(remote))
    imp_changed = sorted(
        k for k in set(local) & set(remote)
        if local[k].get("importance") != remote[k].get("importance"))

    catalog = wrapper.load_catalog()
    covered = set(catalog) | set(wrapper.ALIAS)
    uncovered_new = [k for k in added if k not in covered]
    stale_alias = sorted(k for k in wrapper.ALIAS if k not in remote)

    drift = bool(added or removed or imp_changed or stale_alias)

    head = (f"Индекс: {len(remote)} диагностик; таблица в репо: {len(local)}. "
            f"Покрытие моста (каталог ∪ ALIAS): "
            f"{len(set(remote) & covered)}/{len(remote)}.")
    lines = [head]
    if added:
        lines.append(f"\nНовые диагностики ({len(added)}): " + ", ".join(f"`{k}`" for k in added))
    if uncovered_new:
        lines.append(f"  — из них без покрытия мостом ({len(uncovered_new)}): "
                     + ", ".join(f"`{k}`" for k in uncovered_new))
    if removed:
        lines.append(f"\nУдалённые из индекса ({len(removed)}): " + ", ".join(f"`{k}`" for k in removed))
    if imp_changed:
        lines.append(f"\nСмена важности ({len(imp_changed)}):")
        for k in imp_changed:
            lines.append(f"- `{k}`: {local[k].get('importance')} → {remote[k].get('importance')}")
    if stale_alias:
        lines.append(f"\nМёртвые алиасы (ключа нет в индексе) ({len(stale_alias)}): "
                     + ", ".join(f"`{k}`" for k in stale_alias))
    if drift:
        lines.append("\nДействие: `python3 scripts/harvest_bsl_ls.py`, "
                     "затем дополнить ALIAS в scripts/bsl_ls_analyze.py по новым ключам.")

    text = "\n".join(lines)
    print(("## Дрейф таблицы диагностик BSL LS\n\n" + text) if args.markdown else text)
    return 1 if drift else 0


if __name__ == "__main__":
    raise SystemExit(main())
