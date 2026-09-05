# -*- coding: utf-8 -*-
"""Тесты metadata-слоя (META-001): пары позитив/негатив на каждую проверку,
CLI (exit-коды, форматы, подавления), счётчик покрытия.

Фикстуры — синтетические деревья EDT-выгрузки во временном каталоге
(формат нового EDT: корень в namespace mdclass, дети без префикса)."""
from __future__ import annotations

import contextlib
import io
import json
import sys
import tempfile
import unittest
import uuid as uuidlib
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parent.parent / "scripts"
sys.path.insert(0, str(SCRIPTS))

import metadata_scan as meta  # noqa: E402

NS_HEAD = ('<?xml version="1.0" encoding="UTF-8"?>\n'
           '<mdclass:{tag} xmlns:mdclass='
           '"http://g5.1c.ru/v8/dt/metadata/mdclass" uuid="{u}">\n')
NS_FOOT = "</mdclass:{tag}>\n"


def u() -> str:
    return str(uuidlib.uuid4())


def mdo(tag: str, body: str, name: str = "") -> str:
    return (f'<?xml version="1.0" encoding="UTF-8"?>\n'
            f'<mdclass:{tag} xmlns:mdclass='
            f'"http://g5.1c.ru/v8/dt/metadata/mdclass" uuid="{u()}">\n'
            f'{body}\n</mdclass:{tag}>\n')


def loc(pairs) -> str:
    """Локализуемое свойство конфигурации: прямые дети <key>/<value>."""
    return "\n".join(f"<key>{k}</key><value>{v}</value>" for k, v in pairs)


def synonyms(pairs) -> str:
    return "\n".join(f"<synonym><key>{k}</key><value>{v}</value></synonym>"
                     for k, v in pairs)


CATALOG_BODY = f"""
  <name>{{name}}</name>
  {synonyms([("ru", "{syn}"), ("en", "Test catalog")])}
"""


class Tree:
    """Синтетическая EDT-выгрузка: src c Configuration, объектами, ролями."""

    def __init__(self):
        self.root = Path(tempfile.mkdtemp(prefix="meta_scan_"))
        self.src = self.root / "src"
        self.src.mkdir()

    def write(self, rel: str, text: str) -> Path:
        p = self.src / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text, encoding="utf-8")
        return p

    def catalog(self, name="ТестСправочник", syn="Тест справочник", body="",
                extra="") -> Path:
        inner = (f"<name>{name}</name>\n{synonyms([('ru', syn)])}\n"
                 f"{extra}\n{body}")
        return self.write(f"Catalogs/{name}/{name}.mdo",
                          mdo("Catalog", inner, name))

    def config(self, roles=("Role.ПолныеПрава", "Role.АдминистраторСистемы",
                            "Role.ИнтерактивноеОткрытиеВнешнихОтчетовИОбработок"),
               defaults=("Role.ПолныеПрава", "Role.АдминистраторСистемы",
                         "Role.ИнтерактивноеОткрытиеВнешнихОтчетовИОбработок"),
               objects=("Catalog.ТестСправочник",),
               syn="Тестовая конфигурация, редакция 1.0",
               brief=None, detailed=None, copyright_=None,
               name="ТестКонфигурация") -> Path:
        brief = loc([("ru", syn)]) if brief is None else brief
        detailed = loc([("ru", syn)]) if detailed is None else detailed
        copyright_ = loc([("ru", "© Тест")]) if copyright_ is None else copyright_
        inner = [
            f"<name>{name}</name>",
            synonyms([("ru", syn)]),
            f"<version>1.0.0.1</version>",
            "<briefInformation>" + brief + "</briefInformation>",
            "<detailedInformation>" + detailed + "</detailedInformation>",
            "<copyright>" + copyright_ + "</copyright>",
        ]
        inner += [f"<roles>{r}</roles>" for r in roles]
        inner += [f"<defaultRoles>{r}</defaultRoles>" for r in defaults]
        inner += [f"<catalogs>{o}</catalogs>" for o in objects]
        return self.write("Configuration/Configuration.mdo",
                          mdo("Configuration", "\n".join(inner), name))

    def role(self, name_role="ТестРоль", tokens=("Catalog.ТестСправочник",)) -> Path:
        names = "\n".join(f"<name>{t}</name>" for t in tokens)
        return self.write(f"Roles/{name_role}/Rights.rights",
                          f'<?xml version="1.0" encoding="UTF-8"?>\n'
                          f'<Rights>\n<object>{names}</object>\n</Rights>\n')

    def subsystem(self, tokens=("Catalog.ТестСправочник",)) -> Path:
        body = "<name>ТестПодсистема</name>\n" + "\n".join(
            f"<content>{t}</content>" for t in tokens)
        return self.write("Subsystems/ТестПодсистема/ТестПодсистема.mdo",
                          mdo("Subsystem", body, "ТестПодсистема"))

    def common_module(self, name="ТестМодуль", text="") -> Path:
        return self.write(f"CommonModules/{name}/Module.bsl",
                          f"Процедура Тест()\nКонецПроцедуры\n{text}")

    def scan(self, *paths, src=True) -> tuple:
        objs = meta.collect_mdo([p if p.is_dir() else p.parent for p in paths]
                                if paths else [self.src])
        root = self.src if src else None
        ctx = meta.Context(root, objs)
        return meta.scan(objs, ctx, [])


def keys(findings) -> set:
    return {f.key for f in findings}


class TestCatalogRules(unittest.TestCase):
    """Каталогные ключи уровня объекта: позитив (нарушение) / негатив (чисто)."""

    def setUp(self):
        self.t = Tree()
        self.t.config()
        self.t.role()
        self.t.subsystem()

    def test_synonym_missing_red(self):
        self.t.catalog(syn="")  # пустой синоним ru
        f, _ = self.t.scan(self.t.src / "Catalogs")
        self.assertIn("MDOWithoutSynonym", keys(f))
        self.assertEqual(next(x for x in f if x.key == "MDOWithoutSynonym").sev, "red")

    def test_synonym_present_clean(self):
        self.t.catalog()
        f, _ = self.t.scan(self.t.src / "Catalogs")
        self.assertNotIn("MDOWithoutSynonym", keys(f))

    def test_yo_in_name_and_synonym(self):
        self.t.catalog(name="ТестЁж", syn="Тест ёж")
        f, _ = self.t.scan(self.t.src / "Catalogs")
        yo = [x for x in f if x.key == "YOInMetadata"]
        self.assertTrue(yo)
        self.assertTrue(all(x.sev == "yellow" for x in yo))

    def test_no_yo_clean(self):
        self.t.catalog()
        f, _ = self.t.scan(self.t.src / "Catalogs")
        self.assertNotIn("YOInMetadata", keys(f))

    def test_name_longer_than_80(self):
        name = "А" * 81
        p = self.t.write(f"Catalogs/{name}/{name}.mdo",
                         mdo("Catalog", f"<name>{name}</name>"
                             + synonyms([("ru", "Длинный")]), name))
        f, _ = self.t.scan(self.t.src / "Catalogs")
        self.assertIn("MetadataNameLongerThan", keys(f))

    def test_wrong_synonym_digit(self):
        self.t.catalog(syn="1Тест справочник")
        f, _ = self.t.scan(self.t.src / "Catalogs")
        self.assertIn("WrongSynonym", keys(f))

    def test_wrong_synonym_uppercase_letter_ok(self):
        # сужение 0.31: буквенный регистр не флагуется (№474 требования не
        # содержит, буква каталога давала бы ложняк на каждый объект)
        self.t.catalog(syn="Тест справочник")
        f, _ = self.t.scan(self.t.src / "Catalogs")
        self.assertNotIn("WrongSynonym", keys(f))

    def test_metadata_synonym_mismatch_green(self):
        self.t.catalog(name="СкладОстатков", syn="Наличие на складах")
        f, _ = self.t.scan(self.t.src / "Catalogs")
        hit = [x for x in f if x.key == "MetadataSynonym"]
        self.assertTrue(hit)
        self.assertEqual(hit[0].sev, "green")

    def test_metadata_synonym_match_clean(self):
        self.t.catalog(name="СправочникОстатков", syn="Справочник остатков")
        f, _ = self.t.scan(self.t.src / "Catalogs")
        self.assertNotIn("MetadataSynonym", keys(f))

    def test_same_name_with_tabular_section(self):
        extra = ('<tabularSections uuid="%s"><name>ТестСправочник</name>'
                 "</tabularSections>" % u())
        self.t.catalog(extra=extra)
        f, _ = self.t.scan(self.t.src / "Catalogs")
        self.assertIn("SameMetadataNames", keys(f))

    def test_password_without_mode_red(self):
        attr = ('<attributes uuid="%s"><name>ПарольПользователя</name>'
                '<synonym><key>ru</key><value>Пароль</value></synonym>'
                "<type><types>Строка</types><stringLength>50</stringLength></type>"
                "</attributes>" % u())
        self.t.catalog(extra=attr)
        f, _ = self.t.scan(self.t.src / "Catalogs")
        hit = [x for x in f if x.key == "HidingPasswordInMetadata"]
        self.assertTrue(hit)
        self.assertEqual(hit[0].sev, "red")
        self.assertEqual(hit[0].std, "740")

    def test_password_with_mode_clean(self):
        attr = ('<attributes uuid="%s"><name>ПарольПользователя</name>'
                "<type><types>Строка</types></type>"
                "<passwordMode>true</passwordMode></attributes>" % u())
        self.t.catalog(extra=attr)
        f, _ = self.t.scan(self.t.src / "Catalogs")
        self.assertNotIn("HidingPasswordInMetadata", keys(f))

    def test_extension_object_without_prefix(self):
        p = self.t.write("Ext/МоеРасш/src/Catalogs/ПостороннийОбъект/ПостороннийОбъект.mdo",
                         mdo("Catalog", "<name>ПостороннийОбъект</name>"
                             + synonyms([("ru", "Посторонний объект")]), "x"))
        objs = meta.collect_mdo([p.parent])
        ctx = meta.Context(self.t.src, objs)
        f, _ = meta.scan(objs, ctx, [])
        self.assertIn("ExtensionMetadataWithoutPrefix", keys(f))


class TestConfigRules(unittest.TestCase):
    """Каталогные ключи уровня Configuration.mdo."""

    def test_full_clean_config(self):
        t = Tree()
        t.config()
        t.catalog()
        f, _ = t.scan(t.src / "Configuration")
        self.assertNotIn(keys(f) & {"ConfigurationWithoutSynonym", "BriefInformation",
                                    "DetailedInformation", "Copyright",
                                    "ConfigurationVersion", "InvalidConfigurationName",
                                    "MissingStandardRole", "StandardRolesNotInDefaults"},
                         keys(f))

    def test_brief_information_mismatch(self):
        t = Tree()
        t.config(brief=loc([("ru", "Другая краткая информация")]))
        f, _ = t.scan(t.src / "Configuration")
        self.assertIn("BriefInformation", keys(f))

    def test_copyright_missing(self):
        t = Tree()
        t.config(copyright_="")
        f, _ = t.scan(t.src / "Configuration")
        self.assertIn("Copyright", keys(f))

    def test_version_not_in_synonym(self):
        t = Tree()
        t.config(syn="Тестовая конфигурация")
        f, _ = t.scan(t.src / "Configuration")
        self.assertIn("ConfigurationVersion", keys(f))

    def test_invalid_configuration_name(self):
        t = Tree()
        t.config(name="ТестоваяРедакция2")
        f, _ = t.scan(t.src / "Configuration")
        self.assertIn("InvalidConfigurationName", keys(f))

    def test_missing_standard_role(self):
        t = Tree()
        t.config(roles=("Role.ПолныеПрава",),
                 defaults=("Role.ПолныеПрава",))
        f, _ = t.scan(t.src / "Configuration")
        hit = [x for x in f if x.key == "MissingStandardRole"]
        self.assertEqual({x.detail.split("«")[1].split("»")[0] for x in hit},
                         {"АдминистраторСистемы",
                          "ИнтерактивноеОткрытиеВнешнихОтчетовИОбработок"})

    def test_standard_role_not_in_defaults(self):
        t = Tree()
        t.config(defaults=("Role.ПолныеПрава",))
        f, _ = t.scan(t.src / "Configuration")
        self.assertIn("StandardRolesNotInDefaults", keys(f))


class TestLocalStructural(unittest.TestCase):
    """Локальные структурные ключи — перенос X/M-цепочек bash."""

    def setUp(self):
        self.t = Tree()
        self.t.config()
        self.t.role()
        self.t.subsystem()

    def test_duplicate_uuid_in_mdo(self):
        dup = u()
        body = (f'<producedTypes><objectType typeId="{dup}" valueTypeId="{dup}"/>'
                f"</producedTypes>\n<name>ТестСправочник</name>"
                + synonyms([("ru", "Тест справочник")]))
        self.t.write("Catalogs/ТестСправочник/ТестСправочник.mdo",
                     mdo("Catalog", body, "x").replace(
                         f'uuid="{json.dumps("x")}"', "") if False else
                     f'<?xml version="1.0" encoding="UTF-8"?>\n'
                     f'<mdclass:Catalog xmlns:mdclass='
                     f'"http://g5.1c.ru/v8/dt/metadata/mdclass" uuid="{u()}">\n'
                     f"{body}\n</mdclass:Catalog>\n")
        f, _ = self.t.scan(self.t.src / "Catalogs")
        self.assertIn("DuplicateUUIDInMDO", keys(f))

    def test_uuid_collision_across_project(self):
        shared = u()
        for nm in ("ТестСправочник", "ВторойСправочник"):
            body = (f'<producedTypes><objectType typeId="{shared}"/></producedTypes>'
                    f"\n<name>{nm}</name>" + synonyms([("ru", "Справочник " + nm)]))
            self.t.write(f"Catalogs/{nm}/{nm}.mdo",
                         f'<?xml version="1.0" encoding="UTF-8"?>\n'
                         f'<mdclass:Catalog xmlns:mdclass='
                         f'"http://g5.1c.ru/v8/dt/metadata/mdclass" uuid="{u()}">\n'
                         f"{body}\n</mdclass:Catalog>\n")
        self.t.config(objects=("Catalog.ТестСправочник", "Catalog.ВторойСправочник"))
        f, _ = self.t.scan(self.t.src / "Catalogs")
        self.assertIn("UUIDCollisionProject", keys(f))

    def test_accumulation_register_gates(self):
        body = ("<name>ТестРегистр</name>" + synonyms([("ru", "Тест регистр")]))
        p = self.t.write("AccumulationRegisters/ТестРегистр/ТестРегистр.mdo",
                         f'<?xml version="1.0" encoding="UTF-8"?>\n'
                         f'<mdclass:AccumulationRegister xmlns:mdclass='
                         f'"http://g5.1c.ru/v8/dt/metadata/mdclass" uuid="{u()}">\n'
                         f"{body}\n</mdclass:AccumulationRegister>\n")
        self.t.config(objects=("AccumulationRegister.ТестРегистр",))
        f, _ = self.t.scan(p.parent)
        k = keys(f)
        self.assertIn("AccumRegisterNoResource", k)
        self.assertIn("AccumRegisterNoDimension", k)
        # не-UI тип без подсистемы — не замечание (gate HAS_UI)
        self.assertNotIn("ObjectWithoutSubsystem", k)

    def test_turnovers_hint(self):
        body = ("<name>ТестРегистр</name>" + synonyms([("ru", "Тест регистр")])
                + "<resources uuid=\"%s\"><name>Сумма</name></resources>"
                  "<dimensions uuid=\"%s\"><name>Организация</name></dimensions>"
                  "<registerType>Turnovers</registerType>" % (u(), u()))
        p = self.t.write("AccumulationRegisters/ТестРегистр/ТестРегистр.mdo",
                         f'<?xml version="1.0" encoding="UTF-8"?>\n'
                         f'<mdclass:AccumulationRegister xmlns:mdclass='
                         f'"http://g5.1c.ru/v8/dt/metadata/mdclass" uuid="{u()}">\n'
                         f"{body}\n</mdclass:AccumulationRegister>\n")
        f, _ = self.t.scan(p.parent)
        self.assertIn("AccumRegisterTurnoversHint", keys(f))

    def test_object_not_in_configuration(self):
        self.t.catalog(name="Незарегистрированный", syn="Не в конфигурации")
        f, _ = self.t.scan(self.t.src / "Catalogs")
        self.assertIn("ObjectNotInConfiguration", keys(f))

    def test_object_without_role(self):
        self.t.catalog(name="Безроли", syn="Без роли")
        self.t.config(objects=("Catalog.ТестСправочник", "Catalog.Безроли"))
        f, _ = self.t.scan(self.t.src / "Catalogs")
        hit = [x for x in f if x.key == "ObjectWithoutRole"]
        self.assertEqual([x for x in hit if "Безроли" in x.detail], hit)
        self.assertEqual(hit[0].std, "532")

    def test_object_without_subsystem(self):
        self.t.catalog(name="Безподсистемы", syn="Без подсистемы")
        self.t.config(objects=("Catalog.ТестСправочник", "Catalog.Безподсистемы"))
        self.t.role(tokens=("Catalog.ТестСправочник", "Catalog.Безподсистемы"))
        f, _ = self.t.scan(self.t.src / "Catalogs")
        self.assertIn("ObjectWithoutSubsystem", keys(f))

    def _scheduled_job(self, method_ref, predefined="true", schedule=False) -> Path:
        self.t.common_module("ТестМодуль",
                             "Процедура Исполнить() Экспорт\nКонецПроцедуры\n")
        body = ("<name>ТестРЗ</name>" + synonyms([("ru", "Тест РЗ")])
                + f"<methodName>{method_ref}</methodName>"
                + f"<predefined>{predefined}</predefined>")
        p = self.t.write("ScheduledJobs/ТестРЗ/ТестРЗ.mdo",
                         f'<?xml version="1.0" encoding="UTF-8"?>\n'
                         f'<mdclass:ScheduledJob xmlns:mdclass='
                         f'"http://g5.1c.ru/v8/dt/metadata/mdclass" uuid="{u()}">\n'
                         f"{body}\n</mdclass:ScheduledJob>\n")
        if schedule:
            (p.parent / "Schedule.schedule").write_text("{}", encoding="utf-8")
        self.t.config(objects=())
        return p

    def test_scheduled_job_handler_ok(self):
        p = self._scheduled_job("CommonModule.ТестМодуль.Исполнить", schedule=True)
        f, _ = self.t.scan(p.parent)
        k = keys(f)
        self.assertNotIn("ScheduledJobHandlerMissing", k)
        self.assertNotIn("ScheduledJobNoSchedule", k)

    def test_scheduled_job_handler_missing(self):
        p = self._scheduled_job("CommonModule.ТестМодуль.Отсутствует", schedule=True)
        f, _ = self.t.scan(p.parent)
        hit = [x for x in f if x.key == "ScheduledJobHandlerMissing"]
        self.assertTrue(hit)
        self.assertEqual(hit[0].sev, "red")

    def test_scheduled_job_handler_no_export(self):
        p = self._scheduled_job("CommonModule.ТестМодуль.Исполнить", schedule=True)
        self.t.common_module("ТестМодуль", "Процедура Исполнить()\nКонецПроцедуры\n")
        f, _ = self.t.scan(p.parent)
        hit = [x for x in f if x.key == "ScheduledJobHandlerMissing"]
        self.assertTrue(hit)
        self.assertEqual(hit[0].sev, "yellow")  # понижение: метод есть, без Экспорт

    def test_scheduled_job_no_schedule(self):
        p = self._scheduled_job("CommonModule.ТестМодуль.Исполнить", schedule=False)
        f, _ = self.t.scan(p.parent)
        self.assertIn("ScheduledJobNoSchedule", keys(f))

    def test_event_subscription_handler(self):
        self.t.common_module("ТестМодуль",
                             "Процедура ПриЗаписи(Источник) Экспорт\nКонецПроцедуры\n")
        body = ("<name>ТестПодписка</name>" + synonyms([("ru", "Подписка")])
                + "<event>OnWrite</event>"
                + "<handler>CommonModule.ТестМодуль.ПриЗаписи</handler>")
        p = self.t.write("EventSubscriptions/ТестПодписка/ТестПодписка.mdo",
                         f'<?xml version="1.0" encoding="UTF-8"?>\n'
                         f'<mdclass:EventSubscription xmlns:mdclass='
                         f'"http://g5.1c.ru/v8/dt/metadata/mdclass" uuid="{u()}">\n'
                         f"{body}\n</mdclass:EventSubscription>\n")
        f, _ = self.t.scan(p.parent)
        self.assertNotIn("EventSubscriptionHandlerMissing", keys(f))
        # битый handler
        self.t.write("EventSubscriptions/ТестПодписка/ТестПодписка.mdo",
                     f'<?xml version="1.0" encoding="UTF-8"?>\n'
                     f'<mdclass:EventSubscription xmlns:mdclass='
                     f'"http://g5.1c.ru/v8/dt/metadata/mdclass" uuid="{u()}">\n'
                     f'{body.replace("ПриЗаписи</handler>", "НетМетода</handler>")}\n'
                     f"</mdclass:EventSubscription>\n")
        f, _ = self.t.scan(p.parent)
        self.assertIn("EventSubscriptionHandlerMissing", keys(f))

    def test_report_schema_missing(self):
        body = ("<name>ТестОтчет</name>" + synonyms([("ru", "Тест отчет")])
                + "<mainDataCompositionSchema>Report.ТестОтчет.Template.Осн</mainDataCompositionSchema>")
        p = self.t.write("Reports/ТестОтчет/ТестОтчет.mdo",
                         f'<?xml version="1.0" encoding="UTF-8"?>\n'
                         f'<mdclass:Report xmlns:mdclass='
                         f'"http://g5.1c.ru/v8/dt/metadata/mdclass" uuid="{u()}">\n'
                         f"{body}\n</mdclass:Report>\n")
        f, _ = self.t.scan(p.parent)
        self.assertIn("ReportSchemaMissing", keys(f))

    def test_dcs_param_not_declared(self):
        tpl = ('<templates uuid="%s"><name>Осн</name>'
               "<templateType>DataCompositionSchema</templateType></templates>" % u())
        body = ("<name>ТестОтчет</name>" + synonyms([("ru", "Тест отчет")]) + tpl
                + "<mainDataCompositionSchema>Report.ТестОтчет.Template.Осн</mainDataCompositionSchema>")
        p = self.t.write("Reports/ТестОтчет/ТестОтчет.mdo",
                         f'<?xml version="1.0" encoding="UTF-8"?>\n'
                         f'<mdclass:Report xmlns:mdclass='
                         f'"http://g5.1c.ru/v8/dt/metadata/mdclass" uuid="{u()}">\n'
                         f"{body}\n</mdclass:Report>\n")
        dcs = ('<?xml version="1.0" encoding="UTF-8"?>\n'
               '<DataCompositionSchema xmlns="http://v8.1c.ru/8.1/data-composition-system/schema">'
               "<query>ВЫБРАТЬ Ссылка ИЗ Справочник.Х ГДЕ Ссылка В (&amp;Отбор)</query>"
               "<parameter><name>Другой</name></parameter>"
               "</DataCompositionSchema>")
        self.t.write("Reports/ТестОтчет/Templates/Осн/Template.dcs", dcs)
        f, _ = self.t.scan(p.parent)
        hit = [x for x in f if x.key == "DCSParamNotDeclared"]
        self.assertTrue(hit)
        self.assertIn("Отбор", hit[0].detail)
        # объявленный параметр — чисто
        dcs_ok = dcs.replace("Другой", "Отбор")
        self.t.write("Reports/ТестОтчет/Templates/Осн/Template.dcs", dcs_ok)
        f, _ = self.t.scan(p.parent)
        self.assertNotIn("DCSParamNotDeclared", keys(f))

    def test_query_param_mismatch(self):
        self.t.catalog()
        self.t.write("Catalogs/ТестСправочник/ObjectModule.bsl",
                     'Запрос = Новый Запрос("ВЫБРАТЬ * ИЗ Справочник.Х ГДЕ К = &Ключ");\n')
        f, _ = self.t.scan(self.t.src / "Catalogs")
        self.assertIn("QueryParamMismatch", keys(f))

    def test_query_param_consistent_clean(self):
        self.t.catalog()
        self.t.write("Catalogs/ТестСправочник/ObjectModule.bsl",
                     'Запрос = Новый Запрос("ВЫБРАТЬ * ИЗ Справочник.Х ГДЕ К = &Ключ");\n'
                     'Запрос.УстановитьПараметр("Ключ", Значение);\n')
        f, _ = self.t.scan(self.t.src / "Catalogs")
        self.assertNotIn("QueryParamMismatch", keys(f))

    def test_query_param_report_exception(self):
        # модули отчёта из X5 исключены: параметры связаны через СКД
        body = "<name>ТестОтчет</name>" + synonyms([("ru", "Тест отчет")])
        p = self.t.write("Reports/ТестОтчет/ТестОтчет.mdo",
                         f'<?xml version="1.0" encoding="UTF-8"?>\n'
                         f'<mdclass:Report xmlns:mdclass='
                         f'"http://g5.1c.ru/v8/dt/metadata/mdclass" uuid="{u()}">\n'
                         f"{body}\n</mdclass:Report>\n")
        self.t.write("Reports/ТестОтчет/ObjectModule.bsl",
                     'Запрос = Новый Запрос("ВЫБРАТЬ * ИЗ Справочник.Х ГДЕ К = &Ключ");\n')
        f, _ = self.t.scan(p.parent)
        self.assertNotIn("QueryParamMismatch", keys(f))

    def test_change_and_call_no_resume(self):
        self.t.catalog()
        self.t.write("Catalogs/ТестСправочник/ObjectModule.bsl",
                     "&ИзменениеИКонтроль(\"Метод\")\nПроцедура Метод()\nКонецПроцедуры\n")
        f, _ = self.t.scan(self.t.src / "Catalogs")
        self.assertIn("ChangeAndCallNoResume", keys(f))

    def test_change_and_call_with_resume_clean(self):
        self.t.catalog()
        self.t.write("Catalogs/ТестСправочник/ObjectModule.bsl",
                     "&ИзменениеИКонтроль(\"Метод\")\nПроцедура Метод()\n"
                     "ПродолжитьВызов();\nКонецПроцедуры\n")
        f, _ = self.t.scan(self.t.src / "Catalogs")
        self.assertNotIn("ChangeAndCallNoResume", keys(f))


class TestRobustness(unittest.TestCase):
    """Отклонения сценария 02: битый XML, неизвестный тип, подавления."""

    def setUp(self):
        self.t = Tree()
        self.t.config()
        self.t.role()
        self.t.subsystem()

    def test_broken_xml_warns_not_crashes(self):
        self.t.write("Catalogs/Битый/Битый.mdo", "<mdclass:Catalog>не закрыт")
        self.t.catalog()
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            rc = meta.main(["--format", "md", str(self.t.src / "Catalogs")])
        self.assertIn("не разобран", out.getvalue())
        self.assertEqual(rc, 0)  # битый = 🟢, красного нет

    def test_unknown_type_lists_skipped(self):
        self.t.write("Enums/ТестПеречисление/ТестПеречисление.mdo",
                     mdo("Enum", "<name>ТестПеречисление</name>"
                         + synonyms([("ru", "Перечисление")]), "x"))
        objs = meta.collect_mdo([self.t.src / "Enums"])
        ctx = meta.Context(self.t.src, objs)
        f, _ = meta.scan(objs, ctx, [])
        hit = [x for x in f if x.key == "LocalTypeUnknown"]
        self.assertTrue(hit)
        self.assertIn("роли", hit[0].title + hit[0].detail)

    def test_no_src_root_notes_skip(self):
        self.t.catalog()
        objs = meta.collect_mdo([self.t.src / "Catalogs"])
        ctx = meta.Context(None, objs)
        f, _ = meta.scan(objs, ctx, [])
        hit = [x for x in f if x.key == "LocalContextSkipped"]
        self.assertTrue(hit)
        self.assertNotIn("ObjectWithoutRole", keys(f))  # не стреляет без контекста


class TestCli(unittest.TestCase):
    """CLI: exit-коды, форматы, подавления, дифф без источников, счётчик."""

    def setUp(self):
        self.t = Tree()
        self.t.config()
        self.t.role()
        self.t.subsystem()

    def _run(self, *argv):
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            rc = meta.main(list(argv))
        return rc, out.getvalue()

    def test_exit_0_on_clean(self):
        self.t.catalog()
        rc, out = self._run("--format", "json", str(self.t.src / "Catalogs"))
        self.assertEqual(rc, 0)
        data = json.loads(out)
        self.assertEqual(data["counts"]["red"], 0)

    def test_exit_1_on_red(self):
        self.t.catalog(syn="")
        rc, _ = self._run("--format", "json", str(self.t.src / "Catalogs"))
        self.assertEqual(rc, 1)

    def test_exit_2_on_missing_path(self):
        rc, _ = self._run(str(self.t.src / "НетТакого"))
        self.assertEqual(rc, 2)

    def test_exit_2_without_input(self):
        with self.assertRaises(SystemExit) as cm:
            meta.main([])
        self.assertEqual(cm.exception.code, 2)

    def test_json_catalog_links(self):
        self.t.catalog(syn="")
        rc, out = self._run("--format", "json", str(self.t.src / "Catalogs"))
        finding = json.loads(out)["findings"][0]
        self.assertEqual(finding["catalog"],
                         "https://docs.checkbsl.org/checks/metadata/MDOWithoutSynonym/")

    def test_md_report_file_written(self):
        self.t.catalog(syn="")
        rep = self.t.root / "r1.md"
        rc, _ = self._run("--format", "md", "--report", str(rep),
                          str(self.t.src / "Catalogs"))
        self.assertEqual(rc, 1)
        text = rep.read_text(encoding="utf-8")
        self.assertIn("MDOWithoutSynonym", text)
        self.assertIn("metadata_scan", text)

    def test_suppress_hides_but_counts(self):
        self.t.catalog(syn="")
        mdo_p = self.t.src / "Catalogs/ТестСправочник/ТестСправочник.mdo"
        sup = self.t.root / "suppress.json"
        sup.write_text(json.dumps([
            {"key": "MDOWithoutSynonym", "file": str(mdo_p),
             "reason": "служебный объект", "author": "Roman",
             "date": "2026-09-04"}], ensure_ascii=False), encoding="utf-8")
        rc, out = self._run("--format", "json", "--suppress", str(sup),
                            str(self.t.src / "Catalogs"))
        data = json.loads(out)
        self.assertEqual(data["suppressed"], 1)
        self.assertEqual(data["counts"]["red"], 0)
        self.assertEqual(rc, 0)

    def test_diff_without_mdo_sources(self):
        import subprocess
        try:
            G = ["git", "-c", "user.email=t@t", "-c", "user.name=t"]
            subprocess.run(G + ["init", "-q"], cwd=self.t.root, check=True,
                           capture_output=True)
            (self.t.root / "README.md").write_text("x", encoding="utf-8")
            subprocess.run(G + ["add", "."], cwd=self.t.root, check=True,
                           capture_output=True)
            subprocess.run(G + ["commit", "-qm", "init"], cwd=self.t.root,
                           check=True, capture_output=True)
        except (subprocess.CalledProcessError, OSError):
            self.skipTest("git недоступен")
        import os
        old = os.getcwd()
        os.chdir(self.t.root)
        try:
            rc, out = self._run("--diff", "HEAD")
        finally:
            os.chdir(old)
        self.assertIn("нет", out)
        self.assertEqual(rc, 0)

    def test_coverage_report_lock(self):
        """Замок критерия 2 из 04: ≥15 каталогных metadata-ключей, счётчик
        воспроизводится из каталога references/checkbsl/metadata.md."""
        rc, out = self._run("--coverage-report")
        harvest = meta.harvest_metadata_keys()
        mine = {r.key for r in meta.CHECKS if r.kind == "catalog"}
        self.assertGreaterEqual(len(mine & harvest), 15)
        self.assertIn(f"{len(mine & harvest)}/{len(harvest)}", out)
        # каждый каталогный ключ слоя существует в каталоге (нет опечаток)
        self.assertEqual(mine - harvest, set())
        # у каждого каталогного ключа есть fixes «что не так/как правильно»
        fixes = meta.load_fixes()
        for k in sorted(mine):
            self.assertIn(k, fixes, f"{k} без fixes-записи")
            self.assertTrue(fixes[k].get("why") and fixes[k].get("good"))

    def test_rules_table_no_duplicates(self):
        seen = [r.key for r in meta.CHECKS]
        self.assertEqual(len(seen), len(set(seen)))


if __name__ == "__main__":
    unittest.main()


class TestIndexCache(unittest.TestCase):
    """META-002: кэш индекса проектного обхода — hit/инвалидация/битый файл.
    Кэш — ускоритель, не источник истины: любое сомнение → обход заново."""

    def setUp(self):
        self.t = Tree()
        self.t.catalog()
        self.t.role()
        self.t.subsystem()
        self.cache = self.t.root / "cache"
        self.cache.mkdir()
        self.objects = meta.collect_mdo([self.t.src])

    def _ctx(self) -> meta.Context:
        ctx = meta.Context(self.t.src, self.objects, cache_dir=self.cache)
        ctx.rights_tokens  # свойство триггерит _prime
        return ctx

    def test_prime_writes_cache_file(self):
        self.assertEqual(list(self.cache.iterdir()), [])
        ctx = self._ctx()
        files = list(self.cache.iterdir())
        self.assertEqual(len(files), 1)
        self.assertTrue(files[0].name.startswith("metadata-index-"))
        # индекс построен обходом: токен роли из Roles/ присутствует
        self.assertIn("Catalog.ТестСправочник", ctx.rights_tokens)

    def test_cache_hit_loads_sentinel(self):
        """Доказательство загрузки из кэша: файл с текущим ключом содержит
        метку, которую обход дерева дать не может."""
        ctx = meta.Context(self.t.src, self.objects, cache_dir=self.cache)
        key = ctx._tree_key()               # _tree_key не праймит индекс
        fake = self.cache / f"metadata-index-{key[:24]}.json"
        fake.write_text(json.dumps({
            "uuid_files": {}, "rights_tokens": ["Catalog.СентинелИзКэша"],
            "subsystem_hits": [], "have_roles": True, "have_subs": True,
        }), encoding="utf-8")
        ctx.rights_tokens                    # hit — метка видна
        self.assertIn("Catalog.СентинелИзКэша", ctx.rights_tokens)

    def test_tree_change_invalidates_cache(self):
        self._ctx()                          # индекс записан
        import os
        mdo_file = next(self.t.src.rglob("*.mdo"))
        stamp = mdo_file.stat().st_mtime_ns + 10**9
        os.utime(mdo_file, ns=(stamp, stamp))   # правка меняет mtime
        ctx = self._ctx()                   # miss → обход заново
        self.assertNotIn("Catalog.СентинелИзКэша", ctx.rights_tokens)
        self.assertEqual(len(list(self.cache.iterdir())), 2)  # старый + новый

    def test_broken_cache_rebuilds(self):
        self._ctx()
        cached = next(self.cache.iterdir())
        cached.write_text("{битый json", encoding="utf-8")
        ctx = self._ctx()                   # битый → обход, не падение
        self.assertIn("Catalog.ТестСправочник", ctx.rights_tokens)

    def test_without_cache_dir_no_files(self):
        ctx = meta.Context(self.t.src, self.objects)
        ctx.rights_tokens
        self.assertIn("Catalog.ТестСправочник", ctx.rights_tokens)
        self.assertEqual(list(self.cache.iterdir()), [])
