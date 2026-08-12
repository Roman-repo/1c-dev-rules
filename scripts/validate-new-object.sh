#!/usr/bin/env bash
# validate-new-object.sh — автопроверка структуры нового объекта метаданных 1С
# при ручной правке XML (без EDT). Реализует проверки §6 + §7 из
# skills/1c-metadata/references/metadata-xml.md.
#
# Использование:
#   bash scripts/validate-new-object.sh /path/to/src/Catalogs/ВашОбъект
#   bash scripts/validate-new-object.sh /path/to/src/DataProcessors/ВашОбъект
#
# Exit codes:
#   0 — все критичные проверки PASS (допустимы WARN/INFO)
#   1 — есть хотя бы одна FAIL
#   2 — ошибка использования / объект не найден
#
# ВАЖНО: скрипт НЕ заменяет EDT-валидацию. Он ловит ~80% типовых багов
# ручной правки XML (дубликаты UUID, расхождения имён между файлами, №532).
# После скрипта — обязательно импортируйте объект в EDT и проверьте визуально.

set -u

# ─── разбор аргументов ────────────────────────────────────────────────

OBJ_PATH="${1:-}"
if [[ -z "$OBJ_PATH" ]]; then
    echo "Использование: $0 <путь-к-объекту>"
    echo "Пример:        $0 /path/to/src/Catalogs/МойСправочник"
    exit 2
fi

if [[ ! -d "$OBJ_PATH" ]]; then
    echo "❌ ERR: каталог объекта не существует: $OBJ_PATH"
    exit 2
fi

OBJ_PATH=$(cd "$OBJ_PATH" && pwd)
OBJ_NAME=$(basename "$OBJ_PATH")

# ─── найти файлы объекта ──────────────────────────────────────────────

MDO=$(find "$OBJ_PATH" -maxdepth 1 -name "*.mdo" -type f 2>/dev/null | head -1)
FORM=""
MODULE=""
if [[ -d "$OBJ_PATH/Forms" ]]; then
    FORM=$(find "$OBJ_PATH/Forms" -name "Form.form" -type f 2>/dev/null | head -1)
    MODULE=$(find "$OBJ_PATH/Forms" -name "Module.bsl" -type f 2>/dev/null | head -1)
fi

# src — на 2 уровня выше (.../src/Catalogs/Объект → .../src)
SRC_ROOT=$(cd "$OBJ_PATH/../.." && pwd)

# Тип объекта → единственное число для Configuration.mdo / Roles
OBJ_TYPE_DIR=$(basename "$(dirname "$OBJ_PATH")")
case "$OBJ_TYPE_DIR" in
    Catalogs)                   OBJ_TYPE_SG="Catalog" ;;
    Documents)                  OBJ_TYPE_SG="Document" ;;
    DataProcessors)             OBJ_TYPE_SG="DataProcessor" ;;
    InformationRegisters)       OBJ_TYPE_SG="InformationRegister" ;;
    AccumulationRegisters)      OBJ_TYPE_SG="AccumulationRegister" ;;
    Constants)                  OBJ_TYPE_SG="Constant" ;;
    ChartsOfCharacteristicTypes) OBJ_TYPE_SG="ChartOfCharacteristicTypes" ;;
    *)                          OBJ_TYPE_SG="" ;;
esac

# ─── счётчики и хелперы ───────────────────────────────────────────────

PASS_COUNT=0
FAIL_COUNT=0
WARN_COUNT=0
INFO_COUNT=0

ok()   { echo "✅ PASS: $1"; PASS_COUNT=$((PASS_COUNT+1)); }
fail() { echo "❌ FAIL: $1"; FAIL_COUNT=$((FAIL_COUNT+1)); }
warn() { echo "⚠️  WARN: $1"; WARN_COUNT=$((WARN_COUNT+1)); }
info() { echo "ℹ️  INFO: $1"; INFO_COUNT=$((INFO_COUNT+1)); }

echo "════════════════════════════════════════════════════════════════════"
echo " Валидация объекта: $OBJ_NAME"
echo " Путь:    $OBJ_PATH"
echo " src:     $SRC_ROOT"
echo " Тип:     ${OBJ_TYPE_SG:-<неизвестен — каталог '$OBJ_TYPE_DIR' не распознан>}"
[[ -n "$MDO"    ]] && echo " .mdo:      $MDO"
[[ -n "$FORM"   ]] && echo " Form.form: $FORM"
[[ -n "$MODULE" ]] && echo " Module:    $MODULE"
echo "════════════════════════════════════════════════════════════════════"
echo

# =====================================================================
# §6. ПРОВЕРКИ ВНУТРИ ФАЙЛОВ
# =====================================================================

echo "─── §6. Проверки внутри файлов ───"

if [[ -z "$MDO" ]]; then
    fail ".mdo не найден в корне объекта ($OBJ_PATH)"
else
    # §6 M1: дубликаты UUID внутри .mdo
    dups=$(grep -oE '(uuid|typeId|valueTypeId)="[0-9a-f-]+"' "$MDO" | sort | uniq -d)
    if [[ -z "$dups" ]]; then
        ok "M1: нет дубликатов UUID в .mdo"
    else
        fail "M1: дубликаты UUID: $dups"
    fi

    # §6 M4: синоним ru
    if grep -q '<key>ru</key>' "$MDO"; then
        ok "M4: есть синоним ru"
    else
        fail "M4: нет синонима ru"
    fi

    # правило 12 AGENTS.md: все UUID уникальны по проекту
    proj_conflicts=0
    while IFS= read -r uuid; do
        [[ -z "$uuid" ]] && continue
        c=$(grep -rln "$uuid" "$SRC_ROOT" 2>/dev/null | wc -l | tr -d ' ')
        if [[ "$c" -gt 1 ]]; then
            fail "правило 12: UUID $uuid найден в $c файлах проекта (ожидается 1)"
            proj_conflicts=$((proj_conflicts+1))
        fi
    done < <(grep -oE '(uuid|typeId|valueTypeId)="[0-9a-f-]+"' "$MDO" | sed 's/.*="//;s/"$//' | sort -u)
    [[ "$proj_conflicts" -eq 0 ]] && ok "правило 12: все UUID уникальны по проекту"
fi

if [[ -n "$FORM" ]]; then
    # §6 F3: companion-элементы. InputField+CheckBoxField → contextMenu + extendedTooltip
    # NB: в EDT-формате тип поля — это <type>InputField</type> (внутри <items xsi:type="form:FormField">),
    # НЕ xsi:type="form:InputField". grep -c всегда печатает число (даже 0).
    in_cnt=$(grep -c '<type>InputField</type>' "$FORM" 2>/dev/null);  in_cnt=${in_cnt:-0}
    cb_cnt=$(grep -c '<type>CheckBoxField</type>' "$FORM" 2>/dev/null); cb_cnt=${cb_cnt:-0}
    cm_cnt=$(grep -c '<contextMenu>' "$FORM" 2>/dev/null);             cm_cnt=${cm_cnt:-0}
    et_cnt=$(grep -c '<extendedTooltip>' "$FORM" 2>/dev/null);         et_cnt=${et_cnt:-0}
    expected_cm=$((in_cnt + cb_cnt))
    if [[ $expected_cm -eq 0 ]] || [[ $cm_cnt -ge $expected_cm ]]; then
        ok "F3: contextMenu ($cm_cnt) покрывает поля ($expected_cm)"
    else
        fail "F3: contextMenu ($cm_cnt) < InputField+CheckBoxField ($expected_cm) — не у каждого поля contextMenu"
    fi
    if [[ $et_cnt -ge $expected_cm ]]; then
        ok "F3: extendedTooltip ($et_cnt) покрывает поля ($expected_cm)"
    else
        warn "F3: extendedTooltip ($et_cnt) < полей ($expected_cm) — EDT пересоздаст при открытии"
    fi

    # §6 F4: DataPath сегменты (для ручной сверки с <attributes>)
    segs=$(grep -oE '<segments>[^<]+</segments>' "$FORM" 2>/dev/null)
    if [[ -n "$segs" ]]; then
        info "F4: DataPath сегменты — сверьте корневой сегмент с <attributes>:"
        echo "$segs" | sed 's/^/      /'
    fi
fi

echo

# =====================================================================
# §7. КРОСС-ФАЙЛОВАЯ СОГЛАСОВАННОСТЬ
# =====================================================================

echo "─── §7. Кросс-файловая согласованность ───"

if [[ -z "$MDO" ]]; then
    fail "§7 пропущен: нет .mdo"
else
    # §7 X1: реквизиты из .mdo должны быть в Form.form и/или Module.bsl
    # Извлекаем имена из блоков <attributes uuid="..."><name>X</name>
    req_count=0
    req_missing_form=0
    while IFS= read -r req; do
        [[ -z "$req" ]] && continue
        req_count=$((req_count+1))
        if [[ -n "$FORM" ]]; then
            fc=$(grep -c "Объект\.$req\|<name>$req</name>" "$FORM" 2>/dev/null || echo 0)
            if [[ "$fc" -eq 0 ]]; then
                warn "X1: реквизит '$req' из .mdo не найден в Form.form"
                req_missing_form=$((req_missing_form+1))
            fi
        fi
    done < <(awk '/<attributes uuid=/{getline; gsub(/<\/?name>/,""); gsub(/^[ \t]+/,""); print}' "$MDO")

    if [[ $req_count -gt 0 ]] && [[ $req_missing_form -eq 0 ]] && [[ -n "$FORM" ]]; then
        ok "X1: все $req_count реквизитов из .mdo присутствуют в Form.form"
    elif [[ $req_count -gt 0 ]] && [[ -n "$FORM" ]]; then
        info "X1: проверено $req_count реквизитов; $req_missing_form не найдены в Form.form"
    fi
fi

# §7 X5: параметры запроса — &X в тексте ↔ УстановитьПараметр("X", ...)
# ВНИМАНИЕ: &НаКлиенте/&НаСервере и пр. — это директивы метода, НЕ параметры запроса.
# Их надо исключить, иначе будет ложное срабатывание.
if [[ -n "$MODULE" ]]; then
    DIRECTIVES_RE='^(НаКлиенте|НаСервере|НаСервереБезКонтекста|НаКлиентеНаСервере|НаКлиентеНаСервереБезКонтекста|НаКлиентеНаСервереБезВозвратаНаКлиента|На Corporate|На интеграционном сервере)$'
    qparams=$(grep -oE '&[А-Яа-яЁё_]+' "$MODULE" 2>/dev/null \
        | sed 's/^&//' \
        | grep -vE "$DIRECTIVES_RE" \
        | sort -u)
    setparams=$(grep -oE 'УстановитьПараметр\("[^"]+"' "$MODULE" 2>/dev/null | sed 's/УстановитьПараметр("//;s/"$//' | sort -u)
    if [[ -n "$qparams" ]] || [[ -n "$setparams" ]]; then
        # симметричная разность: то что в одном, но не в другом
        only_in_text=$(comm -23 <(echo "$qparams") <(echo "$setparams") 2>/dev/null)
        only_in_call=$(comm -13 <(echo "$qparams") <(echo "$setparams") 2>/dev/null)
        if [[ -z "$only_in_text" ]] && [[ -z "$only_in_call" ]]; then
            cnt=$(echo "$qparams" | grep -c .)
            ok "X5: параметры запроса (&) и УстановитьПараметр согласованы ($cnt шт.)"
        else
            fail "X5: рассогласование параметров запроса"
            [[ -n "$only_in_text" ]] && echo "      есть в &X, нет в УстановитьПараметр:" $only_in_text
            [[ -n "$only_in_call" ]] && echo "      есть в УстановитьПараметр, нет в &X:" $only_in_call
        fi
    fi
fi

# §7 X6: объект зарегистрирован в Configuration.mdo
if [[ -n "$OBJ_TYPE_SG" ]]; then
    CONFIG="$SRC_ROOT/Configuration/Configuration.mdo"
    if [[ -f "$CONFIG" ]]; then
        if grep -q "$OBJ_TYPE_SG\.$OBJ_NAME" "$CONFIG"; then
            ok "X6: '$OBJ_TYPE_SG.$OBJ_NAME' зарегистрирован в Configuration.mdo"
        else
            fail "X6: '$OBJ_TYPE_SG.$OBJ_NAME' НЕ найден в Configuration.mdo"
        fi
    else
        warn "X6: Configuration.mdo не найден ($CONFIG)"
    fi
fi

# §7 X7 (№532): роли
if [[ -n "$OBJ_TYPE_SG" ]]; then
    roles_found=$(grep -rln "$OBJ_TYPE_SG\.$OBJ_NAME" "$SRC_ROOT/Roles/" 2>/dev/null | head -1)
    if [[ -n "$roles_found" ]]; then
        ok "X7 (№532): объект найден в роли: $(basename "$(dirname "$roles_found")")"
    else
        fail "X7 (№532): '$OBJ_TYPE_SG.$OBJ_NAME' не найден ни в одной роли — объект невидим пользователям"
    fi

    # §7 X7b: подсистема
    subs_found=$(grep -rln "$OBJ_TYPE_SG\.$OBJ_NAME" "$SRC_ROOT/Subsystems/" 2>/dev/null | head -1)
    if [[ -n "$subs_found" ]]; then
        ok "X7: объект включён в подсистему: $(basename "$(dirname "$subs_found")")"
    else
        warn "X7: объект не включён ни в одну подсистему (может быть не виден в интерфейсе)"
    fi
fi

echo

# =====================================================================
# ИТОГ
# =====================================================================

echo "════════════════════════════════════════════════════════════════════"
echo " ИТОГ: ✅ $PASS_COUNT PASS   ❌ $FAIL_COUNT FAIL   ⚠️  $WARN_COUNT WARN   ℹ️  $INFO_COUNT INFO"
echo "════════════════════════════════════════════════════════════════════"

if [[ $FAIL_COUNT -gt 0 ]]; then
    echo
    echo "🔴 Обнаружены FAIL — исправьте перед коммитом."
    echo "   ⚠️  Скрипт НЕ заменяет EDT-валидацию — дополнительно импортируйте объект в EDT."
    exit 1
fi

echo
echo "✅ Все критичные проверки пройдены."
echo "   ⚠️  Скрипт НЕ заменяет EDT-валидацию — импортируйте объект в EDT и проверьте,"
echo "   что форма открывается визуально."
exit 0
