# Журнал изменений

Формат — [Keep a Changelog](https://keepachangelog.com/ru/1.1.0/), версии — [SemVer](https://semver.org/lang/ru/).

## [Unreleased]

## [0.2.0] — 2026-07-25

Второй релиз: зрелость open-source + инструментирование. validate_skills.py научился видеть ссылки «наружу» (вскрыл ~60 мест проектной специфики ТОИР в карточках); добавлены тесты, CI, CONTRIBUTING. Карточки полностью обезличены до универсального состояния.

### Добавлено

- `scripts/validate_skills.py` — автопроверка структуры скилов (без зависимостей, только stdlib). Проверяет: frontmatter, `name`=каталог + kebab-case + префикс `1c-`, `description` ≤1024 (warn >250), `license: MIT`, тело ≤500 строк, отсутствие проектной специфики (`торо_`/`гкс_`/`Project/Toir`), наличие ссылок на `references/*.md` с учётом cross-skill **и** относительных markdown-ссылок «наружу» (в `std/` и cross-skill между карточками) с умным 3-уровневым резолвингом. Exit code: 0/1.
- `tests/` — юнит-тесты валидатора на stdlib `unittest`: 3 fixture-скила (эталон/сломанный/без-license) + интеграционный тест на реальных `skills/` (11 тестов, ~0.04 с).
- `.github/workflows/validate.yml` — CI на push/PR: гоняет валидатор и тесты (Python 3.11 на ubuntu-latest). Битая структура не попадёт в `main`.
- `CONTRIBUTING.md` — гайд для контрибьюторов: как добавить скил, правила оформления (универсальность, ссылки, версии), синхронизация с upstream.
- `.github/PULL_REQUEST_TEMPLATE.md` — чек-лист PR (CI зелёный, локальная проверка, универсальность, README/CHANGELOG обновлены).
- `.github/ISSUE_TEMPLATE/` — шаблоны `bug_report.yml` (under/over-trigger, устаревшее правило, битая ссылка) и `feature_request.yml` + `config.yml` (вопросы → Discussions).
- `SECURITY.md` — политика безопасности (поверхность атаки минимальна: нет рантайм-кода, только `.md` и CI-линтер; ответственное раскрытие через e-mail).

### Изменено

- **Зачистка проектной специфики ТОИР в 15 reference-карточках + 2 SKILL.md + 1 template** (~60 правок). Валидатор вскрыл: ссылки на `../../CLAUDE.md` (файл проекта toir2), на `../perf-reports/...` (отчёты toir2), блоки «Пример из проекта» с `Module.bsl:N`/`Forms/Форма/Form.form:6450`, «Важно для ТОИР», «инцидент Б6/Б7», конвенцию с примерами `РемонтыСервер`. Удалено/обобщено до нейтральных примеров (`Документ.РеализацияТоваровУслуг`, `ТоварыСервер`).
- **15 битых ссылок `standarts/std/` → `../../../std/`** во всех 14 reference-файлах (каталог называется `std/`, не `standarts/std/`; глубина — 3 уровня вверх). Наследие переноса из toir2.
- **Cross-skill ссылки** (~25 мест) приведены к единому виду «скил `1c-<имя>`» вместо битых `../<name>.md`/`./<file>.md` (скилы — каталоги, не `.md`-файлы).
- `docs/ARCHITECTURE.md` — поправлены устаревшие «19 скилов» → 21, статусы этапов 2/3 (завершены).
- `1c-project-bootstrap` — примеры параметров обезличены (убраны `торо_`/`гкс_`/`Project/Toir/src/` из таблицы; `<Префикс>Ремонты` → `<Префикс><Домен>`).
- Версия `0.1.0 → 0.2.0` в `plugin.json` и `marketplace.json`.

### Удалено

- `docs/handoff/промпт-новая-сессия.md` — внутренний артефакт разработки (перенос между сессиями), не для пользователей; засорял публичное лицо репозитория.

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
