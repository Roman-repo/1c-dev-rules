# -*- coding: utf-8 -*-
"""Тесты генератора диаграмм метаданных (issue #96): пары позитив/негатив на
каждый парсер рёбер, компоновка (соседи 1-го уровня, обрезание, режимы),
детерминированность бит-в-бит, экранирование Mermaid, отказоустойчивость
(битый XML, дифф без .mdo) и CLI (exit-коды, кэш индекса рёбер).

Фикстуры — синтетические деревья EDT-выгрузки во временном каталоге
(формат нового EDT: корень в namespace mdclass, дети без префикса)."""
from __future__ import annotations

import contextlib
import io
import os
import subprocess
import sys
import tempfile
import unittest
import uuid as uuidlib
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parent.parent / "scripts"
sys.path.insert(0, str(SCRIPTS))

import metadata_diagram as diag  # noqa: E402
from metadata_scan import load_object  # noqa: E402


def u() -> str:
    return str(uuidlib.uuid4())


def mdo(tag: str, body: str) -> str:
    return (f'<?xml version="1.0" encoding="UTF-8"?>\n'
            f'<mdclass:{tag} xmlns:mdclass='
            f'"http://g5.1c.ru/v8/dt/metadata/mdclass" uuid="{u()}">\n'
            f'{body}\n</mdclass:{tag}>\n')


def _props(name: str, attrs=(), dims=(), owners=(), register_records=(),
           content_md=(), content_text=()) -> str:
    """Тело .mdo из знакомых генератору источников рёбер."""
    parts = [f"<name>{name}</name>"]

    def types_block(ts):
        return "<type>" + "".join(f"<types>{t}</types>" for t in ts) + "</type>"

    for n, ts in attrs:
        parts.append(f'<attributes uuid="{u()}"><name>{n}</name>'
                     f"{types_block(ts)}</attributes>")
    for n, ts in dims:
        parts.append(f'<dimensions uuid="{u()}"><name>{n}</name>'
                     f"{types_block(ts)}</dimensions>")
    for o in owners:
        parts.append(f"<owners>{o}</owners>")
    for rr in register_records:
        parts.append(f"<registerRecords>{rr}</registerRecords>")
    for x in content_md:
        parts.append(f"<content><mdObject>{x}</mdObject>"
                     "<autoRecord>Allow</autoRecord></content>")
    for x in content_text:
        parts.append(f"<content>{x}</content>")
    return "\n".join(parts)


class Tree:
    """Синтетическая EDT-выгрузка: src с объектами знакомых видов."""

    def __init__(self):
        self.root = Path(tempfile.mkdtemp(prefix="meta_diag_"))
        self.src = self.root / "src"
        self.src.mkdir()

    def write(self, rel: str, text: str) -> Path:
        p = self.src / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text, encoding="utf-8")
        return p

    def object(self, kind_dir: str, tag: str, name: str, **kw) -> Path:
        return self.write(f"{kind_dir}/{name}/{name}.mdo",
                          mdo(tag, _props(name, **kw)))

    def document(self, name, **kw):
        return self.object("Documents", "Document", name, **kw)

    def catalog(self, name, **kw):
        return self.object("Catalogs", "Catalog", name, **kw)

    def register(self, name, **kw):
        return self.object("AccumulationRegisters", "AccumulationRegister",
                           name, **kw)

    def exchange_plan(self, name, **kw):
        return self.object("ExchangePlans", "ExchangePlan", name, **kw)

    def subsystem(self, name, **kw):
        return self.object("Subsystems", "Subsystem", name, **kw)


def run_main(*argv) -> tuple:
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        rc = diag.main(list(argv))
    return rc, buf.getvalue()


class TestTokens(unittest.TestCase):
    """ref_token/direct_token: карта видов ссылочных типов и негативы."""

    def test_ref_token_variants(self):
        cases = {
            "CatalogRef.Номенклатура": "Catalog.Номенклатура",
            "DocumentRef.Заявка": "Document.Заявка",
            "EnumRef.СтатусыЗаявок": "Enum.СтатусыЗаявок",
            "ChartOfCharacteristicTypesRef.ДопРеквизиты":
                "ChartOfCharacteristicTypes.ДопРеквизиты",
            "Characteristic.ДопРеквизиты":
                "ChartOfCharacteristicTypes.ДопРеквизиты",
            "CatalogObject.Номенклатура": "Catalog.Номенклатура",
            "InformationRegisterRecordSet.Цены":
                "InformationRegister.Цены",
            "ConstantValueManager.КурсыВалют": "Constant.КурсыВалют",
            "ExchangePlanRef.ОбменТОиР20": "ExchangePlan.ОбменТОиР20",
            "d5p1:CatalogRef.Номенклатура": "Catalog.Номенклатура",
        }
        for raw, expected in cases.items():
            self.assertEqual(diag.ref_token(raw), expected, raw)

    def test_ref_token_non_references(self):
        for raw in ("String", "Number", "Boolean", "Date", "UUID",
                    "ValueStorage", "AnyRef", "CatalogRef", "DocumentRef",
                    "DefinedType.ТиповойСостав", ""):
            self.assertIsNone(diag.ref_token(raw), raw)

    def test_direct_token_rejects_role_and_suffixed(self):
        # прямые значения уже с голым видом: Role.*/склейки мусора — мимо;
        # суффиксы Ref НЕ снимаются (иначе CatalogRef.X прошёл бы дважды)
        self.assertEqual(diag.direct_token("AccumulationRegister.Р1"),
                         "AccumulationRegister.Р1")
        self.assertIsNone(diag.direct_token("Role.ПолныеПрава"))
        self.assertIsNone(diag.direct_token("CatalogRef.Номенклатура"))
        self.assertIsNone(diag.direct_token("CatalogRef"))
        self.assertIsNone(diag.direct_token("мусор без точки"))


class TestExtractEdges(unittest.TestCase):
    """Позитив/негатив на каждый источник рёбер (заземлено по toir2)."""

    def setUp(self):
        self.t = Tree()

    def edges(self, path):
        return diag.extract_edges(load_object(path))

    def test_register_records_movement(self):
        p = self.t.document(
            "ВнутреннееПотреблениеТоваров",
            register_records=("AccumulationRegister.ТоварыНаСкладах",
                              "AccumulationRegister.ЗаказыНаВнутреннееПотребление"))
        self.assertEqual(self.edges(p), [
            ("Document.ВнутреннееПотреблениеТоваров",
             "AccumulationRegister.ЗаказыНаВнутреннееПотребление", "movement"),
            ("Document.ВнутреннееПотреблениеТоваров",
             "AccumulationRegister.ТоварыНаСкладах", "movement"),
        ])

    def test_types_reference(self):
        p = self.t.document("Д1", attrs=[
            ("Номенклатура", ("CatalogRef.Номенклатура",)),
            ("Комментарий", ("String",)),
            ("Организация", ("CatalogRef.Организации", "DocumentRef.Заявка")),
        ])
        self.assertEqual(self.edges(p), [
            ("Document.Д1", "Catalog.Номенклатура", "reference"),
            ("Document.Д1", "Catalog.Организации", "reference"),
            ("Document.Д1", "Document.Заявка", "reference"),
        ])

    def test_register_dimensions_reference(self):
        p = self.t.register("Р1", dims=[("Номенклатура",
                                         ("CatalogRef.Номенклатура",))])
        self.assertEqual(self.edges(p),
                         [("AccumulationRegister.Р1", "Catalog.Номенклатура",
                           "reference")])

    def test_owners_ownership(self):
        p = self.t.catalog("ЗначенияСвойств", owners=(
            "ChartOfCharacteristicTypes.ДополнительныеРеквизитыИСведения",))
        self.assertEqual(self.edges(p), [
            ("Catalog.ЗначенияСвойств",
             "ChartOfCharacteristicTypes.ДополнительныеРеквизитыИСведения",
             "ownership")])

    def test_exchange_content(self):
        p = self.t.exchange_plan("Обмен", content_md=(
            "Catalog.Номенклатура", "Document.Д1"))
        self.assertEqual(self.edges(p), [
            ("ExchangePlan.Обмен", "Catalog.Номенклатура", "exchange"),
            ("ExchangePlan.Обмен", "Document.Д1", "exchange")])

    def test_subsystem_content_composition(self):
        p = self.t.subsystem("Учёт", content_text=("Catalog.С1", "Document.Д1",
                                                   "Role.ПолныеПрава"))
        self.assertEqual(self.edges(p), [
            ("Subsystem.Учёт", "Catalog.С1", "composition"),
            ("Subsystem.Учёт", "Document.Д1", "composition")])

    def test_broken_xml_no_edges(self):
        p = self.t.write("Catalogs/Битый/Битый.mdo", "<not-xml…")
        self.assertEqual(self.edges(p), [])


class TestGraphAndCli(unittest.TestCase):
    """Компоновка + CLI: соседи, режимы, обрезание, детерминированность,
    заглушки, кэш, exit-коды."""

    def setUp(self):
        self.t = Tree()
        self.t.catalog("С1")
        self.t.catalog("Другой")
        self.t.document("Д1", register_records=("AccumulationRegister.Р1",),
                        attrs=[("Ссылка", ("CatalogRef.С1",))])
        self.t.document("Д2", attrs=[("Ссылка", ("CatalogRef.С1",))])
        self.t.document("Д3", attrs=[("Ссылка", ("CatalogRef.С1",))])
        self.t.document("Чужой", attrs=[("Ссылка", ("CatalogRef.Другой",))])
        self.t.register("Р1", dims=[("Номенклатура",
                                     ("CatalogRef.С1",))])
        self.t.catalog("Подчинён", owners=("Catalog.С1",))
        self.t.exchange_plan("Обмен", content_md=("Catalog.С1",))
        self.t.subsystem("Учёт", content_text=("Document.Д1", "Catalog.С1"))
        self.src = str(self.t.src)

    def test_neighbors_depth1_all_incoming(self):
        # критерий 2: изменённый справочник с N входящих ссылок → все N
        out = str(self.t.root / "d.md")
        rc, _ = run_main(f"{self.src}/Catalogs/С1", "--src-root", self.src,
                         "--out", out)
        text = Path(out).read_text(encoding="utf-8")
        self.assertEqual(rc, 0)
        for label in ("Документ Д1", "Документ Д2", "Документ Д3",
                      "Регистр накопления Р1", "Справочник Подчинён",
                      "План обмена Обмен"):
            self.assertIn(label, text)
        self.assertNotIn("Чужой", text)
        self.assertNotIn("Подсистема Учёт", text)  # состав не мешает соседям

    def test_determinism_bit_to_bit(self):
        # критерий 3: тот же вызов → бит-в-бит тот же файл
        out = str(self.t.root / "d.md")
        run_main(f"{self.src}/Documents/Д1", "--src-root", self.src,
                 "--cache-dir", str(self.t.root / "cache"), "--out", out)
        first = Path(out).read_bytes()
        run_main(f"{self.src}/Documents/Д1", "--src-root", self.src,
                 "--cache-dir", str(self.t.root / "cache"), "--out", out)
        self.assertEqual(first, Path(out).read_bytes())

    def test_mermaid_escaping(self):
        # критерий 4: кириллица/скобки/кавычки/Ё не ломают Mermaid
        self.t.catalog('Каталог (тест) "Цитата" Ёлка')
        out = str(self.t.root / "d.md")
        run_main(f"{self.src}/Catalogs", "--src-root", self.src, "--out", out)
        text = Path(out).read_text(encoding="utf-8")
        self.assertIn('Справочник Каталог (тест) \\"Цитата\\" Ёлка', text)
        for line in text.splitlines():
            if line.strip().startswith("n") and "[" in line:
                inner = line.split('"')[1] if '"' in line else ""
                self.assertNotIn('"', inner.replace('\\"', ""))

    def test_mode_dataflow_only_movements(self):
        out = str(self.t.root / "d.md")
        rc, _ = run_main(f"{self.src}/Documents/Д1", "--src-root", self.src,
                         "--mode", "dataflow", "--out", out)
        text = Path(out).read_text(encoding="utf-8")
        self.assertEqual(rc, 0)
        self.assertIn('|"движение"|', text)
        self.assertNotIn('|"ссылка"|', text)
        self.assertNotIn("Справочник С1", text)

    def test_max_nodes_truncation(self):
        # у С1 шесть соседей; лимит 3 → область + 2 ближних, 4 отброшены
        out = str(self.t.root / "d.md")
        rc, _ = run_main(f"{self.src}/Catalogs/С1", "--src-root", self.src,
                         "--max-nodes", "3", "--out", out)
        text = Path(out).read_text(encoding="utf-8")
        self.assertEqual(rc, 0)
        self.assertIn("обрезано до лимита 3: 4 дальних соседей", text)
        self.assertIn("Справочник С1", text)

    def test_subsystem_area_shows_composition(self):
        # подсистема в области → состав виден (кластер)
        out = str(self.t.root / "d.md")
        rc, _ = run_main(f"{self.src}/Subsystems/Учёт", "--src-root", self.src,
                         "--out", out)
        text = Path(out).read_text(encoding="utf-8")
        self.assertEqual(rc, 0)
        self.assertIn('|"состав"|', text)
        self.assertIn("Документ Д1", text)

    def test_subsystem_composition_not_a_neighbor_edge(self):
        # обычная область: состав подсистемы НЕ тащит узлы и рёбра
        out = str(self.t.root / "d.md")
        run_main(f"{self.src}/Documents/Д2", "--src-root", self.src,
                 "--out", out)
        text = Path(out).read_text(encoding="utf-8")
        self.assertNotIn('|"состав"|', text)
        self.assertNotIn("Подсистема Учёт", text)

    def test_broken_xml_area_warns_and_skips(self):
        # критерий 7: битый XML области → WARN, узел без рёбер, exit 0
        self.t.write("Catalogs/Битый/Битый.mdo", "<?xml broken")
        rc, text = run_main(f"{self.src}/Catalogs/Битый", "--out",
                            str(self.t.root / "d.md"))
        self.assertEqual(rc, 0)
        out = (self.t.root / "d.md").read_text(encoding="utf-8")
        self.assertIn("XML не разобран", out)
        self.assertIn("Справочник Битый", out)

    def test_no_mdo_stub(self):
        # критерий 8: правки только в модулях → заглушка, exit 0
        self.t.write("CommonModules/Общ/Module.bsl", "Процедура Тест() КонецПроцедуры")
        rc, text = run_main(f"{self.src}/CommonModules")
        self.assertEqual(rc, 0)
        self.assertIn("нет", text)

    def test_diff_without_mdo_stub_git(self):
        # критерий 8, git-ветка: дифф без .mdo → заглушка, exit 0
        try:
            g = ["git", "-c", "user.email=t@t", "-c", "user.name=t"]
            subprocess.run(g + ["init", "-q"], cwd=self.t.root, check=True,
                           capture_output=True)
            (self.t.root / "README.md").write_text("x", encoding="utf-8")
            subprocess.run(g + ["add", "."], cwd=self.t.root, check=True,
                           capture_output=True)
            subprocess.run(g + ["commit", "-qm", "init"], cwd=self.t.root,
                           check=True, capture_output=True)
        except (subprocess.CalledProcessError, OSError):
            self.skipTest("git недоступен")
        old = os.getcwd()
        os.chdir(self.t.root)
        try:
            rc, text = run_main("--diff", "HEAD")
        finally:
            os.chdir(old)
        self.assertEqual(rc, 0)
        self.assertIn("нет", text)

    def test_no_src_root_warning(self):
        # отклонение 3а: объект вне дерева выгрузки, src-root не выводится —
        # только рёбра самих объектов области, предупреждение в шапке
        # (внутри дерева src-root выводится автоматически, как у слоя)
        solo = self.t.root / "solo" / "Д1"
        solo.mkdir(parents=True, exist_ok=True)
        (solo / "Д1.mdo").write_text(mdo(
            "Document",
            _props("Д1", register_records=("AccumulationRegister.Р1",),
                   attrs=[("Ссылка", ("CatalogRef.С1",))])), encoding="utf-8")
        rc, text = run_main(str(solo))
        self.assertEqual(rc, 0)
        self.assertIn("соседи недоступны", text)
        self.assertIn("Регистр накопления Р1", text)  # свои движения видны

    def test_cache_roundtrip(self):
        cache = self.t.root / "cache"
        out = str(self.t.root / "d.md")
        rc, _ = run_main(f"{self.src}/Documents/Д1", "--src-root", self.src,
                         "--cache-dir", str(cache), "--out", out)
        self.assertEqual(rc, 0)
        files = list(cache.glob("diagram-edges-*.json"))
        self.assertEqual(len(files), 1, "кэш индекса рёбер не создан")
        import json
        data = json.loads(files[0].read_text(encoding="utf-8"))
        self.assertIn("edges", data)
        rc2, _ = run_main(f"{self.src}/Documents/Д1", "--src-root", self.src,
                          "--cache-dir", str(cache), "--out", out)
        self.assertEqual(rc2, 0)
        self.assertEqual(len(list(cache.glob("diagram-edges-*.json"))), 1)

    def test_exit_2_usage_errors(self):
        self.assertEqual(run_main()[0], 2)                      # нет входов
        self.assertEqual(run_main("/нет/такого/пути")[0], 2)    # путь не найден
        self.assertEqual(run_main(self.src, "--depth", "-1")[0], 2)
        self.assertEqual(run_main(self.src, "--max-nodes", "0")[0], 2)


if __name__ == "__main__":
    unittest.main()
