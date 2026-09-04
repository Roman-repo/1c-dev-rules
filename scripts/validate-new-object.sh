#!/usr/bin/env bash
# validate-new-object.sh — автопроверка структуры нового объекта метаданных 1С
# при ручной правке XML (без EDT).
#
# Состав (с 0.32.0, задача META-001 эпика EP-001):
#   • вызов python-слоя scripts/metadata_scan.py — проверки .mdo/.rights/СКД
#     (M1/M4/M12, правило 12, X5/X6/X7/X8/X9/X11/X12/X13/X14) с ключами каталога
#     checkbsl, № стандартов, подавлениями и «как правильно» из fixes-базы;
#   • собственные ФОРМОВЫЕ цепочки (F3/F4/F10, X1–X4, X15, X5/X11 по модулям
#     форм) — .form вне рамок metadata-слоя до отдельного эпика форм.
#
# Использование:
#   bash scripts/validate-new-object.sh /path/to/src/Catalogs/ВашОбъект
#
# Exit codes:
#   0 — все критичные проверки PASS (допустимы WARN/INFO)
#   1 — есть хотя бы одна FAIL (слоя или формовая)
#   2 — ошибка использования / объект не найден
#
# ВАЖНО: скрипт НЕ заменяет EDT-валидацию. После скрипта — обязательно
# импортируйте объект в EDT и проверьте визуально.

set -u

# ─── хелпер: безопасный счётчик grep -c ( НЕ использовать «|| echo 0» —   ───
#     при 0 совпадений grep -c уже печатает «0», но exit=1, и «|| echo 0»
#     добавляет второй «0» → «0\n0» → syntax error в [[ ]] → ложный PASS).
gc() { # gc PATTERN FILE → число совпадений (0 если файл пуст/нет)
    local c
    c=$(grep -c -- "$1" "$2" 2>/dev/null) || true
    c=${c//[^0-9]/}
    echo "${c:-0}"
}

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
SCRIPT_DIR=$(cd "$(dirname "$0")" && pwd)

# ─── найти файлы объекта ──────────────────────────────────────────────

MDO=$(find "$OBJ_PATH" -maxdepth 1 -name "*.mdo" -type f 2>/dev/null | head -1)

# F3: собираем ВСЕ Form.form объекта. Сортируем для стабильности.
FORMS=()
if [[ -d "$OBJ_PATH/Forms" ]]; then
    while IFS= read -r f; do
        [[ -n "$f" ]] && FORMS+=("$f")
    done < <(find "$OBJ_PATH/Forms" -name "Form.form" -type f 2>/dev/null | sort)
fi

# Модули форм (для F10 и X5/X11 — формовая часть остаётся здесь: формы вне
# рамок metadata-слоя до эпика форм)
FORM_MODULES=()
for f in "${FORMS[@]:-}"; do
    m="${f%Form.form}Module.bsl"
    [[ -f "$m" ]] && FORM_MODULES+=("$m")
done

# src — на 2 уровня выше (.../src/Catalogs/Объект → .../src)
SRC_ROOT=$(cd "$OBJ_PATH/../.." && pwd)

OBJ_TYPE_DIR=$(basename "$(dirname "$OBJ_PATH")")

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
echo " Тип:     ${OBJ_TYPE_DIR}"
[[ -n "$MDO" ]] && echo " .mdo:    $MDO"
for f in "${FORMS[@]:-}"; do [[ -n "$f" ]] && echo " Form.form: $f"; done
echo "════════════════════════════════════════════════════════════════════"
echo

# =====================================================================
# ЧАСТЬ 1. METADATA-СЛОЙ (scripts/metadata_scan.py) — .mdo/.rights/СКД
# Проверки M1/M4/M12, правило 12, X5–X14: ключи каталога checkbsl +
# локальные структурные ключи, № стандартов, «как правильно» из fixes.
# =====================================================================

echo "─── Metadata-слой (metadata_scan.py) ───"
if command -v python3 >/dev/null 2>&1; then
    if python3 "$SCRIPT_DIR/metadata_scan.py" "$OBJ_PATH" --src-root "$SRC_ROOT"; then
        ok "metadata-слой: 🔴-нарушений нет (детали — выше; 🟡/🟢 правятся или обосновываются)"
    else
        layer_rc=$?
        if [[ "$layer_rc" -eq 2 ]]; then
            fail "metadata-слой: ошибка запуска (exit 2) — проверьте вывод выше"
        else
            fail "metadata-слой: есть 🔴-нарушения — исправьте по «как правильно» выше"
        fi
    fi
else
    warn "python3 недоступен — metadata-слой пропущен (формовые проверки ниже продолжены)"
fi
echo

# =====================================================================
# ЧАСТЬ 2. ФОРМОВЫЕ ЦЕПОЧКИ (F3/F4/F10, X1–X4, X15) — .form вне рамок
# metadata-слоя (эпик EP-001); X5/X11 здесь — только по модулям форм.
# =====================================================================

echo "─── Формовые проверки (.form) ───"

# §6 F3/F4: проверки КАЖДОЙ формы
for FORM in "${FORMS[@]:-}"; do
    [[ -z "$FORM" ]] && continue
    fname=$(basename "$(dirname "$FORM")")

    # §6 F3: companion-элементы. InputField+CheckBoxField → contextMenu + extendedTooltip
    in_cnt=$(gc '<type>InputField</type>' "$FORM")
    cb_cnt=$(gc '<type>CheckBoxField</type>' "$FORM")
    cm_cnt=$(gc '<contextMenu>' "$FORM")
    et_cnt=$(gc '<extendedTooltip>' "$FORM")
    expected_cm=$((in_cnt + cb_cnt))
    if [[ $expected_cm -eq 0 ]] || [[ $cm_cnt -ge $expected_cm ]]; then
        ok "F3 [$fname]: contextMenu ($cm_cnt) покрывает поля ($expected_cm)"
    else
        fail "F3 [$fname]: contextMenu ($cm_cnt) < InputField+CheckBoxField ($expected_cm) — не у каждого поля contextMenu"
    fi
    if [[ $et_cnt -ge $expected_cm ]]; then
        ok "F3 [$fname]: extendedTooltip ($et_cnt) покрывает поля ($expected_cm)"
    else
        warn "F3 [$fname]: extendedTooltip ($et_cnt) < полей ($expected_cm) — EDT пересоздаст при открытии"
    fi

    # §6 F4: DataPath сегменты (для ручной сверки с <attributes>)
    segs=$(grep -oE '<segments>[^<]+</segments>' "$FORM" 2>/dev/null)
    if [[ -n "$segs" ]]; then
        info "F4 [$fname]: DataPath — сверьте корневой сегмент с <attributes>:"
        echo "$segs" | sed 's/^/      /'
    fi

    # §6 F10 (№630): канонические области модуля формы. 4 обязательные — даже пустые;
    # таблице формы (включая таблицу динамического списка) — своя область
    # «ОбработчикиСобытийЭлементовТаблицыФормы<Имя>». Отсутствие областей у типовой
    # (легаси) формы — не блокер, поэтому недостающие области → WARN, а не FAIL.
    fmod="${FORM%Form.form}Module.bsl"
    if [[ -f "$fmod" ]]; then
        if ! grep -q '#Область' "$fmod"; then
            warn "F10 [$fname]: в модуле формы нет ни одной #Область (№630) — легаси-модуль?"
        else
            f10_missing=""
            for f10_region in ОбработчикиСобытийФормы ОбработчикиСобытийЭлементовШапкиФормы \
                              ОбработчикиКомандФормы СлужебныеПроцедурыИФункции; do
                grep -qE "^#Область[[:space:]]+${f10_region}([[:space:]]|\$)" "$fmod" \
                    || f10_missing="$f10_missing $f10_region"
            done
            if [[ -z "$f10_missing" ]]; then
                ok "F10 [$fname]: канонические области модуля формы присутствуют (№630)"
            else
                warn "F10 [$fname]: отсутствуют области модуля формы (№630):$f10_missing"
            fi
        fi
    fi
done

# §7 X1: реквизиты из .mdo должны быть в ХОТЯ БЫ ОДНОЙ форме.
if [[ -n "$MDO" ]]; then
    req_count=0
    req_missing=0
    while IFS= read -r req; do
        [[ -z "$req" ]] && continue
        req_count=$((req_count+1))
        if [[ ${#FORMS[@]} -gt 0 ]]; then
            found=0
            for FORM in "${FORMS[@]}"; do
                if grep -qE "<segments>[^<]*\.$req</segments>|<segments>Объект\.$req</segments>" "$FORM" 2>/dev/null \
                   || grep -qE "<name>[^<]*$req</name>" "$FORM" 2>/dev/null; then
                    found=1
                    break
                fi
            done
            if [[ "$found" -eq 0 ]]; then
                warn "X1: реквизит '$req' из .mdo не найден ни в одной форме"
                req_missing=$((req_missing+1))
            fi
        fi
    done < <(awk '/<attributes uuid=/{getline; gsub(/<\/?name>/,""); gsub(/^[ \t]+/,""); print}' "$MDO")

    if [[ $req_count -gt 0 ]] && [[ ${#FORMS[@]} -gt 0 ]]; then
        if [[ $req_missing -eq 0 ]]; then
            ok "X1: все $req_count реквизитов из .mdo присутствуют в формах"
        else
            info "X1: проверено $req_count реквизитов; $req_missing не найдены в формах"
        fi
    fi
fi

# §7 X2/X3/X4/X15 (формы): надёжный разбор вложенного XML одной Python-проходкой:
#   X2 — корневой сегмент каждого dataPath есть в <attributes> формы
#        (кроме служебного «Items…»);
#   X3 — commandName «Form.Command.X» → команда X объявлена в <formCommands>
#        → её handler существует в Module.bsl формы;
#   X4 — каждый <handlers><name>Y</name> → «Процедура Y(…)» в Module.bsl формы;
#   X15 — queryText динамических списков: нет СОЕДИНЕНИЙ с виртуальными
#        таблицами и подзапросами напрямую (№655/№732), ВТ без даты — WARN
#        (№733), «ОБЪЕДИНИТЬ» без «ВСЕ» — WARN (№434).
if [[ ${#FORMS[@]} -gt 0 ]]; then
    for FORM in "${FORMS[@]:-}"; do
        [[ -z "$FORM" ]] && continue
        fname=$(basename "$(dirname "$FORM")")
        fmod="${FORM%Form.form}Module.bsl"
        while IFS= read -r verdict; do
            [[ -z "$verdict" ]] && continue
            case "$verdict" in
                OK:*)   ok   "X2/X3/X4/X15 [$fname]: ${verdict#OK: }" ;;
                FAIL:*) fail "X2/X3/X4/X15 [$fname]: ${verdict#FAIL: }" ;;
                WARN:*) warn "X2/X3/X4/X15 [$fname]: ${verdict#WARN: }" ;;
                *)      info "X2/X3/X4/X15 [$fname]: $verdict" ;;
            esac
        done < <(python3 - "$FORM" "$fmod" <<'PY'
import os, re, sys
import xml.etree.ElementTree as ET

form_path, mod_path = sys.argv[1], sys.argv[2]
try:
    root = ET.parse(form_path).getroot()
except Exception as e:
    print(f"WARN: Form.form не разобран ({e}) — сверьте вручную")
    sys.exit(0)

mod = None
if os.path.exists(mod_path):
    mod = open(mod_path, encoding="utf-8").read()

def in_module(proc):
    return mod is not None and re.search(
        r"(Процедура|Функция)\s+" + re.escape(proc) + r"\s*\(", mod) is not None

# X2: корневой сегмент dataPath ↔ <attributes>
attrs = {a.find("name").text for a in root.findall("attributes")
         if a.find("name") is not None}
bad_x2 = []
for s in root.iter("segments"):
    seg = (s.text or "").strip()
    rootseg = seg.split(".")[0]
    if rootseg and rootseg not in attrs and rootseg != "Items":
        bad_x2.append(seg)
if bad_x2:
    for seg in bad_x2:
        print(f"FAIL: dataPath '{seg}' — корневой сегмент '{seg.split('.')[0]}' "
              f"не объявлен в <attributes> (X2)")
else:
    print(f"OK: корневые сегменты dataPath объявлены в <attributes> (X2, сегментов: "
          f"{sum(1 for _ in root.iter('segments'))})")

# X3: команды формы
cmds = {}
for c in root.findall("formCommands"):
    n = c.find("name")
    h = c.find(".//handler/name")
    if n is not None:
        cmds[n.text] = (h.text or "").strip() if h is not None else None

x3_bad = 0
for b in root.iter("commandName"):
    cn = (b.text or "").strip()
    if not cn.startswith("Form.Command."):
        continue
    x = cn.split(".")[-1]
    if x not in cmds:
        print(f"FAIL: commandName '{cn}' → команда '{x}' не объявлена в <formCommands> (X3)")
        x3_bad += 1
for name, handler in cmds.items():
    if not handler:
        print(f"FAIL: команда '{name}' без action/handler — не выполнится (X3)")
        x3_bad += 1
    elif mod is None:
        print(f"WARN: команда '{name}' → Module.bsl формы не найден (X3)")
        x3_bad += 1
    elif not in_module(handler):
        print(f"FAIL: команда '{name}': handler '{handler}' не найден в Module.bsl формы (X3)")
        x3_bad += 1
if x3_bad == 0 and cmds:
    print(f"OK: команды формы разрешаются, handlers в Module.bsl (X3, команд: {len(cmds)})")

# X4: обработчики событий (форма, элементы, таблицы, OnGetDataAtServer ДС)
x4_cnt, x4_bad = 0, 0
for h in root.iter("handlers"):
    n = h.find("name")
    if n is None or not (n.text or "").strip():
        print("FAIL: пустой <handlers><name> — событие не сработает (X4)")
        x4_bad += 1
        continue
    x4_cnt += 1
    nm = n.text.strip()
    if mod is None:
        print(f"WARN: обработчик '{nm}' → Module.bsl формы не найден (X4)")
        x4_bad += 1
    elif not in_module(nm):
        print(f"FAIL: обработчик '{nm}' не найден в Module.bsl формы (X4)")
        x4_bad += 1
if x4_bad == 0 and x4_cnt:
    print(f"OK: обработчики событий найдены в Module.bsl (X4, обработчиков: {x4_cnt})")

# X15: queryText динамических списков — №655/№732/№733/№434.
x15_q, x15_bad = 0, 0
for qt in root.iter("queryText"):
    x15_q += 1
    q = (qt.text or "").replace("&amp;", "&")
    for m in re.finditer(
        r"СОЕДИНЕНИЕ\s+(\S+?)\.(Остатки|СрезПоследних|Обороты|ОстаткиИОбороты|Баланс)\s*\(", q):
        x15_bad += 1
        print(f"FAIL: ДС queryText: СОЕДИНЕНИЕ с виртуальной таблицей "
              f"{m.group(1)}.{m.group(2)}( напрямую — вынести во временную таблицу (№655/№732")
    if re.search(r"СОЕДИНЕНИЕ\s*\(\s*ВЫБРАТЬ", q):
        x15_bad += 1
        print("FAIL: ДС queryText: СОЕДИНЕНИЕ с подзапросом «(ВЫБРАТЬ…)» напрямую (№655/№732)")
    for m in re.finditer(r"(Остатки|СрезПоследних)\s*\(\s*,", q):
        x15_bad += 1
        print(f"WARN: ДС queryText: ВТ {m.group(1)}( вызвана без параметра даты — "
              f"передайте дату и отбор, либо подтвердите осознанность актуального среза (№733)")
    if re.search(r"ОБЪЕДИНИТЬ(?!\s+ВСЕ)", q):
        x15_bad += 1
        print("WARN: ДС queryText: «ОБЪЕДИНИТЬ» без «ВСЕ» — неявный DISTINCT (№434)")
if x15_q > 0 and x15_bad == 0:
    print(f"OK: queryText ДС без ВТ в соединениях и ВТ без даты (X15, запросов: {x15_q})")
PY
)
    done
fi

# §7 X5 (формы): параметры запроса — &X в тексте ↔ УстановитьПараметр("X", ...).
# Только модули ФОРМ: модули объекта (ObjectModule/ManagerModule/Module.bsl)
# проверяет metadata-слой (QueryParamMismatch) вместе с остальными X-цепочками.
if [[ ${#FORM_MODULES[@]} -gt 0 ]]; then
    DIRECTIVES_RE='^(НаКлиенте|НаСервере|НаСервереБезКонтекста|НаКлиентеНаСервере|НаКлиентеНаСервереБезКонтекста|НаКлиентеНаСервереБезВозвратаНаКлиента|НаКлиентеНаСервереБезКонтекстаВозвратНаКлиента|Перед|После|Вместо|ИзменениеИКонтроль)$'
    qparams=$(sed 's/&&[А-Яа-яA-Za-zЁё_]*//g' "${FORM_MODULES[@]}" 2>/dev/null \
        | grep -hoE '&[А-Яа-яA-Za-zЁё_]+' \
        | sed 's/^&//' \
        | grep -vE "$DIRECTIVES_RE" \
        | sort -u)
    setparams=$(grep -hoE 'УстановитьПараметр\("[^"]+"' "${FORM_MODULES[@]}" 2>/dev/null \
        | sed 's/УстановитьПараметр("//;s/"$//' | sort -u)
    if [[ -n "$qparams" ]] || [[ -n "$setparams" ]]; then
        only_in_text=$(comm -23 <(echo "$qparams") <(echo "$setparams") 2>/dev/null)
        only_in_call=$(comm -13 <(echo "$qparams") <(echo "$setparams") 2>/dev/null)
        if [[ -z "$only_in_text" ]] && [[ -z "$only_in_call" ]]; then
            cnt=$(echo "$qparams" | grep -c .)
            ok "X5 [формы]: параметры запроса и УстановитьПараметр согласованы ($cnt шт.)"
        else
            fail "X5 [формы]: рассогласование параметров запроса"
            [[ -n "$only_in_text" ]] && echo "      есть в &X, нет в УстановитьПараметр:" $only_in_text
            [[ -n "$only_in_call" ]] && echo "      есть в УстановитьПараметр, нет в &X:" $only_in_call
        fi
    fi
fi

# §7 X11 (формы): &ИзменениеИКонтроль без ПродолжитьВызов — по модулям форм
# (модули объекта проверяет metadata-слой, ChangeAndCallNoResume).
if [[ ${#FORM_MODULES[@]} -gt 0 ]]; then
    izm_files=$(grep -lE '&ИзменениеИКонтроль[[:space:]]*\(' "${FORM_MODULES[@]}" 2>/dev/null)
    if [[ -n "$izm_files" ]]; then
        bad11=0
        while IFS= read -r f; do
            [[ -z "$f" ]] && continue
            if ! grep -qE 'ПродолжитьВызов[[:space:]]*\(' "$f" 2>/dev/null; then
                fail "X11: $(basename "$f"): есть &ИзменениеИКонтроль(...), но нет ПродолжитьВызов() — оригинальный метод не выполнится"
                bad11=$((bad11+1))
            fi
        done <<< "$izm_files"
        [[ "$bad11" -eq 0 ]] && ok "X11 [формы]: все &ИзменениеИКонтроль сопровождаются ПродолжитьВызов()"
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
