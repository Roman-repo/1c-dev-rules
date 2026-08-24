#!/usr/bin/env bash
# bump-version.sh — атомарно бампит версию плагина в plugin.json + marketplace.json
# + kimi.plugin.json (если файл существует — манифест для Kimi; отсутствие файла
# не считается ошибкой, чтобы скрипт работал и в ZCode-only копиях репо)
# + README.md — бейдж версии в шапке (версия видна пользователю; README есть,
# а бейджа нет → рассинхрон, exit 1).
# СИНХРОННО и с верификацией. Предотвращает инцидент F1 (0.4.0): plugin.json и
# CHANGELOG ушли на новую версию, а marketplace.json отстал → ZCode не обновил кэш,
# релиз не дошёл до пользователей.
#
# Использование (из корня репо):
#   bash scripts/bump-version.sh 0.6.0       # явно
#   bash scripts/bump-version.sh patch       # 0.5.0 → 0.5.1
#   bash scripts/bump-version.sh minor       # 0.5.0 → 0.6.0
#   bash scripts/bump-version.sh major       # 0.5.0 → 1.0.0
#
# Единственный источник истины о текущей версии — plugin.json.
# Exit 0 — все файлы обновлены и совпадают; 1 — ошибка/рассинхрон.

set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PLUGIN_JSON="$ROOT/.zcode-plugin/plugin.json"
MARKETPLACE_JSON="$ROOT/marketplace.json"
KIMI_JSON="$ROOT/kimi.plugin.json"
README_MD="$ROOT/README.md"

for f in "$PLUGIN_JSON" "$MARKETPLACE_JSON"; do
    [[ -f "$f" ]] || { echo "❌ не найден $f"; exit 1; }
done
HAS_KIMI=0
[[ -f "$KIMI_JSON" ]] && HAS_KIMI=1
HAS_README=0
[[ -f "$README_MD" ]] && HAS_README=1

command -v python3 >/dev/null || { echo "❌ требуется python3"; exit 1; }

cur() { python3 -c 'import json,sys; print(json.load(open(sys.argv[1]))["version"])' "$1"; }
mcur() { python3 -c 'import json,sys; print(json.load(open(sys.argv[1]))["plugins"][0]["version"])' "$1"; }

CUR="$(cur "$PLUGIN_JSON")"
echo "Текущая версия: $CUR"

ARG="${1:-}"
if [[ "$ARG" =~ ^[0-9]+\.[0-9]+\.[0-9]+$ ]]; then
    NEW="$ARG"
elif [[ "$ARG" == "patch" || "$ARG" == "minor" || "$ARG" == "major" ]]; then
    IFS='.' read -r MAJ MIN PAT <<< "$CUR"
    case "$ARG" in
        major) MAJ=$((MAJ+1)); MIN=0; PAT=0 ;;
        minor) MIN=$((MIN+1)); PAT=0 ;;
        patch) PAT=$((PAT+1)) ;;
    esac
    NEW="$MAJ.$MIN.$PAT"
else
    echo "Использование: $0 <x.y.z | patch | minor | major>"
    exit 1
fi

[[ "$NEW" == "$CUR" ]] && { echo "⚠️  новая версия совпадает с текущей ($CUR)"; exit 1; }
echo "Новая версия:   $NEW"

python3 - "$PLUGIN_JSON" "$MARKETPLACE_JSON" "$KIMI_JSON" "$HAS_KIMI" "$README_MD" "$HAS_README" "$NEW" <<'PY'
import json, re, sys
plugin_path, market_path, kimi_path, has_kimi, readme_path, has_readme, new = sys.argv[1:8]

with open(plugin_path, encoding="utf-8") as f:
    plugin = json.load(f)
plugin["version"] = new
with open(plugin_path, "w", encoding="utf-8") as f:
    json.dump(plugin, f, indent=2, ensure_ascii=False)
    f.write("\n")

with open(market_path, encoding="utf-8") as f:
    market = json.load(f)
market["plugins"][0]["version"] = new  # версия плагина (НЕ schema version: 1)
with open(market_path, "w", encoding="utf-8") as f:
    json.dump(market, f, indent=2, ensure_ascii=False)
    f.write("\n")

if has_kimi == "1":
    with open(kimi_path, encoding="utf-8") as f:
        kimi = json.load(f)
    kimi["version"] = new
    with open(kimi_path, "w", encoding="utf-8") as f:
        json.dump(kimi, f, indent=2, ensure_ascii=False)
        f.write("\n")

if has_readme == "1":
    with open(readme_path, encoding="utf-8") as f:
        readme = f.read()
    readme, n_label = re.subn(r"(!\[Version: )[0-9]+\.[0-9]+\.[0-9]+(\])",
                              lambda m: m.group(1) + new + m.group(2), readme, count=1)
    readme, n_url = re.subn(r"(badge/version-)[0-9]+\.[0-9]+\.[0-9]+(-blue\.svg)",
                            lambda m: m.group(1) + new + m.group(2), readme, count=1)
    if n_label != 1 or n_url != 1:
        print("❌ README.md: не найден бейдж версии [![Version: X.Y.Z](…badge/version-X.Y.Z-blue.svg)] — добавьте/поправьте бейдж в шапке README")
        sys.exit(1)
    with open(readme_path, "w", encoding="utf-8") as f:
        f.write(readme)
PY

VP="$(cur "$PLUGIN_JSON")"
VM="$(mcur "$MARKETPLACE_JSON")"
VK="$NEW"
[[ "$HAS_KIMI" == "1" ]] && VK="$(cur "$KIMI_JSON")"
VR=ok
if [[ "$HAS_README" == "1" ]]; then
    grep -Fq "[![Version: $NEW]" "$README_MD" && grep -q "badge/version-$NEW-blue.svg" "$README_MD" || VR=fail
fi
if [[ "$VP" == "$NEW" && "$VM" == "$NEW" && "$VK" == "$NEW" && "$VR" == "ok" ]]; then
    if [[ "$HAS_KIMI" == "1" && "$HAS_README" == "1" ]]; then
        echo "✅ plugin.json=$VP, marketplace.json=$VM, kimi.plugin.json=$VK, README-бейдж — синхронно."
    elif [[ "$HAS_KIMI" == "1" ]]; then
        echo "✅ plugin.json=$VP, marketplace.json=$VM, kimi.plugin.json=$VK — синхронно (README отсутствует, пропущен)."
    else
        echo "✅ plugin.json=$VP, marketplace.json=$VM — синхронно (kimi.plugin.json отсутствует, пропущен)."
    fi
    echo "📝 Не забудьте: добавить запись [$NEW] в docs/CHANGELOG.md."
else
    echo "❌ РАССИНХРОН: plugin.json=$VP, marketplace.json=$VM, kimi.plugin.json=$VK, README-бейдж=$VR (ожидался $NEW)"
    exit 1
fi
