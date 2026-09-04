#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
test_validate_new_object.py — тесты скрипта scripts/validate-new-object.sh.

Строит минимальные фикстурные деревья исходников 1С (src/<Тип>/<Объект>/…)
во временных каталогах и гоняет скрипт через subprocess:
  - валидный справочник            → exit 0, без FAIL;
  - сломанный справочник           → exit 1, FAIL по M1/M4/X6/X7;
  - валидное регламентное задание  → exit 0 (X8 methodName, X9 Schedule);
  - РЗ с битым methodName          → exit 1, FAIL по X8;
  - ошибки использования           → exit 2.

Запуск: python3 -m unittest tests.test_validate_new_object -v
Требует bash (скрипт — bash); на Windows тесты пропускаются.
"""
from __future__ import annotations

import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
SCRIPT = REPO_ROOT / "scripts" / "validate-new-object.sh"

U1 = "11111111-1111-1111-1111-111111111111"
U2 = "22222222-2222-2222-2222-222222222222"
U3 = "33333333-3333-3333-3333-333333333333"

CATALOG_MDO = f"""<?xml version="1.0" encoding="UTF-8"?>
<mdclass:Catalog xmlns:mdclass="http://v8.1c.ru/8.1/data/enterprise/current-config" uuid="{U1}">
\t<name>ТестовыйСправочник</name>
\t<synonym><key>ru</key><value>Тестовый справочник</value></synonym>
\t<attributes uuid="{U2}">
\t\t<name>Организация</name>
\t</attributes>
</mdclass:Catalog>
"""

CATALOG_MDO_BROKEN = f"""<?xml version="1.0" encoding="UTF-8"?>
<mdclass:Catalog xmlns:mdclass="http://v8.1c.ru/8.1/data/enterprise/current-config" uuid="{U1}">
\t<name>ТестовыйСправочник</name>
\t<attributes uuid="{U2}">
\t\t<name>Организация</name>
\t</attributes>
\t<attributes uuid="{U2}">
\t\t<name>Контрагент</name>
\t</attributes>
</mdclass:Catalog>
"""

SCHEDULED_JOB_MDO = f"""<?xml version="1.0" encoding="UTF-8"?>
<mdclass:ScheduledJob xmlns:mdclass="http://v8.1c.ru/8.1/data/enterprise/current-config" uuid="{U3}">
\t<name>ТестовоеЗадание</name>
\t<synonym><key>ru</key><value>Тестовое задание</value></synonym>
\t<methodName>CommonModule.ТестовыйОбщий.ВыполнитьОбработку</methodName>
\t<predefined>true</predefined>
</mdclass:ScheduledJob>
"""

SCHEDULED_JOB_MDO_BROKEN = SCHEDULED_JOB_MDO.replace(
    "CommonModule.ТестовыйОбщий.ВыполнитьОбработку",
    "CommonModule.ТестовыйОбщий.НесуществующийМетод",
)

COMMON_MODULE_BSL = """Процедура ВыполнитьОбработку() Экспорт
\t// тело
КонецПроцедуры
"""


def write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


class ValidateNewObjectCase(unittest.TestCase):
    """Базовый класс: временное дерево src/, запуск скрипта."""

    def setUp(self) -> None:
        if sys.platform == "win32":
            self.skipTest("validate-new-object.sh требует bash (POSIX)")
        if not shutil.which("bash"):
            self.skipTest("bash не найден в PATH")
        self.tmp = Path(tempfile.mkdtemp(prefix="vno-test-"))
        self.src = self.tmp / "src"
        self.src.mkdir(parents=True)

    def tearDown(self) -> None:
        shutil.rmtree(self.tmp, ignore_errors=True)

    def run_script(self, *args: str) -> subprocess.CompletedProcess:
        return subprocess.run(
            ["bash", str(SCRIPT), *args],
            capture_output=True, text=True, timeout=120,
        )

    # --- фикстуры ---------------------------------------------------------

    def make_catalog(self, broken: bool = False) -> Path:
        obj = self.src / "Catalogs" / "ТестовыйСправочник"
        obj.mkdir(parents=True)
        write(obj / "ТестовыйСправочник.mdo",
              CATALOG_MDO_BROKEN if broken else CATALOG_MDO)
        config = "<Configuration>\n"
        if not broken:
            config += "\t<catalogs>Catalog.ТестовыйСправочник</catalogs>\n"
        config += "</Configuration>\n"
        write(self.src / "Configuration" / "Configuration.mdo", config)
        if not broken:
            write(self.src / "Roles" / "ТестоваяРоль" / "Role.rights",
                  "<Rights>\n\t<object>Catalog.ТестовыйСправочник</object>\n</Rights>\n")
        return obj

    def make_scheduled_job(self, broken: bool = False) -> Path:
        obj = self.src / "ScheduledJobs" / "ТестовоеЗадание"
        obj.mkdir(parents=True)
        write(obj / "ТестовоеЗадание.mdo",
              SCHEDULED_JOB_MDO_BROKEN if broken else SCHEDULED_JOB_MDO)
        write(obj / "Schedule.schedule", "<schedule/>\n")
        write(self.src / "Configuration" / "Configuration.mdo",
              "<Configuration>\n\t<scheduledJobs>ScheduledJob.ТестовоеЗадание</scheduledJobs>\n</Configuration>\n")
        write(self.src / "CommonModules" / "ТестовыйОбщий" / "Module.bsl",
              COMMON_MODULE_BSL)
        return obj


class TestValidObjects(ValidateNewObjectCase):
    def test_valid_catalog_exit_0(self) -> None:
        obj = self.make_catalog()
        r = self.run_script(str(obj))
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertNotIn("❌ FAIL", r.stdout)
        self.assertIn("metadata_scan", r.stdout)  # слой выполнен (M1/X6/X7 и др. — в нём)

    def test_valid_scheduled_job_exit_0(self) -> None:
        obj = self.make_scheduled_job()
        r = self.run_script(str(obj))
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertNotIn("❌ FAIL", r.stdout)
        self.assertIn("metadata-слой", r.stdout)  # X8/X9 переехали в слой; успех = PASS слоя


class TestBrokenObjects(ValidateNewObjectCase):
    def test_broken_catalog_exit_1(self) -> None:
        obj = self.make_catalog(broken=True)
        r = self.run_script(str(obj))
        self.assertEqual(r.returncode, 1, r.stdout + r.stderr)
        self.assertIn("DuplicateUUIDInMDO", r.stdout)  # M1 в слое
        self.assertIn("MDOWithoutSynonym", r.stdout)  # M4 в слое — ключ каталога №474
        self.assertIn("ObjectNotInConfiguration", r.stdout)  # X6 в слое
        self.assertIn("❌ FAIL", r.stdout)

    def test_broken_scheduled_job_method_exit_1(self) -> None:
        obj = self.make_scheduled_job(broken=True)
        r = self.run_script(str(obj))
        self.assertEqual(r.returncode, 1, r.stdout + r.stderr)
        self.assertIn("ScheduledJobHandlerMissing", r.stdout)  # X8 в слое


class TestUsageErrors(ValidateNewObjectCase):
    def test_no_args_exit_2(self) -> None:
        r = self.run_script()
        self.assertEqual(r.returncode, 2, r.stdout + r.stderr)

    def test_missing_dir_exit_2(self) -> None:
        r = self.run_script(str(self.tmp / "нет" / "такого" / "каталога"))
        self.assertEqual(r.returncode, 2, r.stdout + r.stderr)


if __name__ == "__main__":
    unittest.main()
