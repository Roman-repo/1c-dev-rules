#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
metadata_scan.py — детерминированный metadata-слой применения пакета правил
checkbsl (docs.checkbsl.org/checks/metadata/) к XML метаданных EDT-выгрузки
(.mdo/.rights): третий слой этапа «Код ревью» (1c-code-review), перенос
проверок validate-new-object.sh (MVP-задача META-001 эпика EP-001, ADR-001:
отдельный слой — источник нового типа, не текст .bsl и не AST модулей).

Зачем: 32 ключа каталога секции metadata не видны ни regex-сканеру (.bsl),
ни BSL LS (AST модулей) — покрыто 4 из 32; проверки новых объектов жили в
bash validate-new-object.sh без ключей каталога, № стандартов и подавлений.
Слой читает XML метаданных из диффа/каталога и выдаёт находки «ключ каталога
или локальный ключ + № стандарта + что не так + как правильно» (fixes — общая
база scripts/bsl_ls_fixes.json). Формы (.form) — вне рамок EP-001: формовые
цепочки (F3/F4/F10, X1–X4, X15) остаются в validate-new-object.sh.

Использование:
    python3 scripts/metadata_scan.py ФАЙЛ|КАТАЛОГ [...]           # md-отчёт
    python3 scripts/metadata_scan.py . --format json              # машиночитаемо
    python3 scripts/metadata_scan.py --diff main                  # объекты из диффа
    python3 scripts/metadata_scan.py src/Catalogs/Объект --src-root src
    python3 scripts/metadata_scan.py . --suppress suppress.json   # «не баг» из 05a
    python3 scripts/metadata_scan.py --coverage-report            # metadata: N/32

Выход: 0 — 🔴 нет (🟡/🟢 допустимы), 1 — есть 🔴, 2 — ошибка использования
(нет входа, путь не найден, git недоступен, битый suppress.json). Битый XML
отдельного файла — WARN-находка (🟢), остальные файлы проверяются; молчаливого
«всё ок» слой не даёт. Без зависимостей: только стандартная библиотека, Java
не нужна. Подавления — общий контур review_suppress.py (suppress.json,
решения Ревьюера «не баг»): подавленная находка не выводится, но видна
счётчиком — подавление не молчаливое.

Потолок/счётчик: --coverage-report печатает каталогные metadata-ключи слоя
(N/32, источник — skills/1c-code-review/references/checkbsl/metadata.md);
вливание слоя в общий счётчик обёртки bsl_ls_analyze.py — задача META-002.
"""
from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Set, Tuple

sys.path.insert(0, str(Path(__file__).resolve().parent))
from checkbsl_scan import load_suppress, suppression_for  # noqa: E402

SCRIPTS_DIR = Path(__file__).resolve().parent
CATALOG_MD = (SCRIPTS_DIR.parent / "skills" / "1c-code-review" / "references"
              / "checkbsl" / "metadata.md")
FIXES_JSON = SCRIPTS_DIR / "bsl_ls_fixes.json"
DCS_SCHEMA_NS = "{http://v8.1c.ru/8.1/data-composition-system/schema}"

SEV_MARK = {"red": "🔴", "yellow": "🟡", "green": "🟢"}

# --- правила -------------------------------------------------------------------
#
# Поля MetaRule:
#   key    — ключ каталога checkbsl (kind="catalog") или локальный структурный
#            ключ (kind="local", наследие X/M-цепочек bash — в каталоге их нет)
#   sev    — red 🔴 / yellow 🟡 / green 🟢 (приоритет — чек-лист 1c-code-review
#            и стандарт; отдельные случаи в проверках могут понижать серьёзность)
#   title  — формулировка для отчёта
#   std    — № стандарта v8std, где установлен
#   scope  — object | config: к чему применимо правило
#   src    — требует --src-root (кросс-файловые: роли, подсистемы, UUID проекта)


@dataclass(frozen=True)
class MetaRule:
    key: str
    sev: str
    title: str
    kind: str = "catalog"
    std: str = ""
    scope: str = "object"
    src: bool = False


CHECKS: List[MetaRule] = [
    # — каталогные (docs.checkbsl.org/checks/metadata/) —
    MetaRule("MDOWithoutSynonym", "red", "У объекта метаданных нет синонима ru",
             std="474"),
    MetaRule("YOInMetadata", "yellow", "Буква «ё» в имени/синониме/комментарии"),
    MetaRule("MetadataNameLongerThan", "yellow", "Имя объекта метаданных длиннее 80 символов"),
    # WrongSynonym сужен до бесспорного случая (цифра в начале): буквенный
    # регистр по букве каталога («не с прописной») противоречит №474 и практике
    # типовых конфигураций — дал бы ложняк на каждый объект (живой прогон 0.31)
    MetaRule("WrongSynonym", "yellow", "Синоним начинается с цифры"),
    MetaRule("MetadataSynonym", "green", "Синоним не соответствует имени (проверьте осмысленность)"),
    MetaRule("SameMetadataNames", "yellow", "Одинаковое имя у объекта и его табличной части"),
    MetaRule("HidingPasswordInMetadata", "red", "Строковый реквизит пароля без скрытия (passwordMode)", std="740"),
    MetaRule("ExtensionMetadataWithoutPrefix", "yellow", "Объект расширения без префикса расширения"),
    MetaRule("ConfigurationWithoutSynonym", "red", "У конфигурации нет синонима ru",
             scope="config", std="474"),
    MetaRule("BriefInformation", "yellow", "Краткая информация отличается от синонима", scope="config"),
    MetaRule("DetailedInformation", "yellow", "Подробная информация отличается от синонима", scope="config"),
    MetaRule("Copyright", "yellow", "Не заполнены авторские права", scope="config"),
    MetaRule("ConfigurationVersion", "yellow", "Синоним конфигурации не оканчивается на номер редакции", scope="config"),
    MetaRule("InvalidConfigurationName", "yellow", "Имя конфигурации содержит «редакция»/«подредакция»", scope="config"),
    MetaRule("MissingStandardRole", "red", "Нет обязательной роли (ПолныеПрава/АдминистраторСистемы/ИнтерактивноеОткрытиеВнешнихОтчетовИОбработок)", scope="config"),
    MetaRule("StandardRolesNotInDefaults", "yellow", "Обязательная роль не в основных ролях конфигурации", scope="config"),
    # — локальные структурные (перенос X/M-цепочек validate-new-object.sh) —
    MetaRule("DuplicateUUIDInMDO", "red", "Дубликаты UUID внутри .mdo (M1)", kind="local"),
    MetaRule("UUIDCollisionProject", "red", "UUID объекта найден более чем в одном файле проекта (правило 12 AGENTS.md)", kind="local", src=True),
    MetaRule("AccumRegisterNoResource", "red", "Регистр накопления без ресурсов (M12)", kind="local"),
    MetaRule("AccumRegisterNoDimension", "red", "Регистр накопления без измерений (M12)", kind="local"),
    MetaRule("AccumRegisterTurnoversHint", "yellow", "Регистр оборотов: остатки через .Остатки() недоступны (M12)", kind="local"),
    MetaRule("ObjectNotInConfiguration", "red", "Объект не зарегистрирован в Configuration.mdo (X6)", kind="local", src=True),
    MetaRule("ObjectWithoutRole", "red", "Объект не найден ни в одной роли — невидим пользователям (X7)", kind="local", std="532", src=True),
    MetaRule("ObjectWithoutSubsystem", "yellow", "Объект не включён ни в одну подсистему — может быть не виден в интерфейсе (X7b)", kind="local", src=True),
    MetaRule("ScheduledJobHandlerMissing", "red", "methodName регламентного задания не разрешается в экспортный метод (X8)", kind="local"),
    MetaRule("ScheduledJobNoSchedule", "yellow", "predefined=true без файла расписания Schedule.schedule (X9)", kind="local", std="539"),
    MetaRule("EventSubscriptionHandlerMissing", "red", "handler подписки на событие не разрешается в экспортный метод (X12)", kind="local"),
    MetaRule("ReportSchemaMissing", "red", "mainDataCompositionSchema не разрешается в шаблон СКД (X13)", kind="local"),
    MetaRule("DCSParamNotDeclared", "red", "Параметр запроса СКД не объявлен в <parameter> схемы (X14)", kind="local"),
    MetaRule("QueryParamMismatch", "red", "Рассогласование параметров запроса &X ↔ УстановитьПараметр (X5)", kind="local"),
    MetaRule("ChangeAndCallNoResume", "red", "&ИзменениеИКонтроль без ПродолжитьВызов() — оригинал не выполнится (X11)", kind="local"),
    # — служебное уведомление (отклонение 2а сценария 02) —
    MetaRule("LocalTypeUnknown", "green", "Тип объекта не распознан — кросс-файловые проверки пропущены", kind="local"),
]

RULES_BY_KEY = {r.key: r for r in CHECKS}

# Таблица типов: каталог выгрузки → тип (bash-наследие; HAS_RIGHTS/HAS_UI
# купируют ложняки 3–4 полевых тестов — РЗ/общие модули прав не имеют и т.п.)
DIR_TYPES = {
    "Catalogs": "Catalog", "Documents": "Document",
    "DataProcessors": "DataProcessor", "InformationRegisters": "InformationRegister",
    "AccumulationRegisters": "AccumulationRegister", "Constants": "Constant",
    "ChartsOfCharacteristicTypes": "ChartOfCharacteristicTypes",
    "CommonModules": "CommonModule", "ScheduledJobs": "ScheduledJob",
    "HTTPServices": "HTTPService", "WebServices": "WebService",
    "EventSubscriptions": "EventSubscription", "Reports": "Report",
}
HAS_RIGHTS = {"Catalog", "Document", "DataProcessor", "InformationRegister",
              "AccumulationRegister", "Constant", "ChartOfCharacteristicTypes",
              "HTTPService", "WebService", "Report"}
HAS_UI = {"Catalog", "Document", "DataProcessor",
          "ChartOfCharacteristicTypes", "Report"}
STANDARD_ROLES = ("ПолныеПрава", "АдминистраторСистемы",
                  "ИнтерактивноеОткрытиеВнешнихОтчетовИОбработок")
UUID_RE = re.compile(r"\b[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}"
                     r"-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}\b")
MDREF_RE = re.compile(r"\b(?:Catalog|Document|DataProcessor|InformationRegister"
                          r"|AccumulationRegister|Constant|ChartOfCharacteristicTypes"
                          r"|Report|CommonModule|ScheduledJob|HTTPService|WebService"
                          r"|EventSubscription)\.[\wЁёА-Яа-я.-]+")
DIRECTIVES_RE = re.compile(
    r"^(НаКлиенте|НаСервере|НаСервереБезКонтекста|НаКлиентеНаСервере|НаКлиентеНаСервереБезКонтекста"
    r"|НаКлиентеНаСервереБезВозвратаНаКлиента|НаКлиентеНаСервереБезКонтекстаВозвратНаКлиента"
    r"|Перед|После|Вместо|ИзменениеИКонтроль)$")


# --- находка -------------------------------------------------------------------


@dataclass
class Finding:
    key: str
    sev: str
    title: str
    std: str
    kind: str
    file: str
    line: int
    fragment: str
    detail: str = ""

    def as_dict(self) -> dict:
        return {
            "key": self.key, "severity": self.sev, "title": self.title,
            "std": self.std or None, "kind": self.kind,
            "file": self.file, "line": self.line, "fragment": self.fragment,
            "detail": self.detail or None,
            "catalog": (f"https://docs.checkbsl.org/checks/metadata/{self.key}/"
                        if self.kind == "catalog" else None),
        }


def _rule_finding(rule_key: str, obj: "MdoObject", needle: str = "",
                  detail: str = "", sev: Optional[str] = None,
                  path: Optional[Path] = None) -> Finding:
    rule = RULES_BY_KEY[rule_key]
    file = path if path is not None else obj.path
    return Finding(rule.key, sev or rule.sev, rule.title, rule.std, rule.kind,
                   str(file), line_of(read_text(file), needle) if needle else 1,
                   (needle or Path(file).name)[:80], detail)


# --- XML-хелперы (формат EDT: корень в namespace mdclass, дети — без; матчим
#     по локальному имени — работает и в старом формате без namespace) ---------


def L(el: ET.Element) -> str:
    return el.tag.split("}")[-1]


def kids(el: ET.Element, name: str) -> List[ET.Element]:
    return [c for c in el if L(c) == name]


def kid(el: ET.Element, name: str) -> Optional[ET.Element]:
    for c in el:
        if L(c) == name:
            return c
    return None


def find_all(el: ET.Element, name: str) -> List[ET.Element]:
    return [e for e in el.iter() if L(e) == name]


def find_one(el: ET.Element, name: str) -> Optional[ET.Element]:
    for e in el.iter():
        if L(e) == name:
            return e
    return None


def read_text(p: Path) -> str:
    try:
        return p.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""


def line_of(raw: str, needle: str, default: int = 1) -> int:
    """Номер строки с needle (первое вхождение) — ET позиций не хранит."""
    if needle:
        for i, ln in enumerate(raw.splitlines(), start=1):
            if needle in ln:
                return i
    return default


def synonym_values(el: ET.Element) -> Dict[str, str]:
    """{lang: value} по ПРЯМЫМ детям <synonym>-формы (<key>/<value>) —
    синонимы самого объекта, не вложенных реквизитов/форм."""
    out: Dict[str, str] = {}
    for s in kids(el, "synonym"):
        k, v = kid(s, "key"), kid(s, "value")
        if k is not None and v is not None and (k.text or "").strip():
            out[(k.text or "").strip()] = (v.text or "").strip()
    return out


def localized_values(el: ET.Element, tag: str) -> Dict[str, str]:
    return synonym_values_for(el, tag)


def synonym_values_for(el: ET.Element, tag: str) -> Dict[str, str]:
    """{lang: value} для локализуемого тега конфигурации (прямые дети)."""
    out: Dict[str, str] = {}
    for s in kids(el, tag):
        k, v = kid(s, "key"), kid(s, "value")
        if k is not None and v is not None and (k.text or "").strip():
            out[(k.text or "").strip()] = (v.text or "").strip()
    return out


def uuids_of(root: ET.Element) -> List[str]:
    """Все uuid/typeId/valueTypeId объекта (атрибуты элементов)."""
    out: List[str] = []
    for el in root.iter():
        for attr in ("uuid", "typeId", "valueTypeId"):
            v = el.attrib.get(attr)
            if v:
                out.append(v.lower())
    return out


# --- объект --------------------------------------------------------------------


@dataclass
class MdoObject:
    path: Path
    root: Optional[ET.Element]  # None = XML не разобран (битый → WARN)
    raw: str
    mtype: str   # Catalog/…/Configuration/"" (не распознан)
    name: str
    dir: Path

    @property
    def is_config(self) -> bool:
        return self.mtype == "Configuration"

    @property
    def token(self) -> str:
        return f"{self.mtype}.{self.name}"


class Context:
    """Ленивые кросс-файловые индексы по src_root (один проход — все объекты)."""

    def __init__(self, src_root: Optional[Path], objects: List[MdoObject]):
        self.src_root = src_root
        self.objects = objects
        self._uuid_files: Optional[Dict[str, Set[Path]]] = None
        self._rights_tokens: Optional[Set[str]] = None
        self._subsystem_hits: Optional[Set[str]] = None
        self._config_text: Optional[str] = None
        self.skipped: List[str] = []  # имена пропущенных кросс-файловых проверок
        if src_root is None:
            self.note_skip("кросс-файловые проверки (--src-root не передан)")

    def note_skip(self, what: str) -> None:
        if what not in self.skipped:
            self.skipped.append(what)

    @property
    def config_text(self) -> str:
        if self._config_text is None:
            cfg = None
            if self.src_root:
                cfg = self.src_root / "Configuration" / "Configuration.mdo"
            if cfg and cfg.is_file():
                self._config_text = read_text(cfg)
            else:
                self._config_text = ""
                self.note_skip("Configuration.mdo (нет --src-root или файла)")
        return self._config_text

    def _prime(self) -> None:
        """ОДИН проход по src: uuid→файлы + токены ролей + ссылки подсистем.
        Раздельные обходы стоили ~9,6 с на дереве toir2 (2627 модулей) —
        каждый файл читался до трёх раз; один проход ≈4-5 с (НФТ 03: <5 с)."""
        if self._uuid_files is not None:
            return
        self._uuid_files = {}
        self._rights_tokens = set()
        self._subsystem_hits = set()
        if not (self.src_root and self.src_root.is_dir()):
            self.note_skip("UUID-коллизии, роли, подсистемы (--src-root не передан)")
            return
        rx_bytes = re.compile(UUID_RE.pattern.encode())
        have_roles = have_subs = False
        for p in sorted(self.src_root.rglob("*")):
            if not p.is_file() or p.suffix not in (".mdo", ".form", ".rights"):
                continue
            try:
                data = p.read_bytes()
            except OSError:
                continue
            for uu in rx_bytes.findall(data):
                self._uuid_files.setdefault(uu.decode().lower(), set()).add(p)
            rel = p.relative_to(self.src_root)
            if rel.parts and rel.parts[0] == "Roles":
                have_roles = True
                self._rights_tokens |= set(MDREF_RE.findall(data.decode("utf-8", "ignore")))
            elif rel.parts and rel.parts[0] == "Subsystems":
                have_subs = True
                self._subsystem_hits |= set(MDREF_RE.findall(data.decode("utf-8", "ignore")))
        if not have_roles:
            self.note_skip("роли (Roles/ недоступен)")
        if not have_subs:
            self.note_skip("подсистемы (Subsystems/ недоступен)")

    @property
    def rights_tokens(self) -> Set[str]:
        """Ссылки на объекты/поля из Rights.rights — текстовый поиск MDRef-
        токенов (семантика grep X7 в bash: любой тег, не только <name>)."""
        self._prime()
        return self._rights_tokens

    @property
    def subsystem_hits(self) -> Set[str]:
        """Токены объектов, упомянутые в .mdo подсистем."""
        self._prime()
        return self._subsystem_hits

    @property
    def uuid_files(self) -> Dict[str, Set[Path]]:
        self._prime()
        return self._uuid_files

    def common_module_bsl(self, mod_name: str) -> Optional[Path]:
        if not self.src_root:
            return None
        p = self.src_root / "CommonModules" / mod_name / "Module.bsl"
        return p if p.is_file() else None


# --- проверки ------------------------------------------------------------------


def norm_ident(text: str) -> str:
    """Нормализация для сравнения «имя ↔ синоним»: только буквы/цифры, lower."""
    return re.sub(r"[^0-9a-zа-яё]", "", text.lower())


def check_object_catalog(obj: MdoObject, ctx: Context) -> List[Finding]:
    if obj.root is None or obj.is_config:
        return []
    out: List[Finding] = []
    props = obj.root  # новый формат: name/synonym — дети корня
    syns = synonym_values(props)
    name = obj.name

    if not syns.get("ru"):
        out.append(_rule_finding("MDOWithoutSynonym", obj, "<key>ru</key>",
                                 "синоним ru отсутствует или пуст — объект "
                                 "появится в интерфейсе служебным именем"))
    for lang, val in syns.items():
        if "ё" in val or "Ё" in val:
            out.append(_rule_finding(
                "YOInMetadata", obj, f"<value>{val[:40]}",
                f"«ё» в синониме ({lang}): \"{val[:60]}\" — ломает обмены/выгрузку"))
    if "ё" in name or "Ё" in name:
        out.append(_rule_finding("YOInMetadata", obj, f"<name>{name[:40]}",
                                 f"«ё» в имени объекта: {name}"))
    comment = kid(props, "comment")
    if comment is not None and comment.text and ("ё" in comment.text or "Ё" in comment.text):
        out.append(_rule_finding("YOInMetadata", obj, "<comment>",
                                 "«ё» в комментарии объекта"))
    if "ё" in obj.dir.name or "Ё" in obj.dir.name:
        out.append(_rule_finding("YOInMetadata", obj, "", "«ё» в имени каталога объекта",
                                 path=obj.dir))

    if len(name) > 80:
        out.append(_rule_finding("MetadataNameLongerThan", obj, f"<name>{name[:40]}",
                                 f"длина имени {len(name)} > 80 символов"))
    for lang, val in syns.items():
        if val and val[0].isdigit():
            out.append(_rule_finding("WrongSynonym", obj, f"<value>{val[:40]}",
                                     f"синоним ({lang}) начинается с цифры: \"{val[:60]}\""))
    if syns.get("ru") and norm_ident(name) and norm_ident(syns["ru"]) \
            and norm_ident(name) != norm_ident(syns["ru"]):
        out.append(_rule_finding(
            "MetadataSynonym", obj, f"<value>{syns['ru'][:40]}",
            f"имя \"{name}\" не строится из синонима \"{syns['ru'][:60]}\" — "
            "проверьте осмысленность пары", sev="green"))

    for ts in kids(props, "tabularSections"):
        ts_name = kid(ts, "name")
        if ts_name is not None and ts_name.text == name:
            out.append(_rule_finding(
                "SameMetadataNames", obj, f"<name>{name}</name>",
                f"табличная часть «{name}» совпадает с именем объекта — "
                "неоднозначность в запросах"))

    for a in find_all(props, "attributes"):
        an = kid(a, "name")
        aname = (an.text or "").strip() if an is not None else ""
        if not re.search(r"Пароль|Password", aname, re.IGNORECASE):
            continue
        pm = kid(a, "passwordMode")
        if pm is not None and (pm.text or "").strip().lower() == "true":
            continue
        t = kid(a, "type")
        types = " ".join((x.text or "") for x in find_all(t, "types")) if t is not None else ""
        if "Строка" in types or "String" in types:
            out.append(_rule_finding(
                "HidingPasswordInMetadata", obj, f"<name>{aname}</name>",
                f"реквизит «{aname}» типа Строка без passwordMode — пароль "
                "виден на экране (№740)"))

    parts = obj.path.parts
    if "Ext" in parts:  # выгрузка расширения: Ext/<ИмяРасширения>/src/…
        i = parts.index("Ext")
        if i + 1 < len(parts) and parts[i + 1] != "src":
            ext_prefix = parts[i + 1].split("_")[0]
            if ext_prefix and not name.startswith(ext_prefix):
                out.append(_rule_finding(
                    "ExtensionMetadataWithoutPrefix", obj, f"<name>{name[:40]}",
                    f"объект расширения «{parts[i + 1]}» без префикса "
                    f"«{ext_prefix}» (требование 8.5 1С:Совместимо)"))
    return out


def check_config(obj: MdoObject, ctx: Context) -> List[Finding]:
    if obj.root is None or not obj.is_config:
        return []
    out: List[Finding] = []
    syns = synonym_values(obj.root)
    name = obj.name

    if not syns.get("ru"):
        out.append(_rule_finding("ConfigurationWithoutSynonym", obj, "<key>ru</key>",
                                 "синоним конфигурации ru отсутствует — синонимы "
                                 "формируют пользовательский интерфейс (№474)"))
    brief = localized_values(obj.root, "briefInformation")
    detailed = localized_values(obj.root, "detailedInformation")
    copyright_ = localized_values(obj.root, "copyright")
    for lang, sv in syns.items():
        if lang in brief and brief[lang] and brief[lang] != sv:
            out.append(_rule_finding("BriefInformation", obj, "<briefInformation>",
                                     f"краткая информация ({lang}) ≠ синониму"))
        if lang in detailed and detailed[lang] and detailed[lang] != sv:
            out.append(_rule_finding("DetailedInformation", obj, "<detailedInformation>",
                                     f"подробная информация ({lang}) ≠ синониму"))
        if lang not in copyright_ or not copyright_.get(lang):
            out.append(_rule_finding("Copyright", obj, "<copyright>",
                                     f"авторские права не заполнены ({lang})"))
    if syns.get("ru") and not re.search(r"редакци\w*\s*[\d.]+|подредакци\w*\s*[\d.]+",
                                        syns["ru"], re.IGNORECASE):
        out.append(_rule_finding(
            "ConfigurationVersion", obj, f"<value>{syns['ru'][:40]}",
            f"синоним не оканчивается на номер редакции: «{syns['ru'][:70]}»"))
    if re.search(r"редакция|подредакция", name, re.IGNORECASE):
        out.append(_rule_finding("InvalidConfigurationName", obj, f"<name>{name[:40]}",
                                 "имя конфигурации содержит «редакция»/«подредакция»"))

    roles = {(e.text or "").strip().split(".", 1)[-1]
             for e in find_all(obj.root, "roles") if e.text}
    defaults = {(e.text or "").strip().split(".", 1)[-1]
                for e in find_all(obj.root, "defaultRoles") if e.text}
    for r in STANDARD_ROLES:
        if r not in roles:
            out.append(_rule_finding("MissingStandardRole", obj, "<roles>",
                                     f"обязательная роль «{r}» не определена "
                                     "в конфигурации (п. 2.6.1 1С:Совместимо)"))
        elif r not in defaults:
            out.append(_rule_finding("StandardRolesNotInDefaults", obj, "<defaultRoles>",
                                     f"роль «{r}» не в основных ролях конфигурации"))
    return out


def check_structural(obj: MdoObject, ctx: Context) -> List[Finding]:
    """Локальные структурные проверки — перенос M1/M12/правило-12/X5–X14 из bash."""
    if obj.root is None:
        return []
    out: List[Finding] = []

    # M1: дубликаты UUID внутри .mdo
    seen: Dict[str, int] = {}
    for u in uuids_of(obj.root):
        seen[u] = seen.get(u, 0) + 1
    for u, cnt in seen.items():
        if cnt > 1:
            out.append(_rule_finding("DuplicateUUIDInMDO", obj, u,
                                     f"UUID {u} встречается {cnt} раз в одном .mdo"))

    # правило 12 AGENTS.md: UUID уникален по проекту (кросс-файловый)
    if ctx.src_root:
        idx = ctx.uuid_files
        for u in dict.fromkeys(uuids_of(obj.root)):
            files = idx.get(u, set())
            if len(files) > 1:
                others = ", ".join(str(f.name) for f in sorted(files)
                                   if f != obj.path)[:120]
                out.append(_rule_finding(
                    "UUIDCollisionProject", obj, u,
                    f"UUID {u} найден в {len(files)} файлах ({others})"))

    # M12: регистры накопления
    if obj.mtype == "AccumulationRegister":
        for tag, key in (("resources", "AccumRegisterNoResource"),
                         ("dimensions", "AccumRegisterNoDimension")):
            if not kids(obj.root, tag):
                out.append(_rule_finding(key, obj, f"<{tag}>",
                                         f"регистр без <{tag}> некорректен"))
        rt = kid(obj.root, "registerType")
        if rt is not None and (rt.text or "").strip() == "Turnovers":
            out.append(_rule_finding(
                "AccumRegisterTurnoversHint", obj, "<registerType>",
                "регистр оборотов: контроль остатков через .Остатки() НЕЛЬЗЯ "
                "(нужен Balance/BalanceAndTurnovers)"))

    # X6: регистрация в Configuration.mdo
    if obj.mtype and not obj.is_config and ctx.src_root:
        if obj.token not in ctx.config_text:
            out.append(_rule_finding(
                "ObjectNotInConfiguration", obj, f"<name>{obj.name[:40]}",
                f"«{obj.token}» не найден в Configuration.mdo"))

    # X7/X7b: роли и подсистемы
    if obj.mtype in HAS_RIGHTS and ctx.src_root:
        if obj.token not in ctx.rights_tokens:
            out.append(_rule_finding(
                "ObjectWithoutRole", obj, f"<name>{obj.name[:40]}",
                f"«{obj.token}» не найден ни в одной роли Roles/ — объект "
                "невидим пользователям (№532)"))
    if obj.mtype in HAS_UI and ctx.src_root:
        if obj.token not in ctx.subsystem_hits:
            out.append(_rule_finding(
                "ObjectWithoutSubsystem", obj, f"<name>{obj.name[:40]}",
                f"«{obj.token}» не включён ни в одну подсистему — может быть "
                "не виден в интерфейсе"))

    # X8/X12: methodName РЗ / handler подписки ↔ экспортный метод модуля
    for tag, key, what in (("methodName", "ScheduledJobHandlerMissing", "РЗ"),
                           ("handler", "EventSubscriptionHandlerMissing", "подписка")):
        expected_type = "ScheduledJob" if tag == "methodName" else "EventSubscription"
        if obj.mtype != expected_type:
            continue
        h = kid(obj.root, tag)
        if h is None or not (h.text or "").strip():
            out.append(_rule_finding(
                key, obj, f"<{tag}>",
                f"нет <{tag}> — {what} молча не сработает"))
            continue
        ref = h.text.strip()
        m = re.match(r"^CommonModule\.([^.]+)\.(.+)$", ref)
        if not m:
            out.append(_rule_finding(key, obj, f"<{tag}>",
                                     f"{tag} «{ref}» не вида CommonModule.Имя.Метод",
                                     sev="yellow"))
            continue
        mod_name, meth = m.group(1), m.group(2)
        bsl = ctx.common_module_bsl(mod_name)
        if bsl is None:
            out.append(_rule_finding(
                key, obj, f"<{tag}>",
                f"«{ref}»: общий модуль CommonModules/{mod_name}/Module.bsl не найден"))
            continue
        text = read_text(bsl)
        if not re.search(r"(Процедура|Функция)\s+" + re.escape(meth) + r"\s*\(", text):
            out.append(_rule_finding(
                key, obj, f"<{tag}>",
                f"«{ref}»: метод «{meth}» не определён в модуле {mod_name}"))
        elif not re.search(r"(Процедура|Функция)\s+" + re.escape(meth)
                           + r"\s*\([^)]*\)\s*Экспорт", text):
            out.append(_rule_finding(
                key, obj, f"<{tag}>",
                f"«{ref}»: метод «{meth}» без «Экспорт» — {what} его не вызовет",
                sev="yellow"))

    # X9: расписание РЗ
    if obj.mtype == "ScheduledJob":
        pre = kid(obj.root, "predefined")
        if pre is not None and (pre.text or "").strip().lower() == "true":
            if not (obj.dir / "Schedule.schedule").is_file():
                out.append(_rule_finding(
                    "ScheduledJobNoSchedule", obj, "<predefined>",
                    "predefined=true, но Schedule.schedule не найден рядом с .mdo"))

    # X13: mainDataCompositionSchema ↔ шаблон СКД
    if obj.mtype == "Report":
        mdc = kid(obj.root, "mainDataCompositionSchema")
        if mdc is not None and (mdc.text or "").strip():
            ref = mdc.text.strip()
            m = re.search(r"\.Template\.([^./]+)", ref)
            if m:
                tpl = m.group(1)
                declared = any(
                    (kid(t, "name") is not None and kid(t, "name").text == tpl
                     and any((x.text or "").strip() == "DataCompositionSchema"
                             for x in find_all(t, "templateType")))
                    for t in kids(obj.root, "templates"))
                if not declared:
                    out.append(_rule_finding(
                        "ReportSchemaMissing", obj, "<mainDataCompositionSchema>",
                        f"шаблон «{tpl}» не объявлен в .mdо или не DataCompositionSchema"))
                elif not (obj.dir / "Templates" / tpl / "Template.dcs").is_file():
                    out.append(_rule_finding(
                        "ReportSchemaMissing", obj, "<mainDataCompositionSchema>",
                        f"шаблон «{tpl}» объявлен, но Templates/{tpl}/Template.dcs "
                        "не найден"))

    # X14: параметры запроса СКД ↔ <parameter> схемы
    if obj.mtype == "Report" and (obj.dir / "Templates").is_dir():
        for dcs in sorted((obj.dir / "Templates").rglob("Template.dcs")):
            out.extend(check_dcs(obj, dcs))

    # X5: &X ↔ УстановитьПараметр в модулях объекта (Report-исключение: СКД)
    out.extend(check_query_params(obj))

    # X11: &ИзменениеИКонтроль без ПродолжитьВызов
    for bsl in object_modules(obj):
        text = read_text(bsl)
        if re.search(r"&ИзменениеИКонтроль\s*\(", text) \
                and not re.search(r"ПродолжитьВызов\s*\(", text):
            out.append(_rule_finding(
                "ChangeAndCallNoResume", obj, "&ИзменениеИКонтроль",
                f"модуль {bsl.name}: есть &ИзменениеИКонтроль(...), но нет "
                "ПродолжитьВызов() — оригинальный метод не выполнится", path=bsl))
    return out


def check_dcs(obj: MdoObject, dcs: Path) -> List[Finding]:
    try:
        root = ET.parse(str(dcs)).getroot()
    except ET.ParseError:
        return [_rule_finding("DCSParamNotDeclared", obj, "", 
                              f"схема {dcs.parent.name} не разобрана — сверьте вручную",
                              sev="green", path=dcs)]
    qparams: Set[str] = set()
    for q in root.iter():
        if L(q) == "query" and q.text:
            qparams |= set(re.findall(r"&([А-Яа-яA-Za-zЁё_]+)", q.text))
    declared: Set[str] = set()
    for p in root:
        if L(p) == "parameter":
            n = kid(p, "name")
            if n is not None and (n.text or "").strip():
                declared.add(n.text.strip())
    out = []
    for x in sorted(qparams - declared):
        out.append(_rule_finding(
            "DCSParamNotDeclared", obj, f"&{x}",
            f"параметр «&{x}» запроса схемы {dcs.parent.name} не объявлен в "
            "<parameter> — настроить будет нельзя", path=dcs))
    return out


def object_modules(obj: MdoObject) -> List[Path]:
    """Модули объекта для текстовых проверок (X5/X11): объектный, менеджера,
    общий Module.bsl + модули форм (Forms/*/Module.bsl — чтение как текст,
    не парсинг .form). Объединение как в X5/X11 bash: запрос с &X часто живёт
    в модуле формы, а УстановитьПараметр — в менеджере (живой прогон toir2:
    торо_ОбъектыРемонта, иначе ложное «рассогласование»)."""
    mods = [obj.dir / n for n in ("ObjectModule.bsl", "ManagerModule.bsl",
                                  "Module.bsl") if (obj.dir / n).is_file()]
    forms = obj.dir / "Forms"
    if forms.is_dir():
        mods.extend(sorted(forms.glob("*/Module.bsl")))
    return mods


def check_query_params(obj: MdoObject) -> List[Finding]:
    if obj.mtype == "Report":  # параметры отчёта связаны через СКД, не через код
        return []
    mods = object_modules(obj)
    if not mods:
        return []
    texts = [read_text(m) for m in mods]
    joined = "\n".join(texts)
    joined = re.sub(r"&&[\wЁё]+", "", joined)  # &&Маркер — подстановка СтрЗаменить
    qparams = {t for t in re.findall(r"&([А-Яа-яA-Za-zЁё_]+)", joined)
               if not DIRECTIVES_RE.match(t)}
    setparams = set(re.findall(r'УстановитьПараметр\("([^"]+)"', joined))
    only_text = sorted(qparams - setparams)
    only_call = sorted(setparams - qparams)
    if not only_text and not only_call:
        return []
    detail = []
    if only_text:
        detail.append("есть в &X, нет в УстановитьПараметр: " + ", ".join(only_text))
    if only_call:
        detail.append("есть в УстановитьПараметр, нет в &X: " + ", ".join(only_call))
    return [_rule_finding("QueryParamMismatch", obj, "УстановитьПараметр",
                          "; ".join(detail))]


# --- сбор объектов ---------------------------------------------------------------


def load_object(mdo_path: Path) -> MdoObject:
    raw = read_text(mdo_path)
    root = None
    try:
        root = ET.fromstring(raw)
    except ET.ParseError:
        pass
    # тип: тег корня (новый формат EDT: mdclass:Catalog); старый формат
    # обёрнут MetaDataObject — тогда каталог выгрузки .../<TypeDir>/<Имя>/
    mtype = L(root) if root is not None else ""
    if mtype not in DIR_TYPES.values() and mtype != "Configuration":
        # старый формат (корень MetaDataObject) или неизвестный тип (Enums и
        # т.п.): берём из каталога выгрузки; не распознали — mtype="" (гейты
        # кросс-файловых проверок молчат, LocalTypeUnknown перечисляет пропущенное)
        from_dir = DIR_TYPES.get(mdo_path.parent.parent.name, "")
        mtype = from_dir if from_dir else ""
    name = ""
    if root is not None:
        n = kid(root, "name")
        name = (n.text or "").strip() if n is not None else ""
    if not name:
        name = mdo_path.parent.name if mdo_path.parent.name == mdo_path.stem \
            else mdo_path.stem
    return MdoObject(mdo_path, root, raw, mtype, name, mdo_path.parent)


def collect_mdo(inputs: List[Path]) -> List[MdoObject]:
    """Объекты метаданных из входов: .mdo-файлы + .mdo внутри каталогов-объектов.
    .rights-входы не создают объектов (пока META-003), но не ошибочны."""
    mdo_files: List[Path] = []
    for p in inputs:
        if p.is_file() and p.suffix == ".mdo":
            mdo_files.append(p)
        elif p.is_dir():
            mdo_files.extend(sorted(p.rglob("*.mdo")))
    seen: Set[Path] = set()
    out: List[MdoObject] = []
    for f in mdo_files:
        if f in seen:
            continue
        seen.add(f)
        out.append(load_object(f))
    return out


def diff_paths(ref: str) -> List[Path]:
    """Изменённые .mdo/.rights относительно git-ссылки → пути объектов."""
    try:
        # core.quotepath=off: git экранирует кириллицу октетами (Ñ…),
        # такого пути не существует — дифф молча терял бы русскоязычные объекты
        res = subprocess.run(["git", "-c", "core.quotepath=off", "diff",
                              "--name-only", "--diff-filter=ACMR",
                              ref, "--", "*.mdo", "*.rights"],
                             capture_output=True, text=True, check=True)
    except (subprocess.CalledProcessError, OSError) as e:
        raise RuntimeError(f"git diff {ref} недоступен: {e}") from e
    out: List[Path] = []
    for line in res.stdout.splitlines():
        line = line.strip()
        if line and (line.endswith(".mdo") or line.endswith(".rights")):
            p = Path(line)
            if p.exists():
                out.append(p)
    return out


def infer_src_root(objects: List[MdoObject], explicit: Optional[Path]) -> Optional[Path]:
    if explicit:
        return explicit
    for o in objects:
        if o.is_config:
            return o.dir.parent  # <src>/Configuration/
        if o.dir.parent.name in DIR_TYPES:  # <src>/<TypeDir>/<Имя>/
            return o.dir.parent.parent
    return None


# --- сканирование ----------------------------------------------------------------


def scan(objects: List[MdoObject], ctx: Context,
         entries: List[dict]) -> Tuple[List[Finding], List[Finding]]:
    findings: List[Finding] = []
    for obj in objects:
        if obj.root is None:
            findings.append(Finding(
                "LocalBrokenXml", "green",
                "XML не разобран — проверки по файлу пропущены, сверьте вручную",
                "", "local", str(obj.path), 1, obj.path.name))
            continue
        checks = [check_object_catalog, check_config, check_structural]
        for fn in checks:
            findings.extend(fn(obj, ctx))
        if not obj.mtype:
            findings.append(Finding(
                "LocalTypeUnknown", "green",
                RULES_BY_KEY["LocalTypeUnknown"].title
                + f" (каталог «{obj.dir.parent.name}»): "
                + ", ".join(["роли", "подсистемы", "Configuration.mdo",
                             "обработчики РЗ/подписок", "шаблоны СКД"]),
                "", "local", str(obj.path), 1, obj.path.name))
    if ctx.skipped:
        findings.append(Finding(
            "LocalContextSkipped", "green",
            "Кросс-файловые проверки пропущены: " + "; ".join(ctx.skipped)
            + " — передайте --src-root", "", "local", "-", 1,
            "; ".join(ctx.skipped)[:80]))
    findings = dedup(findings)
    kept, suppressed = [], []
    for f in findings:
        if suppression_for(f.key, f.file, f.line, entries):
            suppressed.append(f)
        else:
            kept.append(f)
    return kept, suppressed


def dedup(findings: List[Finding]) -> List[Finding]:
    seen: Set[Tuple[str, str, int, str]] = set()
    out: List[Finding] = []
    for f in findings:
        mark = (f.key, f.file, f.line, f.detail)
        if mark in seen:
            continue
        seen.add(mark)
        out.append(f)
    return out


# --- вывод ------------------------------------------------------------------------


def load_fixes() -> Dict[str, dict]:
    if not FIXES_JSON.is_file():
        return {}
    data = json.loads(FIXES_JSON.read_text(encoding="utf-8"))
    return {k: v for k, v in data.items() if k != "_comment"}


def format_md(findings: List[Finding], objects: List[MdoObject],
              suppressed: List[Finding], rules_applied: int) -> str:
    counts = {s: sum(1 for f in findings if f.sev == s)
              for s in ("red", "yellow", "green")}
    n_mdo = sum(1 for o in objects if not o.is_config)
    n_cfg = sum(1 for o in objects if o.is_config)
    src = (f".mdo {n_mdo}" + (f", Configuration.mdo {n_cfg}" if n_cfg else ""))
    head = (f"# metadata_scan — {len(findings)} замечаний "
            f"(🔴 {counts['red']}, 🟡 {counts['yellow']}, 🟢 {counts['green']}); "
            f"источников: {src}; применимо правил: {rules_applied}"
            + (f"; подавлено: {len(suppressed)} (--suppress)" if suppressed else "")
            + "\n")
    if not findings:
        head += "\nЧисто: metadata-слой нарушений не нашёл (формы и макеты — вне " \
                "рамок слоя; остальной каталог — за ручным проходом чек-листа).\n"
        return head
    fixes = load_fixes()
    blocks = [head]
    for i, f in enumerate(findings, start=1):
        std = f" (№{f.std})" if f.std else ""
        kind = "локальный ключ" if f.kind == "local" else "ключ каталога"
        blocks.append(f"\n**{i}. {SEV_MARK[f.sev]} `{f.key}`{std}** — {f.title}"
                      f" <small>({kind})</small>")
        blocks.append(f"- Файл: `{f.file}:{f.line}`")
        if f.fragment:
            blocks.append(f"- Фрагмент: `{f.fragment}`")
        fx = fixes.get(f.key)
        why = f.detail or (fx or {}).get("why", "")
        if why:
            blocks.append(f"- Что не так: {why}")
        if fx and fx.get("good"):
            good = fx["good"].strip().splitlines()[0][:200]
            blocks.append(f"- Как правильно: `{good}`")
        if f.kind == "catalog":
            blocks.append(f"- Карточка: https://docs.checkbsl.org/checks/metadata/{f.key}/")
    if suppressed:
        blocks.append("\n## Подавленные\n")
        for f in suppressed:
            blocks.append(f"- {SEV_MARK[f.sev]} `{f.key}` — {f.file}:{f.line}")
    return "\n".join(blocks) + "\n"


def format_json(findings: List[Finding], objects: List[MdoObject],
                suppressed: List[Finding], rules_applied: int) -> str:
    counts = {s: sum(1 for f in findings if f.sev == s)
              for s in ("red", "yellow", "green")}
    return json.dumps(
        {"objects": [{"file": str(o.path), "type": o.mtype, "name": o.name}
                     for o in objects],
         "rules_applied": rules_applied,
         "counts": counts, "suppressed": len(suppressed),
         "findings": [f.as_dict() for f in findings]},
        ensure_ascii=False, indent=2)


def coverage_report() -> str:
    catalog_keys = harvest_metadata_keys()
    mine = {r.key for r in CHECKS if r.kind == "catalog"}
    inter = mine & catalog_keys
    lines = [f"metadata-слой: {len(inter)}/{len(catalog_keys)} каталогных "
             f"metadata-ключей (счётчик META-001; вливание в общий — META-002)"]
    lines.append("покрыто: " + (", ".join(sorted(inter)) or "—"))
    rest = sorted(catalog_keys - mine)
    lines.append("не покрыто (" + str(len(rest)) + "): " + ", ".join(rest))
    lines.append("локальных структурных ключей (вне каталога): "
                 + str(sum(1 for r in CHECKS if r.kind == "local")))
    return "\n".join(lines) + "\n"


def harvest_metadata_keys() -> Set[str]:
    """Ключи секции metadata из каталога checkbsl (знаменатель счётчика)."""
    keys: Set[str] = set()
    if CATALOG_MD.is_file():
        for m in re.finditer(r"^\| `([\w]+)` \|", CATALOG_MD.read_text(encoding="utf-8"),
                             re.M):
            keys.add(m.group(1))
    return keys


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(
        description="Детерминированный metadata-слой пакета checkbsl: XML метаданных "
                    "EDT-выгрузки (.mdo/.rights) из файлов/каталогов или git-диффа")
    ap.add_argument("paths", nargs="*", help="файлы .mdo/.rights или каталоги объектов")
    ap.add_argument("--diff", metavar="REF",
                    help="проверять объекты, чьи .mdo/.rights изменены относительно REF (git)")
    ap.add_argument("--src-root", metavar="DIR",
                    help="корень src EDT-выгрузки для кросс-файловых проверок "
                         "(роли, подсистемы, Configuration.mdo, UUID проекта)")
    ap.add_argument("--format", choices=("md", "json"), default="md")
    ap.add_argument("--suppress", action="append", default=[], metavar="FILE",
                    help="suppress.json (scripts/review_suppress.py): находки с решением"
                    " Ревьюера «не баг» исключаются, но видны счётчиком (повторяемый)")
    ap.add_argument("--report", metavar="FILE",
                    help="записать md-отчёт в файл (петля самоочистки, r<N>.md)")
    ap.add_argument("--coverage-report", action="store_true",
                    help="печатать покрытие каталогных metadata-ключей и выйти")
    args = ap.parse_args(argv)

    if args.coverage_report:
        print(coverage_report())
        return 0
    try:
        if args.diff:
            inputs = diff_paths(args.diff)
            if not inputs:
                print("metadata-источников (.mdo/.rights) в диффе нет — "
                      "проверять нечего")
                return 0
        else:
            inputs = [Path(p) for p in args.paths]
        if not inputs:
            ap.error("укажите файл/каталог или --diff REF")
        for p in inputs:
            if not p.exists():
                raise RuntimeError(f"путь не найден: {p}")
        entries: List[dict] = []
        for s in args.suppress:
            entries.extend(load_suppress(Path(s)))
        objects = collect_mdo(inputs)
        if not objects:
            print("metadata-источников (.mdo) во входах нет — .rights без объектов "
                  "проверяются слоем META-003")
            return 0
        src_root = infer_src_root(objects,
                                  Path(args.src_root) if args.src_root else None)
        ctx = Context(src_root, objects)
        findings, suppressed = scan(objects, ctx, entries)
    except (FileNotFoundError, RuntimeError, json.JSONDecodeError) as e:
        print(f"❌ {e}", file=sys.stderr)
        return 2

    rules_applied = sum(
        1 for r in CHECKS
        if (r.scope == "config" and any(o.is_config for o in objects))
        or (r.scope == "object" and any(not o.is_config for o in objects)))
    md = format_md(findings, objects, suppressed, rules_applied)
    if args.format == "md":
        print(md)
    else:
        print(format_json(findings, objects, suppressed, rules_applied))
    if args.report:
        Path(args.report).parent.mkdir(parents=True, exist_ok=True)
        Path(args.report).write_text(md, encoding="utf-8")
    return 1 if any(f.sev == "red" for f in findings) else 0


if __name__ == "__main__":
    sys.exit(main())
