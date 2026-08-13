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

# ─── найти файлы объекта ──────────────────────────────────────────────

MDO=$(find "$OBJ_PATH" -maxdepth 1 -name "*.mdo" -type f 2>/dev/null | head -1)

# F3: собираем ВСЕ Form.form объекта (раньше был «find | head -1» — брал
# недетерминированно одну форму, часто не главную). Сортируем для стабильности.
FORMS=()
if [[ -d "$OBJ_PATH/Forms" ]]; then
    while IFS= read -r f; do
        [[ -n "$f" ]] && FORMS+=("$f")
    done < <(find "$OBJ_PATH/Forms" -name "Form.form" -type f 2>/dev/null | sort)
fi

# Модули форм (для X5 — параметры запроса в обработчиках)
FORM_MODULES=()
for f in "${FORMS[@]:-}"; do
    m="${f%Form.form}Module.bsl"
    [[ -f "$m" ]] && FORM_MODULES+=("$m")
done

# F7: модуль объекта и модуль менеджера — в них живут запросы проведения,
# а не только в модулях форм. X5 теперь проверяет и их.
OBJ_MODULE=""
[[ -f "$OBJ_PATH/ObjectModule.bsl" ]] && OBJ_MODULE="$OBJ_PATH/ObjectModule.bsl"
MGR_MODULE=""
[[ -f "$OBJ_PATH/ManagerModule.bsl" ]] && MGR_MODULE="$OBJ_PATH/ManagerModule.bsl"
# CommonModule держит код в корневом Module.bsl (НЕ ObjectModule/ManagerModule).
# Без этого X5 (параметры запроса &X ↔ УстановитьПараметр) не проверял бы общие
# модули — а в них живут запросы регламентных заданий (G1 3-го полевого теста).
CM_MODULE=""
[[ -f "$OBJ_PATH/Module.bsl" ]] && CM_MODULE="$OBJ_PATH/Module.bsl"

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
    CommonModules)              OBJ_TYPE_SG="CommonModule" ;;
    ScheduledJobs)              OBJ_TYPE_SG="ScheduledJob" ;;
    HTTPServices)               OBJ_TYPE_SG="HTTPService" ;;
    WebServices)                OBJ_TYPE_SG="WebService" ;;
    EventSubscriptions)         OBJ_TYPE_SG="EventSubscription" ;;
    Reports)                    OBJ_TYPE_SG="Report" ;;
    *)                          OBJ_TYPE_SG="" ;;
esac

# Тип не распознан (Enums, Settings, DefinedTypes и т.п. не покрыты). Кросс-файловые
# проверки §7 (X6/X7/X8/X12…) Gate'ятся на непустом OBJ_TYPE_SG и молча пропускаются —
# без этого флага скрипт выдал бы ложный «✅ все проверки пройдены» для неизвестного
# типа (4-й полевой тест, кейс G: EventSubscription до фиксуса не распознавался).
TYPE_UNKNOWN=0
[[ -z "$OBJ_TYPE_SG" ]] && TYPE_UNKNOWN=1

# У каких типов есть ОБЪЕКТНЫЕ права (№532). CommonModule и ScheduledJob объектных
# прав НЕ имеют (метод/РЗ вызывается платформой, роли на них не ставятся) — для них
# №532 неприменим. Без этого флага X7 давал бы ложный FAIL на каждый новый модуль/РЗ
# (G1 3-го полевого теста: ScheduledJobs/CommonModules вообще не распознавались).
# HTTPService/WebService имеют право Use (не Read/Update) — но проверку «объект в роли»
# (X7) для них тоже надо гонять, поэтому они в HAS_RIGHTS (G4 3-го полевого теста).
case "$OBJ_TYPE_SG" in
    Catalog|Document|DataProcessor|InformationRegister|AccumulationRegister|Constant|ChartOfCharacteristicTypes|HTTPService|WebService|Report)
        HAS_RIGHTS=1 ;;
    *)  HAS_RIGHTS=0 ;;
esac

# Принадлежность к подсистеме критична для видимости в командном интерфейсе ТОЛЬКО
# у пользовательских (UI) объектов. Регистры/HTTP- и Web-сервисы/константы в подсистему
# обычно НЕ входят (служебные, интеграционные, управляются через формы/настройки) —
# для них X7b понижаем до INFO, иначе ложный WARN на каждом таком объекте
# (4-й полевой тест, кейс F = G2 3-й сессии: рег. очереди/логов, HTTP-сервисы).
# Report — пользовательский отчёт, виден в подсистеме отчётов (5-й полевой тест, кейс B).
case "$OBJ_TYPE_SG" in
    Catalog|Document|DataProcessor|ChartOfCharacteristicTypes|Report)
        HAS_UI=1 ;;
    *)  HAS_UI=0 ;;
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
for f in "${FORMS[@]:-}"; do [[ -n "$f" ]] && echo " Form.form: $f"; done
[[ -n "$OBJ_MODULE" ]] && echo " ObjectModule: $OBJ_MODULE"
[[ -n "$MGR_MODULE" ]] && echo " ManagerModule: $MGR_MODULE"
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

    # правило 12 AGENTS.md: все UUID уникальны по проекту.
    # G6 (3-й полевой тест): Python-проход вместо N × grep. BSD grep с -f и многими
    # паттернами минутами виснет на крупных объектах (WebServices/Exchange, 81 UUID →
    # 5:54 на grep -rlF -f; Python os.walk по .mdo/.form/.rights — 2.6с, ~135× быстрее).
    rule12=$(python3 - "$SRC_ROOT" "$MDO" <<'PY'
import os, re, sys
src, mdo = sys.argv[1], sys.argv[2]
try:
    with open(mdo, encoding='utf-8') as f:
        text = f.read()
except Exception:
    print("ERR"); sys.exit(0)
our = set(re.findall(r'(?:uuid|typeId|valueTypeId)="([0-9a-f-]{36})"', text))
if not our:
    print("OK 0"); sys.exit(0)
exts = ('.mdo', '.form', '.rights')
hits = {}
for root, _, files in os.walk(src):
    for fn in files:
        if not fn.endswith(exts):
            continue
        try:
            with open(os.path.join(root, fn), encoding='utf-8', errors='ignore') as fh:
                t = fh.read()
        except Exception:
            continue
        for u in re.findall(r'"([0-9a-f-]{36})"', t):
            if u in our:
                hits[u] = hits.get(u, 0) + 1
conflicts = {u: c for u, c in hits.items() if c > 1}
if not conflicts:
    print("OK %d" % len(our))
else:
    print("CONFLICT %d" % len(conflicts))
    for u, c in sorted(conflicts.items()):
        print("%s %d" % (u, c))
PY
)
    rule12_head=$(echo "$rule12" | head -1)
    case "$rule12_head" in
        OK*)
            ok "правило 12: все UUID уникальны по проекту ($(echo "$rule12_head" | awk '{print $2}') шт.)" ;;
        CONFLICT*)
            while read -r u c; do
                [[ -z "$u" ]] && continue
                fail "правило 12: UUID $u найден в $c файлах проекта (ожидается 1)"
            done < <(echo "$rule12" | tail -n +2) ;;
        ERR*)
            warn "правило 12: не удалось прочитать .mdo" ;;
        *)
            warn "правило 12: неожиданный ответ: $rule12_head" ;;
    esac

    # F8/§6 M12: специфика accumulation-регистров
    if [[ "$OBJ_TYPE_SG" == "AccumulationRegister" ]]; then
        res_cnt=$(gc '<resources uuid=' "$MDO")
        dim_cnt=$(gc '<dimensions uuid=' "$MDO")
        if [[ "$res_cnt" -ge 1 ]]; then
            ok "M12: есть ресурсы ($res_cnt)"
        else
            fail "M12: нет ресурсов (<resources>) — регистр без ресурса некорректен"
        fi
        if [[ "$dim_cnt" -ge 1 ]]; then
            ok "M12: есть измерения ($dim_cnt)"
        else
            fail "M12: нет измерений (<dimensions>) — регистр без измерения некорректен"
        fi
        if grep -q '<registerType>Turnovers</registerType>' "$MDO"; then
            warn "M12: регистр Turnovers — контролировать остатки через .Остатки() НЕЛЬЗЯ (нужен Balance/BalanceAndTurnovers). См. registers-design §1"
        elif grep -q '<name>RecordType</name>' "$MDO"; then
            ok "M12: баланс-регистр (есть RecordType) — .Остатки() доступна"
        else
            info "M12: тип регистра не указан явно / нет RecordType — сверьте с эталоном (EDT: Balance по умолчанию)"
        fi
    fi
fi

# §6 F3/F4: проверки КАЖДОЙ формы (раньше — одной, недетерминированной)
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
done

echo

# =====================================================================
# §7. КРОСС-ФАЙЛОВАЯ СОГЛАСОВАННОСТЬ
# =====================================================================

echo "─── §7. Кросс-файловая согласованность ───"

if [[ -z "$MDO" ]]; then
    fail "§7 пропущен: нет .mdo"
else
    # §7 X1: реквизиты из .mdo должны быть в ХОТЯ БЫ ОДНОЙ форме.
    # Учитываем пути и шапки, и ТЧ: «Объект.Атр» / «Объект.ТЧ.Атр» (segments
    # оканчиваются на «.Атр») и колонки таблицы «<name>…Атр</name>».
    # F2: баг «grep -c … || echo 0» исправлен — используем хелпер gc().
    req_count=0
    req_missing=0
    while IFS= read -r req; do
        [[ -z "$req" ]] && continue
        req_count=$((req_count+1))
        if [[ ${#FORMS[@]} -gt 0 ]]; then
            found=0
            for FORM in "${FORMS[@]}"; do
                # segments оканчиваются на «.<req>» или совпадают «Объект.<req>»
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

# §7 X5: параметры запроса — &X в тексте ↔ УстановитьПараметр("X", ...).
# F7: сканируем НЕ только модули форм, но и ObjectModule.bsl / ManagerModule.bsl —
# именно там живут запросы проведения (Остатки/СрезПоследних в ОбработкаПроведения).
# G7' (3-й полевой тест, кейс D): CFE-директивы перехвата (&Перед/&После/&Вместо/
# &ИзменениеИКонтроль) X5 ловил как параметры запроса → ложный FAIL в модулях расширений.
DIRECTIVES_RE='^(НаКлиенте|НаСервере|НаСервереБезКонтекста|НаКлиентеНаСервере|НаКлиентеНаСервереБезКонтекста|НаКлиентеНаСервереБезВозвратаНаКлиента|НаКлиентеНаСервереБезКонтекстаВозвратНаКлиента|Перед|После|Вместо|ИзменениеИКонтроль)$'
X5_FILES=()
for m in "${FORM_MODULES[@]:-}"; do [[ -n "$m" ]] && X5_FILES+=("$m"); done
# ObjectModule/ManagerModule ОТЧЁТА содержат запросы СКД: их параметры &X связаны
# через схему компоновки / КомпоновщикНастроек, а НЕ через Запрос.УстановитьПараметр.
# Поэтому для Report эти модули из X5 исключаем — иначе ложный FAIL «рассогласование».
# Корректность СКД-параметров отчёта проверяет X13 (mainDataCompositionSchema↔Template).
# (5-й полевой тест, кейс B: X5 ложился на существующий отчёт с инлайн-запросом СКД.)
if [[ "$OBJ_TYPE_SG" != "Report" ]]; then
    [[ -n "$OBJ_MODULE" ]] && X5_FILES+=("$OBJ_MODULE")
    [[ -n "$MGR_MODULE" ]] && X5_FILES+=("$MGR_MODULE")
fi
[[ -n "$CM_MODULE" ]]  && X5_FILES+=("$CM_MODULE")

if [[ ${#X5_FILES[@]} -gt 0 ]]; then
    # Параметры запроса могут быть и латиничными (RepairID) — иначе такой параметр
    # НЕ попадёт в выборку &X, и скрипт выдаст ложное «рассогласование»
    # (G5 3-го полевого теста: латиничное имя параметра). Латиничных директив нет.
    qparams=$(grep -hoE '&[А-Яа-яA-Za-zЁё_]+' "${X5_FILES[@]}" 2>/dev/null \
        | sed 's/^&//' \
        | grep -vE "$DIRECTIVES_RE" \
        | sort -u)
    setparams=$(grep -hoE 'УстановитьПараметр\("[^"]+"' "${X5_FILES[@]}" 2>/dev/null \
        | sed 's/УстановитьПараметр("//;s/"$//' | sort -u)
    if [[ -n "$qparams" ]] || [[ -n "$setparams" ]]; then
        only_in_text=$(comm -23 <(echo "$qparams") <(echo "$setparams") 2>/dev/null)
        only_in_call=$(comm -13 <(echo "$qparams") <(echo "$setparams") 2>/dev/null)
        if [[ -z "$only_in_text" ]] && [[ -z "$only_in_call" ]]; then
            cnt=$(echo "$qparams" | grep -c .)
            ok "X5: параметры запроса (&) и УстановитьПараметр согласованы ($cnt шт., файлы: ${X5_FILES[*]##*/})"
        else
            fail "X5: рассогласование параметров запроса"
            [[ -n "$only_in_text" ]] && echo "      есть в &X, нет в УстановитьПараметр:" $only_in_text
            [[ -n "$only_in_call" ]] && echo "      есть в УстановитьПараметр, нет в &X:" $only_in_call
        fi
    fi
fi

# §7 X11 (CFE-перехватчики): &ИзменениеИКонтроль без ПродолжитьВызов — оригинальный
# метод молча не выполнится (скил 1c-extensions, thin-triggers «&ИзменениеИКонтроль без
# ПродолжитьВызов»). Эвристика на уровне файла: если в модуле есть &ИзменениеИКонтроль,
# но нет ни одного ПродолжитьВызов — FAIL. (Не procedure-level: ПродолжитьВызов может
# оказаться в соседней процедуре того же файла — тогда проверка промолчит, это компромисс.)
if [[ ${#X5_FILES[@]} -gt 0 ]]; then
    # ВАЖНО: ищем именно ДИРЕКТИВУ &ИзменениеИКонтроль("...") и ВЫЗОВ ПродолжитьВызов(
    # со скобкой — иначе словим упоминания в комментариях (ложный FAIL).
    izm_files=$(grep -lE '&ИзменениеИКонтроль[[:space:]]*\(' "${X5_FILES[@]}" 2>/dev/null)
    if [[ -n "$izm_files" ]]; then
        bad11=0
        while IFS= read -r f; do
            [[ -z "$f" ]] && continue
            if ! grep -qE 'ПродолжитьВызов[[:space:]]*\(' "$f" 2>/dev/null; then
                fail "X11: $(basename "$f"): есть &ИзменениеИКонтроль(...), но нет ПродолжитьВызов() — оригинальный метод не выполнится"
                bad11=$((bad11+1))
            fi
        done <<< "$izm_files"
        [[ "$bad11" -eq 0 ]] && ok "X11: все &ИзменениеИКонтроль сопровождаются ПродолжитьВызов()"
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

# §7 X7 (№532): роли. Применимо ТОЛЬКО к типам с объектными правами — CommonModule
# и ScheduledJob прав не имеют (метод/РЗ вызывает платформа).
if [[ "$HAS_RIGHTS" -eq 1 ]]; then
    roles_found=$(grep -rln "$OBJ_TYPE_SG\.$OBJ_NAME" "$SRC_ROOT/Roles/" 2>/dev/null | head -1)
    if [[ -n "$roles_found" ]]; then
        ok "X7 (№532): объект найден в роли: $(basename "$(dirname "$roles_found")")"
    else
        fail "X7 (№532): '$OBJ_TYPE_SG.$OBJ_NAME' не найден ни в одной роли — объект невидим пользователям"
    fi

    # §7 X7b: подсистема. Для не-UI типов (HAS_UI=0) отсутствие подсистемы нормально —
    # INFO, не WARN (см. комментарий к HAS_UI выше).
    subs_found=$(grep -rln "$OBJ_TYPE_SG\.$OBJ_NAME" "$SRC_ROOT/Subsystems/" 2>/dev/null | head -1)
    if [[ -n "$subs_found" ]]; then
        ok "X7: объект включён в подсистему: $(basename "$(dirname "$subs_found")")"
    elif [[ "$HAS_UI" -eq 1 ]]; then
        warn "X7: объект не включён ни в одну подсистему (может быть не виден в интерфейсе)"
    else
        info "X7: объект не в подсистеме — для типа '$OBJ_TYPE_SG' это нормально (служебный/интеграционный)"
    fi
elif [[ -n "$OBJ_TYPE_SG" ]]; then
    info "X7 (№532): пропущено для '$OBJ_TYPE_SG' — нет объектных прав"
fi

# §7 X8 (ScheduledJob): <methodName>CommonModule.Имя.Метод</methodName> ↔ реальный
# Экспорт-метод в Module.bsl общего модуля. Опечатка/отсутствие метода → РЗ молча
# не запускается. Прямой аналог X1 (реквизиты↔формы), но для обработчика РЗ.
# (G1 3-го полевого теста: раньше ScheduledJob не распознавался, methodName не проверялся.)
if [[ "$OBJ_TYPE_SG" == "ScheduledJob" ]] && [[ -n "$MDO" ]]; then
    mn=$(grep -oE '<methodName>[^<]+</methodName>' "$MDO" | sed -E 's/<\/?methodName>//g' | head -1)
    if [[ -z "$mn" ]]; then
        fail "X8: нет <methodName> в .mdo — РЗ без обработчика не запустится"
    else
        # sed -E (ERE): переносимо между GNU и BSD/macOS (BRE '\?'/'\+' на mac не работают).
        mod_name=$(echo "$mn"   | sed -nE 's/^CommonModule\.([^.]+)\..*$/\1/p')
        meth_name=$(echo "$mn" | sed -nE 's/^CommonModule\.[^.]+\.(.*)$/\1/p')
        mod_bsl="$SRC_ROOT/CommonModules/$mod_name/Module.bsl"
        if [[ -z "$mod_name" ]] || [[ -z "$meth_name" ]]; then
            warn "X8: methodName '$mn' не вида 'CommonModule.Имя.Метод' — сверьте вручную"
        elif [[ ! -f "$mod_bsl" ]]; then
            fail "X8: '$mn' → общий модуль CommonModules/$mod_name/Module.bsl не найден"
        elif ! grep -qE "(Процедура|Функция)[[:space:]]+$meth_name[[:space:]]*\(" "$mod_bsl"; then
            fail "X8: метод '$meth_name' не определён в $mod_bsl (methodName '$mn' битый)"
        elif ! grep -qE "(Процедура|Функция)[[:space:]]+$meth_name[[:space:]]*\([^)]*\)[[:space:]]*Экспорт" "$mod_bsl"; then
            warn "X8: метод '$meth_name' найден, но без 'Экспорт' — РЗ его не вызовет"
        else
            ok "X8: methodName '$mn' → метод '$meth_name' существует и Экспорт"
        fi
    fi
fi

# §7 X9 (ScheduledJob): расписание. При predefined=true рядом с .mdo должен лежать
# Schedule.schedule; иначе у РЗ нет расписания по умолчанию (№539/№402).
if [[ "$OBJ_TYPE_SG" == "ScheduledJob" ]] && [[ -n "$MDO" ]]; then
    if grep -q '<predefined>true</predefined>' "$MDO"; then
        if [[ -f "$OBJ_PATH/Schedule.schedule" ]]; then
            ok "X9: Schedule.schedule присутствует (predefined=true)"
        else
            warn "X9: predefined=true, но Schedule.schedule не найден рядом с .mdo"
        fi
    else
        info "X9: predefined не задан — расписание настраивается интерактивно"
    fi
fi

# §7 X12 (EventSubscription): <handler>CommonModule.Имя.Метод</handler> ↔ реальный
# Экспорт-метод в Module.bsl общего модуля. Опечатка/отсутствие модуля или метода →
# подписка молча не сработает (событие уйдёт в несуществующий/неэкспортный обработчик).
# Прямой аналог X8 (methodName РЗ). (4-й полевой тест, кейс G: EventSubscription
# вообще не распознавался, handler не проверялся.)
if [[ "$OBJ_TYPE_SG" == "EventSubscription" ]] && [[ -n "$MDO" ]]; then
    hn=$(grep -oE '<handler>[^<]+</handler>' "$MDO" | sed -E 's/<\/?handler>//g' | head -1)
    if [[ -z "$hn" ]]; then
        fail "X12: нет <handler> в .mdo — подписка без обработчика не сработает"
    else
        # sed -E (ERE): переносимо между GNU и BSD/macOS (как в X8).
        mod_name=$(echo "$hn"   | sed -nE 's/^CommonModule\.([^.]+)\..*$/\1/p')
        meth_name=$(echo "$hn" | sed -nE 's/^CommonModule\.[^.]+\.(.*)$/\1/p')
        mod_bsl="$SRC_ROOT/CommonModules/$mod_name/Module.bsl"
        if [[ -z "$mod_name" ]] || [[ -z "$meth_name" ]]; then
            warn "X12: handler '$hn' не вида 'CommonModule.Имя.Метод' — сверьте вручную"
        elif [[ ! -f "$mod_bsl" ]]; then
            fail "X12: '$hn' → общий модуль CommonModules/$mod_name/Module.bsl не найден"
        elif ! grep -qE "(Процедура|Функция)[[:space:]]+$meth_name[[:space:]]*\(" "$mod_bsl"; then
            fail "X12: метод '$meth_name' не определён в $mod_bsl (handler '$hn' битый)"
        elif ! grep -qE "(Процедура|Функция)[[:space:]]+$meth_name[[:space:]]*\([^)]*\)[[:space:]]*Экспорт" "$mod_bsl"; then
            warn "X12: метод '$meth_name' найден, но без 'Экспорт' — подписка его не вызовет"
        else
            ok "X12: handler '$hn' → метод '$meth_name' существует и Экспорт"
        fi
    fi
fi

# §7 X13 (Report): <mainDataCompositionSchema> в .mdo ↔ реальный шаблон СКД
# (Templates/<Имя>/Template.dcs) с templateType=DataCompositionSchema. Опечатка в
# имени шаблона или отсутствие файла схемы → отчёт падает при открытии/формировании.
# Прямой аналог X8 (methodName РЗ) / X12 (handler подписки), но для схемы компоновки.
# (5-й полевой тест, кейс B: Report не распознавался, схема не проверялась.)
if [[ "$OBJ_TYPE_SG" == "Report" ]] && [[ -n "$MDO" ]]; then
    mdc=$(grep -oE '<mainDataCompositionSchema>[^<]+</mainDataCompositionSchema>' "$MDO" \
          | sed -E 's/<\/?mainDataCompositionSchema>//g' | head -1)
    if [[ -z "$mdc" ]]; then
        # Допустимо: схема задаётся программно или отчёт без СКД (только обработчики).
        info "X13: нет <mainDataCompositionSchema> в .mdo — отчёт без схемы СКД"
    else
        # Report.торо_Имя.Template.ИмяШаблона → ИмяШаблона
        tpl_name=$(echo "$mdc" | sed -nE 's/^.*\.Template\.([^./]+).*$/\1/p')
        if [[ -z "$tpl_name" ]]; then
            warn "X13: mainDataCompositionSchema '$mdc' не вида '...Template.Имя' — сверьте вручную"
        elif ! grep -q "<name>$tpl_name</name>" "$MDO" \
           || ! grep -q '<templateType>DataCompositionSchema</templateType>' "$MDO"; then
            fail "X13: шаблон '$tpl_name' не объявлен в .mdo (<templates>) или не DataCompositionSchema"
        else
            dcs="$OBJ_PATH/Templates/$tpl_name/Template.dcs"
            if [[ -f "$dcs" ]]; then
                ok "X13: mainDataCompositionSchema '$mdc' → шаблон '$tpl_name' (Template.dcs найден)"
            else
                fail "X13: шаблон '$tpl_name' объявлен, но Templates/$tpl_name/Template.dcs не найден"
            fi
        fi
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
if [[ "$TYPE_UNKNOWN" -eq 1 ]]; then
    echo "⚠️  ВНИМАНИЕ: тип '$OBJ_TYPE_DIR' не распознан скриптом."
    echo "   Кросс-файловые проверки §7 (X6 регистрация, X7 роли, X8/X12 обработчики) ПРОПУЩЕНЫ."
    echo "   Не доверяйте сообщению «все проверки пройдены» — сверьте объект вручную и"
    echo "   обязательно проверьте в EDT."
    echo "   Поддерживаемые типы: Catalog, Document, DataProcessor, InformationRegister,"
    echo "   AccumulationRegister, Constant, ChartOfCharacteristicTypes, CommonModule,"
    echo "   ScheduledJob, HTTPService, WebService, EventSubscription, Report."
fi
echo "✅ Все критичные проверки пройдены."
echo "   ⚠️  Скрипт НЕ заменяет EDT-валидацию — импортируйте объект в EDT и проверьте,"
echo "   что форма открывается визуально."
exit 0
