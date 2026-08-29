#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
check_version_sync.py — проверка синхронности версии плагина во всех местах.

Источник истины — .zcode-plugin/plugin.json. Сверяются:
  - marketplace.json        → plugins[0].version
  - kimi.plugin.json        → version (если файл есть)
  - README.md               → заголовок H1 «# <имя> — X.Y.Z»
  - README.md               → бейдж [![Version: X.Y.Z](…badge/version-X.Y.Z-blue.svg)]
  - README.md               → строка статуса «**Текущий: ✅ vX.Y.Z**»

Запускается в CI на каждый push/PR; bump-version.sh держит всё синхронным,
этот скрипт — страховка от ручных правок README/манифестов мимо скрипта.

Использование:
    python3 scripts/check_version_sync.py [PATH_TO_PLUGIN_ROOT]

Exit code: 0 — всё синхронно; 1 — есть рассинхрон; 2 — нет plugin.json.
Без зависимостей: только стандартная библиотека.
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path
from typing import List, Optional, Tuple


def read_version_json(path: Path, key_chain: Tuple) -> Optional[str]:
    if not path.is_file():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        for key in key_chain:
            data = data[key]
        return str(data)
    except (KeyError, IndexError, TypeError, json.JSONDecodeError) as exc:
        print(f"✗ {path.name}: не удалось прочитать версию ({exc})", file=sys.stderr)
        return "<unreadable>"


def check_readme(readme_path: Path, expected: str) -> List[str]:
    """Вернёт список ошибок (пустой = всё совпадает)."""
    errors: List[str] = []
    if not readme_path.is_file():
        return [f"README.md не найден: {readme_path}"]
    text = readme_path.read_text(encoding="utf-8")

    if not re.search(rf"^# .+ — {re.escape(expected)}[ \t]*$", text, flags=re.M):
        m = re.search(r"^# [^\n]*? — ([0-9]+\.[0-9]+\.[0-9]+)[ \t]*$", text, flags=re.M)
        found = m.group(1) if m else "не найдена"
        errors.append(f"README H1: версия {found}, ожидалась {expected}")

    if f"[![Version: {expected}]" not in text or f"badge/version-{expected}-blue.svg" not in text:
        m = re.search(r"!\[Version: ([0-9]+\.[0-9]+\.[0-9]+)\]", text)
        found = m.group(1) if m else "не найдена"
        errors.append(f"README бейдж: версия {found}, ожидалась {expected}")

    if f"**Текущий: ✅ v{expected}**" not in text:
        m = re.search(r"\*\*Текущий: ✅ v([0-9]+\.[0-9]+\.[0-9]+)\*\*", text)
        found = m.group(1) if m else "не найдена"
        errors.append(f"README строка статуса: версия {found}, ожидалась {expected}")

    return errors


def main(argv: List[str]) -> int:
    root = Path(argv[1]).resolve() if len(argv) > 1 else Path(__file__).resolve().parent.parent

    plugin_json = root / ".zcode-plugin" / "plugin.json"
    expected = read_version_json(plugin_json, ("version",))
    if expected is None:
        print(f"✗ не найден {plugin_json}", file=sys.stderr)
        return 2

    print(f"Эталон (plugin.json): {expected}")
    errors: List[str] = []

    marketplace = read_version_json(root / "marketplace.json", ("plugins", 0, "version"))
    if marketplace is None:
        errors.append("marketplace.json не найден")
    elif marketplace != expected:
        errors.append(f"marketplace.json: версия {marketplace}, ожидалась {expected}")

    kimi_json = root / "kimi.plugin.json"
    if kimi_json.is_file():
        kimi = read_version_json(kimi_json, ("version",))
        if kimi != expected:
            errors.append(f"kimi.plugin.json: версия {kimi}, ожидалась {expected}")

    errors.extend(check_readme(root / "README.md", expected))

    if errors:
        print("\nРАССИНХРОН:")
        for e in errors:
            print(f"  ✗ {e}")
        print("\nИсправление: bash scripts/bump-version.sh <версия> — обновит все места атомарно.")
        return 1

    print("✓ Версия синхронна: plugin.json, marketplace.json, kimi.plugin.json, README (H1 + бейдж + статус)")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
