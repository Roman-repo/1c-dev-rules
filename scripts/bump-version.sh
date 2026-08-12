#!/usr/bin/env bash
# bump-version.sh — атомарно бампит версию плагина в plugin.json + marketplace.json
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
# Exit 0 — оба файла обновлены и совпадают; 1 — ошибка/рассинхрон.

set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PLUGIN_JSON="$ROOT/.zcode-plugin/plugin.json"
MARKETPLACE_JSON="$ROOT/marketplace.json"

for f in "$PLUGIN_JSON" "$MARKETPLACE_JSON"; do
    [[ -f "$f" ]] || { echo "❌ не найден $f"; exit 1; }
done

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

python3 - "$PLUGIN_JSON" "$MARKETPLACE_JSON" "$NEW" <<'PY'
import json, sys
plugin_path, market_path, new = sys.argv[1], sys.argv[2], sys.argv[3]

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
PY

VP="$(cur "$PLUGIN_JSON")"
VM="$(mcur "$MARKETPLACE_JSON")"
if [[ "$VP" == "$NEW" && "$VM" == "$NEW" ]]; then
    echo "✅ plugin.json=$VP, marketplace.json=$VM — синхронно."
    echo "📝 Не забудьте: добавить запись [$NEW] в docs/CHANGELOG.md."
else
    echo "❌ РАССИНХРОН: plugin.json=$VP, marketplace.json=$VM (ожидался $NEW)"
    exit 1
fi
