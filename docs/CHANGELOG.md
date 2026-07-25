# Журнал изменений

Формат — [Keep a Changelog](https://keepachangelog.com/ru/1.1.0/), версии — [SemVer](https://semver.org/lang/ru/).

## [Unreleased]

### Добавлено

- `scripts/validate_skills.py` — автопроверка структуры скилов (без зависимостей, только stdlib). Проверяет: frontmatter, `name`=каталог + kebab-case + префикс `1c-`, `description` ≤1024 (warn >250), `license: MIT`, тело ≤500 строк, отсутствие проектной специфики (`торо_`/`гкс_`/`Project/Toir`), наличие ссылок на `references/*.md` с учётом cross-skill. Exit code: 0/1.

### Изменено

- `docs/ARCHITECTURE.md` — поправлены устаревшие «19 скилов» → 21, статусы этапов 2/3 (завершены, не «в работе»).
- `1c-project-bootstrap` — примеры параметров обезличены (убраны `торо_`/`гкс_`/`Project/Toir/src/` из таблицы).

## [0.1.0] — 2026-07-23

Первая публичная версия плагина. Каркас + 21 скил + 28 references + 15 std-справочников.

### Добавлено

**Каркас и документация**
- `.zcode-plugin/plugin.json` — манифест плагина ZCode (name: `1c-dev-rules`, v0.1.0)
- `README.md` — инструкция подключения и список скилов
- `LICENSE` — MIT
- `docs/ARCHITECTURE.md` — как устроен набор (для контрибьюторов)
- `docs/SOURCES.md` — upstream-источники (v8std, ai_rules_1c, cc-1c-skills)

**Скилы (21)**
- `1c-dispatch-gate` — главный маршрутизатор (гейт + таблица A/B/C) + 2 references
- `1c-project-bootstrap` — генератор `AGENTS.md` для нового проекта + 2 templates + 1 reference
- `1c-arch-checklist` — 7 вопросов эксперта до написания кода
- `1c-thin-triggers` — чек-лист самопроверки по 5 слоям
- `1c-code-review` — чек-лист ревью MR по секциям
- `1c-queries` — запросы, СКД, динамические списки + 2 references (queries-full, dcs-design)
- `1c-forms` — формы, архетипы, типовые + 4 references
- `1c-metadata` — метаданные, XML, скелеты, регистры + 4 references
- `1c-events-transactions` — проведение, блокировки, транзакции + 3 references
- `1c-logging` — журналирование, исключения + 1 reference
- `1c-performance` — производительность + 1 reference
- `1c-security` — права, RLS + 1 reference
- `1c-scheduled` — регламентные задания + 1 reference
- `1c-integration` — интеграция, обмен, файлы + 1 reference
- `1c-async` — асинхронные методы, запрет модальности + 1 reference
- `1c-extensions` — расширения (CFE), перехватчики + 1 reference
- `1c-reports` — отчёты, печатные формы + 1 reference
- `1c-ui-ux` — командный интерфейс, стилистика + 2 references
- `1c-localization` — локализация, `НСтр` + 1 reference
- `1c-patterns` — SOLID/GRASP/инженерные паттерны + 1 reference
- `1c-platform-support` — ParentConfigurations.bin, редактируемость БСП

**Справочный слой**
- `std/` — 15 файлов оглавлений v8std (перенесено из исходного проекта)

### Особенности
- Все скилы универсальны — не содержат упоминаний конкретных конфигураций (`торо_`, `Project/Toir`).
- Каждое `SKILL.md` валидно: `name` совпадает с каталогом, `description` ≤250 символов.
- Все ссылки на стандарты — на `github.com/zeegin/v8std/blob/main/docs/std/NNN.md`.
- References очищены от проектной специфики.

## Источники

- [zeegin/v8std](https://github.com/zeegin/v8std) — стандарты разработки 1С
- [comol/ai_rules_1c](https://github.com/comol/ai_rules_1c) — практика кодирования (public domain)
- [Nikolay-Shirokov/cc-1c-skills](https://github.com/Nikolay-Shirokov/cc-1c-skills) — спецификации форматов 1С
