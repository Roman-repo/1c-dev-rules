# Практика: Грабли ручной правки XML метаданных и форм

> Практические ловушки при ручном редактировании `*.mdo` (EDT) и `*.form` / `*.xml` форм.
> Адаптировано из [`metadata-xml-workarounds.md`](https://github.com/comol/ai_rules_1c/blob/main/content/rules/metadata-xml-workarounds.md) репозитория `ai_rules_1c`.

## Когда применять

При ручной правке метаданных XML/MDO и форм — когда нужно добавить реквизит, элемент формы, табличную часть **без визального редактора**. Это самые частые причины, по которым форма/объект перестаёт открываться.

---

## 🔴 1. `LineNumber` в табличных частях — НЕ добавлять

Табличные части **не должны** содержать `<standardAttributes>` с `LineNumber`. Платформа добавляет его автоматически; явная копия вызывает ошибку загрузки или дублирование.

```xml
<!-- НЕПРАВИЛЬНО — удалить этот блок из <tabularSections> -->
<standardAttributes>
  <dataHistory>Use</dataHistory>
  <name>LineNumber</name>
  <fillValue xsi:type="core:UndefinedValue"/>
  <fullTextSearch>Use</fullTextSearch>
</standardAttributes>
```

То же для `.mdo` (EDT) — не добавляйте `LineNumber` как `standardAttribute` к табличным частям.

📖 Стандарт: [№649](https://github.com/zeegin/v8std/blob/main/docs/std/649.md) Реквизиты

---

## 🔴 2. `PagesGroupExtInfo` — правильное имя типа (с буквой «s»)

Имя типа extInfo для группы страниц — `PagesGroupExtInfo` (с буквой **«s»**), не `PageGroupExtInfo`:

```xml
<!-- ПРАВИЛЬНО -->
<type>Pages</type>
<extInfo xsi:type="form:PagesGroupExtInfo">
  <pagesRepresentation>Auto</pagesRepresentation>
  <currentRowUse>Auto</currentRowUse>
</extInfo>
```

Опечатка `PageGroupExtInfo` **молча ломает форму** — группа страниц не загружается в Конфигураторе/EDT.

---

## 🔴 3. Элемент `Page` — обязательно `<enabled>true</enabled>`

Для каждого элемента `Page` внутри группы `Pages` флаг `<enabled>` **обязателен**. Без него Конфигуратор считает страницу отключённой (или форма не проходит валидацию):

```xml
<type>Page</type>
<enabled>true</enabled>
```

---

## 🔴 4. UID/UUID — глобально уникальные

Все UID/UUID новых объектов метаданных, реквизитов, табличных частей должны быть **глобально уникальны** в рамках конфигурации.

- Генерируйте каждый UUID отдельно: `[guid]::NewGuid()` (PowerShell), `uuid.uuid4()` (Python), `NewUUID` в EDT.
- **Никогда** не копируйте-вставляйте существующий UUID.
- **Никогда** не используйте плейсхолдеры/последовательности (`a1b2c3d4…`, `b2c3d4e5…`).
- После массовой генерации метаданных — запускайте проверку на дубликаты UUID по дереву исходников.

При добавлении объектов метаданных — также обновляйте `Configuration.xml` (порядок `<childObjects>` имеет значение).

---

## 🟡 5. Хук валидации после правки

После любой ручной правки XML метаданных — проверяйте:

- `verify_xml` из `1c-code-metadata-mcp` против соответствующей XSD (`get_xsd_schema` если не уверены в типе) — когда поднимем MCP.
- Sanity-загрузку через `1c-metadata-manage/tools/1c-meta-validate/scripts/meta-validate.ps1`.
- Вручную — импорт в EDT и проверка, что объект открывается.

Это ловит структурные проблемы (отсутствующие обязательные элементы, неверные имена типов, битые ссылки) до того, как они дойдут до платформы.

---

## 6. Конкретные чек-проверки при ручном ревью XML

> Инварианты, которые платформа проверяет неявно. Источник — разобранные скрипты
> валидации `form-validate` и `meta-validate` из [cc-1c-skills](https://github.com/Nikolay-Shirokov/cc-1c-skills),
> адаптированные под EDT-формат (`form:Form`, `mdclass:*`). Теги Конфигуратора
> (`ChildItems`, `AutoCommandBar`) переложены на EDT-эквиваленты (`items`, `autoCommandBar`).

Эти проверки **не заменяют EDT** — они дают список того, что искать глазами при ручной правке.

### 6.1. Проверки формы (`Form.form`)

| # | Что проверить | Как (EDT-теги) | Симптом при ошибке |
|---|---|---|---|
| F1 | **ID уникальны ВНУТРИ каждого пула** | Пулы независимы: `items` (элементы), `attributes` (реквизиты), `formCommands` (команды). ID=1 может быть и в `attributes`, и в `formCommands` одновременно — это **нормально**. Дубликат внутри одного пула (два `id=5` в `attributes`) — ошибка. `autoCommandBar` имеет фиксированный `id="-1"` | Дубликат внутри пула → форма не открывается |
| F2 | **Имена уникальны ВНУТРИ каждого пула** | Кнопка с `name=Загрузить` в `items` и команда `name=Загрузить` в `formCommands` — это **допустимо** (разные пулы). Два одинаковых `name` в `attributes` — ошибка | Дубликат имени внутри пула → ошибка загрузки |
| F3 | **Companion-элементы** у каждого типизированного элемента | `InputField`/`CheckBoxField`/`LabelField` → должны иметь `contextMenu` + `extendedTooltip`. `UsualGroup`/`Pages`/`Page`/`Button` → `extendedTooltip`. `TableField` → `contextMenu` + `autoCommandBar` + поисковые дополнения | Отсутствие → EDT пересоздаст при открытии, или визуальный баг |
| F4 | **DataPath ссылается на существующий атрибут** | `dataPath` вида `Объект.ИмяФайла` — корневой сегмент (`Объект`, `НоменклатураКЗагрузке`) должен быть в `<attributes>` | Несуществующий путь → «поле не найдено» в рантайме |
| F5 | **CommandName у кнопки разрешается** | `commandName` вида `Form.Command.Загрузить` — команда `Загрузить` должна быть в `<formCommands>`. `Form.StandardCommand.*` — всегда валидны | Несуществующая команда → кнопка не работает |
| F6 | **Обработчики событий непустые** | `<handlers><event>`/`<Event>` — текст обработчика должен быть заполнен (ссылается на процедуру модуля) | Пустой обработчик → событие не сработает |
| F7 | **Команда имеет Action** | `<formCommands>` → `action` должен быть непустым (содержит `handler`) | Команда без action → не выполняется |
| F8 | **MainAttribute ≤ 1** | Среди `attributes` — не более одного с `<main>true</main>` | Несколько главных → ошибка загрузки |
| F9 | **Title — многоязычный XML** | `title` формы должен содержать `key`/`value`, а не голый текст | Голый текст → локализация сломана |

### 6.2. Проверки объекта метаданных (`.mdo`)

| # | Что проверить | Как (EDT-теги) | Симптом при ошибке |
|---|---|---|---|
| M1 | **Корневой элемент и namespace** | Для EDT: корень `mdclass:Catalog`/`mdclass:Document`/`mdclass:DataProcessor`, namespace `http://g5.1c.ru/v8/dt/metadata/mdclass` | Неверный корень → EDT не загружает |
| M2 | **UUID на объекте и всех подобъектах** | Каждый `attributes`/`forms`/`tabularSections`/`commands` имеет `uuid`. Случайный, без дубликатов в проекте | Дубликат UUID → конфликт при загрузке |
| M3 | **Имя — валидный идентификатор 1С** | `name` соответствует правилам: без пробелов, без `ё`, с префиксом `<ПрефиксПроекта>`, начинается с буквы | Невалидное имя → ошибка загрузки |
| M4 | **Синоним заполнен** | `synonym` с `key` `ru` (и при необходимости `en`/`es`) — обязателен | Без синонима → пустое имя в интерфейсе |
| M5 | **ChildObjects — допустимые типы** | Внутри `Catalog` — только `attributes`, `tabularSections`, `forms`, `templates`, `commands`. Внутри `Document` — аналогично + `documentRegisterRecords` (БСП). Чужой тег → ошибка | Недопустимый тег → LoadConfigFromFiles падает |
| M6 | **Реквизиты: UUID, имя, тип** | Каждый `attributes` имеет `uuid`, `name`, и `<type>` с `types` внутри. Составной тип — по №728 | Нет типа → ошибка; невалидный UUID → конфликт |
| M7 | **Уникальность имён** | Имена `attributes`/табличных частей/форм уникальны в пределах объекта | Дубликат → ошибка загрузки |
| M8 | **TabularSections — корректная структура** | `tabularSections` содержит `attributes` с UUID/имя/тип. **Не** содержит `LineNumber` в `standardAttributes` (платформа добавляет сама) | Явный `LineNumber` → дублирование, ошибка |
| M9 | **Согласованность свойств** | `AccountingRegister` имеет `chartOfAccounts`; `CalculationRegister` — `chartOfCalculationTypes`; `EventSubscription` — `handler` (формат `CommonModule.Имя.Процедура`); `ScheduledJob` — `methodName` | Пустое обязательное поле → ошибка при загрузке |
| M10 | **Command — Group обязателен** | Каждая `commands` имеет `_group` (или корректную ссылку на `CommandGroup.Имя`). Тип параметра секционной группы несовместим с `CommandParameterType` | Команда без группы → 1С отвергает при загрузке |
| M11 | **Роли на новый объект (№532)** | Объект присутствует хотя бы в одном `<путь к src>/Roles/*/Rights.rights`: `grep -rl "Constant.Имя\|InformationRegister.Имя\|Catalog.Имя" <путь к src>/Roles/`. Для регистра/справочника — права и на измерения/реквизиты (`Dimension.Имя`, `Attribute.Имя`) | Нет ролей → объект невидим стандартным пользователям |
| M12 | **Регистр: свойства режима** | `writeMode` (`Independent`/`RecorderSubordinate`), `informationRegisterPeriodicity` (`Nonperiodic`/`RecorderPosition`/…), `editType` (`InDialog`/`Directly`), у измерений — `mainFilter`. Значения осмысленны и сверены с эталоном похожего регистра | Несоответствие режима → неверное чтение/запись (напр. набор записей нельзя отфильтровать по ресурсу) |
| M13 | **Константа: тип и квалификаторы** | `<type>` с `types` и квалификаторами (`numberQualifiers`/`stringQualifiers`/`dateQualifiers`). Для числа — `precision`/`nonNegative`. Составной тип — по №728. Заполнение значения по умолчанию — осмысленное | Нет квалификаторов → платформа подставляет умолчания, могут не совпадать с бизнес-смыслом |

### 6.3. Признаки для быстрой прикидки

Перед ручным ревью `.form`/`.mdo` — прогоните эти grep-проверки.
⚠ Внимание: ID и имена живут в **разных пулах** (`items`/`attributes`/`formCommands`).
Простой `uniq -d` по всему файлу даст ложные срабатывания — проверяйте каждый пул отдельно.

```bash
# Дубликаты UUID в .mdo (UUID глобальны — здесь простой uniq корректен)
grep -oE 'uuid="[0-9a-f-]+"' Объект.mdo | sort | uniq -d

# Дубликаты ID ВНУТРИ attributes (отдельный пул, не смешивать с items/formCommands)
sed -n '/<attributes>/,/<\/attributes>/p' Forms/Форма/Form.form | grep -oE '<id>[0-9]+</id>' | sort | uniq -d

# Companion-элементы: каждый InputField должен иметь extendedTooltip рядом
grep -A2 'xsi:type="form:InputField"' Forms/Форма/Form.form | grep -c "extendedTooltip"

# DataPath: корневой сегмент должен быть в attributes (ручная сверка)
grep -oE '<segments>[^<]+</segments>' Forms/Форма/Form.form
```

---

## Чек-лист перед коммитом правок XML

- [ ] В табличных частях нет `LineNumber` в `standardAttributes`
- [ ] Группы страниц используют `PagesGroupExtInfo` (с «s»)
- [ ] Каждый `Page` содержит `<enabled>true</enabled>`
- [ ] Все новые UUID сгенерированы, не скопированы
- [ ] `Configuration.xml` / `Configuration.mdo` обновлён (`childObjects`)
- [ ] Форма/объект открывается в EDT после правки
- [ ] **Прогнаны проверки из §6** (ID/DataPath/companions для формы; UUID/типы/синонимы для объекта)

## Связанные карточки

- Скил `1c-metadata` — правила метаданных уровня стандарта
- Скил `1c-metadata` → `module-templates` — шаблоны модулей
- Скил `1c-forms` → `typical-forms-new` — структура модуля формы, `modifiesStoredData`
- Скил `1c-forms` → `typical-forms-mod` — программная модификация форм

## Источники

- Конкретные инварианты в §6 — разобраны из скриптов `form-validate.ps1` (12 проверок) и
  `meta-validate.ps1` (16 проверок) репозитория [cc-1c-skills](https://github.com/Nikolay-Shirokov/cc-1c-skills),
  адаптированы под EDT-формат. Оригинальные скрипты работают под Конфигуратор (`xcf/logform`),
  суть инвариантов форматонезависима.
