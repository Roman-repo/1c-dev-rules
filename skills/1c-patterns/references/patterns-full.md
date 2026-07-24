# Карточка: Паттерны проектирования (уровень архитектуры)

> Резюме раздела [`docs/patterns/`](https://github.com/zeegin/v8std/tree/main/docs/patterns) репозитория v8std.
> Это уровень **выше стандартов**: не «как писать код», а «как проектировать». Паттерны «приземлены» на 1С.

## Зачем этот раздел

Каталог стандартов отвечает на вопрос «как написать код по правилам». Эта карточка отвечает на вопрос **«где разместить логику и как не связать систему в узел»**. Для ИИ, проектирующей архитектуру (например, «положить ли это в форму или общий модуль»), это ценный контекст.

В 1С часть архитектурных понятий закреплена **не конструкциями языка, а стандартами** — поэтому паттерны здесь связаны со стандартами явно.

---

## Инженерные принципы

Практические правила, которые не дают архитектуре усложниться без причины.

| Принцип | Суть | На что смотреть в 1С |
|---|---|---|
| [**DRY**](https://github.com/zeegin/v8std/blob/main/docs/patterns/engineering/dry/index.md) (Don't Repeat Yourself) | Не дублируйте знание в нескольких местах | Не придётся ли синхронно править один смысл в трёх модулях |
| [**KISS**](https://github.com/zeegin/v8std/blob/main/docs/patterns/engineering/kiss/index.md) (Keep It Simple) | Выбирайте простое решение, если оно уже закрывает задачу | Не появился ли паттерн там, где достаточно процедуры |
| [**YAGNI**](https://github.com/zeegin/v8std/blob/main/docs/patterns/engineering/yagni/index.md) (You Aren't Gonna Need It) | Не стройте расширение заранее без подтверждённой потребности | Не проектируете ли вы «на будущее», которого нет |
| [**Rule of Three**](https://github.com/zeegin/v8std/blob/main/docs/patterns/engineering/rule-of-three/index.md) | Обобщать стоит после устойчивого повторения | Не выносите ли абстракцию слишком рано |
| [**Separation of Concerns**](https://github.com/zeegin/v8std/blob/main/docs/patterns/engineering/separation-of-concerns/index.md) | Разделяйте разные типы задач по слоям | Не смешаны ли UI, бизнес-логика и доступ к данным |

→ **Связь со стандартами:** Separation of Concerns прямо отсылает к [№487](https://github.com/zeegin/v8std/blob/main/docs/std/487.md) (минимизация серверных вызовов) и [№737](https://github.com/zeegin/v8std/blob/main/docs/std/737.md) (проверка прав). См. также [retrieval-rules.md](./retrieval-rules.md).

---

## SOLID

Пять принципов устойчивой объектной архитектуры. В 1С — как набор проверочных вопросов.

| Принцип | Суть | На что смотреть в 1С |
|---|---|---|
| [**SRP**](https://github.com/zeegin/v8std/blob/main/docs/patterns/solid/single-responsibility/index.md) (Single Responsibility) | У модуля одна причина для изменения | Не смешаны ли расчёт, проведение, печать и интеграции |
| [**OCP**](https://github.com/zeegin/v8std/blob/main/docs/patterns/solid/open-closed/index.md) (Open/Closed) | Лучше расширять, чем переписывать | Можно ли добавить вариант без правки стабильного кода |
| [**LSP**](https://github.com/zeegin/v8std/blob/main/docs/patterns/solid/liskov-substitution/index.md) (Liskov Substitution) | Подстановка реализации не ломает ожиданий | Можно ли безопасно заменить реализацию |
| [**ISP**](https://github.com/zeegin/v8std/blob/main/docs/patterns/solid/interface-segregation/index.md) (Interface Segregation) | Контракты должны быть узкими | Не заставляете ли клиентов зависеть от лишних методов |
| [**DIP**](https://github.com/zeegin/v8std/blob/main/docs/patterns/solid/dependency-inversion/index.md) (Dependency Inversion) | Высокоуровневая логика зависит от абстракций | Не знает ли бизнес-модуль слишком много о конкретной интеграции |

---

## GRASP

Принципы распределения ответственности. Не каталог готовых шаблонов, а набор вопросов: кто должен знать, кто создавать, кто координировать.

| Принцип | Суть | На что смотреть |
|---|---|---|
| [**Information Expert**](https://github.com/zeegin/v8std/blob/main/docs/patterns/grasp/information-expert/index.md) | Ответственность у того, у кого есть данные | Не тянуть логику в случайный модуль, если контекст рядом |
| [**Creator**](https://github.com/zeegin/v8std/blob/main/docs/patterns/grasp/creator/index.md) | Создаёт тот, кто тесно связан с объектом | Кто агрегирует/хранит/инициализирует сущность |
| [**Controller**](https://github.com/zeegin/v8std/blob/main/docs/patterns/grasp/controller/index.md) | Внешние запросы принимает координатор сценария | Кто управляет use case целиком |
| [**Low Coupling**](https://github.com/zeegin/v8std/blob/main/docs/patterns/grasp/low-coupling/index.md) | Зависимостей должно быть меньше | Насколько дорого менять модуль из-за другого |
| [**High Cohesion**](https://github.com/zeegin/v8std/blob/main/docs/patterns/grasp/high-cohesion/index.md) | У модуля узкая понятная зона ответственности | Не превращается ли модуль в склад разнородной логики |
| [**Polymorphism**](https://github.com/zeegin/v8std/blob/main/docs/patterns/grasp/polymorphism/index.md) | Поведение по вариантам — в реализации, не в условиях | Можно ли заменить `Если…ИначеЕсли` на контракт |
| [**Pure Fabrication**](https://github.com/zeegin/v8std/blob/main/docs/patterns/grasp/pure-fabrication/index.md) | Служебный объект, если он уменьшает связанность | Где сервис полезнее перегрузки домена чужой логикой |
| [**Indirection**](https://github.com/zeegin/v8std/blob/main/docs/patterns/grasp/indirection/index.md) | Промежуточный слой ослабляет прямую зависимость | Нужен ли адаптер/фасад для развязки модулей |
| [**Protected Variations**](https://github.com/zeegin/v8std/blob/main/docs/patterns/grasp/protected-variations/index.md) | Точки изменения закрываются стабильным интерфейсом | Где отделить контракт от реализации |

---

## GOF — 23 классических паттерна

Приводятся с «приземлением» на 1С: как идея раскладывается на объекты метаданных, общие модули и формы.

### Порождающие

| Паттерн | Суть |
|---|---|
| [Abstract Factory](https://github.com/zeegin/v8std/blob/main/docs/patterns/gof/abstract-factory/index.md) | Создаёт семейства связанных объектов через единый интерфейс |
| [Builder](https://github.com/zeegin/v8std/blob/main/docs/patterns/gof/builder/index.md) | Разделяет пошаговую сборку сложного объекта и итоговое представление |
| [Factory Method](https://github.com/zeegin/v8std/blob/main/docs/patterns/gof/factory-method/index.md) | Делегирует создание специализированному методу |
| [Prototype](https://github.com/zeegin/v8std/blob/main/docs/patterns/gof/prototype/index.md) | Создаёт объекты копированием настроенного экземпляра |
| [Singleton](https://github.com/zeegin/v8std/blob/main/docs/patterns/gof/singleton/index.md) | Один экземпляр и глобальная точка доступа |

### Структурные

| Паттерн | Суть |
|---|---|
| [Adapter](https://github.com/zeegin/v8std/blob/main/docs/patterns/gof/adapter/index.md) | Приводит несовместимый интерфейс к ожидаемому |
| [Bridge](https://github.com/zeegin/v8std/blob/main/docs/patterns/gof/bridge/index.md) | Разделяет абстракцию и реализацию для независимого развития |
| [Composite](https://github.com/zeegin/v8std/blob/main/docs/patterns/gof/composite/index.md) | Единообразная работа с одиночными объектами и деревьями |
| [Decorator](https://github.com/zeegin/v8std/blob/main/docs/patterns/gof/decorator/index.md) | Добавляет поведение через обёртку |
| [Facade](https://github.com/zeegin/v8std/blob/main/docs/patterns/gof/facade/index.md) | Простой вход к сложной подсистеме |
| [Flyweight](https://github.com/zeegin/v8std/blob/main/docs/patterns/gof/flyweight/index.md) | Выносит общую часть состояния для экономии памяти |
| [Proxy](https://github.com/zeegin/v8std/blob/main/docs/patterns/gof/proxy/index.md) | Подстановкой контролирует доступ к реальному объекту |

### Поведенческие

| Паттерн | Суть |
|---|---|
| [Chain of Responsibility](https://github.com/zeegin/v8std/blob/main/docs/patterns/gof/chain-of-responsibility/index.md) | Запрос по цепочке обработчиков, пока кто-то не возьмёт |
| [Command](https://github.com/zeegin/v8std/blob/main/docs/patterns/gof/command/index.md) | Действие как отдельный объект |
| [Interpreter](https://github.com/zeegin/v8std/blob/main/docs/patterns/gof/interpreter/index.md) | Грамматика и интерпретация выражений |
| [Iterator](https://github.com/zeegin/v8std/blob/main/docs/patterns/gof/iterator/index.md) | Обход коллекции без раскрытия внутреннего устройства |
| [Mediator](https://github.com/zeegin/v8std/blob/main/docs/patterns/gof/mediator/index.md) | Централизует взаимодействие, уменьшая прямые связи |
| [Memento](https://github.com/zeegin/v8std/blob/main/docs/patterns/gof/memento/index.md) | Сохранение и восстановление состояния |
| [Observer](https://github.com/zeegin/v8std/blob/main/docs/patterns/gof/observer/index.md) | Подписчики получают уведомления об изменениях |
| [State](https://github.com/zeegin/v8std/blob/main/docs/patterns/gof/state/index.md) | Поведение по состояниям в отдельных объектах |
| [Strategy](https://github.com/zeegin/v8std/blob/main/docs/patterns/gof/strategy/index.md) | Взаимозаменяемые алгоритмы за единым интерфейсом |
| [Template Method](https://github.com/zeegin/v8std/blob/main/docs/patterns/gof/template-method/index.md) | Каркас алгоритма, шаги на расширение |
| [Visitor](https://github.com/zeegin/v8std/blob/main/docs/patterns/gof/visitor/index.md) | Операции над структурой в отдельном посетителе |

Примеры реализаций на 1С — в репозитории [zeegin/DesignPatterns](https://github.com/zeegin/DesignPatterns).

---

## Принципы ООП и их формализация в 1С

В 1С архитектурные понятия часто закреплены **стандартами**, а не конструкциями языка.

| Понятие | Как формализовано в 1С |
|---|---|
| **Интерфейс** | Документированный набор экспортных методов в области `ПрограммныйИнтерфейс` — [№551](https://github.com/zeegin/v8std/blob/main/docs/std/551.md), [№455](https://github.com/zeegin/v8std/blob/main/docs/std/455.md), [№453](https://github.com/zeegin/v8std/blob/main/docs/std/453.md) |
| **Инкапсуляция** | Скрытие внутренней реализации; программный интерфейс модуля формы вынесен — [№630](https://github.com/zeegin/v8std/blob/main/docs/std/630.md), [№678](https://github.com/zeegin/v8std/blob/main/docs/std/678.md) |
| **Полиморфизм** | Общий контракт у разных реализаций (все объекты с методом `Записать()`); замена `Если…ИначеЕсли` на контракт |
| **Наследование** | Платформенное (метаданные порождают типы); пользовательского наследования классов нет — использовать композицию |

---

## Как пользоваться (для ИИ-модели)

1. **При проектировании модуля/подсистемы** — проверьте GRASP (кто за что отвечает) и SOLID (не перегружен ли модуль).
2. **При разрастании условий** (`Если…ИначеЕсли` по типам) — смотрите Polymorphism / Strategy.
3. **При дублировании логики** — DRY, но с оглядкой на Rule of Three (не выносите абстракцию слишком рано).
4. **При смешивании UI и логики в форме** — Separation of Concerns → стандарты №487, №629, №630.

## Предостережение

Паттерны — не догма. В 1С часто достаточно простой процедуры или запроса. Применять паттерн только если он решает реальную проблему, а не усложняет решение (KISS).

Первоисточник: [github.com/zeegin/v8std/tree/main/docs/patterns](https://github.com/zeegin/v8std/tree/main/docs/patterns)
