# Статистики товаров

Модуль `features` считает статистики по очищенным пользовательским событиям.

Он находится после [`preprocessing`](../preprocessing/README.md) и до построения похожих товаров в [
`retrieval`](../retrieval/README.md).

Главная задача модуля — превратить `clean_events` в компактные признаки, которые дальше используются для scoring,
fallback-рекомендаций, диагностики и оценки качества.

## Что делает модуль

```text
clean events
→ item popularity
→ action type distribution
```

В текущей версии основной класс модуля:

```text
ItemPopularityBuilder
```

Он считает:

* популярность товаров;
* количество событий по каждому типу действия;
* число уникальных пользователей;
* распределение типов действий в train-окне.

## Основные файлы

| Файл                                       | Что в нём находится                                  |
|--------------------------------------------|------------------------------------------------------|
| [`item_popularity.py`](item_popularity.py) | расчёт популярности товаров и распределения действий |
| [`__init__.py`](__init__.py)               | публичный экспорт функций и классов модуля           |

## Входные данные

На вход модуль получает `clean_events`.

Эта таблица создаётся в [`preprocessing`](../preprocessing/README.md).

Ожидаемые поля:

```text
user_id
event_date
timestamp
action_type
item_id
search_query
widget_name
```

Для расчёта статистик особенно важны:

| Поле          | Зачем нужно                                     |
|---------------|-------------------------------------------------|
| `item_id`     | понять, по какому товару было действие          |
| `user_id`     | посчитать уникальных пользователей              |
| `action_type` | разложить события по каналам поведения          |
| `event_date`  | понимать период, за который строится статистика |

Подробный контракт описан в [`../../../docs/data_contract.md`](../../../docs/data_contract.md).

## `ItemPopularityBuilder`

`ItemPopularityBuilder` строит две таблицы:

```text
item_popularity
action_type_distribution
```

Пример использования:

```python
from ozon_similar_products.features.item_popularity import ItemPopularityBuilder

builder = ItemPopularityBuilder(
    item_action_types=["view", "click", "favorite", "to_cart"],
)

item_popularity = builder.build_item_popularity(clean_events)
action_distribution = builder.build_action_type_distribution(
    clean_events,
    calibration_start="2024-04-17",
    calibration_end="2024-04-23",
)
```

В полном конвейере объект создаётся и вызывается внутри [`pipeline`](../pipeline/README.md).

## `item_popularity`

`item_popularity` — таблица фактической популярности товаров за выбранное окно.

Контракт:

| Поле              | Что означает                           |
|-------------------|----------------------------------------|
| `item_id`         | идентификатор товара                   |
| `events_count`    | общее число товарных событий по товару |
| `unique_users`    | число уникальных пользователей         |
| `views_count`     | число событий `view`                   |
| `clicks_count`    | число событий `click`                  |
| `favorites_count` | число событий `favorite`               |
| `to_cart_count`   | число событий `to_cart`                |

Пример смысла строки:

```text
item_id = 100
events_count = 250
unique_users = 90
views_count = 180
clicks_count = 45
favorites_count = 15
to_cart_count = 10
```

Это означает, что товар `100` встретился в 250 событиях, а с ним взаимодействовали 90 уникальных пользователей.

## Зачем нужна популярность товаров

Популярность используется в нескольких местах.

### Для fallback-рекомендаций

Если для товара не хватает поведенческих похожих товаров, fallback-слой может добрать кандидатов из популярных товаров.

Например:

```text
популярные товары в той же категории
популярные товары того же типа
популярные товары того же бренда
глобально популярные товары
```

Подробнее: [`business/README.md`](../business/README.md).

### Для нормализации scoring

Популярные товары могут слишком часто попадать в пары просто потому, что их видят многие пользователи.

Scoring может учитывать популярность, чтобы ослабить перекос в сторону слишком популярных товаров.

Подробнее: [`retrieval/README.md`](../retrieval/README.md).

### Для диагностики

Популярность помогает быстро проверить:

* какие товары доминируют в событиях;
* есть ли перекос в сторону просмотров;
* достаточно ли сильных действий;
* не строятся ли рекомендации только вокруг самых популярных товаров.

Подробнее: [`diagnostics/README.md`](../diagnostics/README.md).

## `action_type_distribution`

`action_type_distribution` показывает, как часто разные действия встречаются в train-окне.

Контракт:

| Поле                | Что означает                              |
|---------------------|-------------------------------------------|
| `action_type`       | тип действия                              |
| `events_count`      | число событий этого типа                  |
| `event_share`       | доля действия среди всех товарных событий |
| `unique_users`      | число пользователей, совершивших действие |
| `unique_items`      | число товаров, по которым было действие   |
| `calibration_start` | начало периода калибровки                 |
| `calibration_end`   | конец периода калибровки                  |

Пример:

```text
action_type = view
events_count = 100000
event_share = 0.80

action_type = to_cart
events_count = 3000
event_share = 0.024
```

Это значит, что просмотры встречаются намного чаще, чем добавления в корзину.

## Зачем нужно распределение действий

Разные действия имеют разную частоту.

Просмотров обычно много, добавлений в корзину — меньше. Если просто суммировать действия без учёта частот, частые слабые
сигналы могут перебить редкие сильные сигналы.

`action_type_distribution` нужен для калибровки scoring.

Например, `CoVisitationScorer` может учитывать:

```text
business weight
частоту действия в train-окне
счётчики пары
нормализацию по популярности
```

Важно: `action_type_distribution` сам не меняет события и не назначает финальный `score`. Он только сохраняет статистику
для следующего слоя.

## Что модуль не делает

`features` не строит рекомендации.

Он не должен:

* очищать сырые события;
* строить пользовательские сессии;
* строить пары товаров;
* считать итоговый `score`;
* выбирать top-K;
* добавлять fallback-рекомендации;
* сохранять итоговую выдачу.

Эти задачи выполняются в других слоях:

| Задача                        | Модуль                                        |
|-------------------------------|-----------------------------------------------|
| очистка событий и сессии      | [`preprocessing`](../preprocessing/README.md) |
| пары товаров, scoring и top-K | [`retrieval`](../retrieval/README.md)         |
| fallback-рекомендации         | [`business`](../business/README.md)           |
| сохранение результата         | [`output`](../output/README.md)               |
| полный запуск                 | [`pipeline`](../pipeline/README.md)           |

## Место в конвейере

```text
data
→ preprocessing
→ features
→ retrieval
→ business
→ output
```

Более подробно:

```text
raw events
→ clean events
→ item popularity
→ action type distribution
→ pair aggregates
→ pair scores
→ recommendations
```

`features` работает с уже очищенными событиями и отдаёт статистики, которые используются дальше.

## Что менять осторожно

| Что менять                               | Почему осторожно                                  |
|------------------------------------------|---------------------------------------------------|
| список `item_action_types`               | влияет на то, какие действия попадут в статистики |
| названия колонок популярности            | downstream-слои ожидают конкретный контракт       |
| расчёт `unique_users`                    | влияет на диагностику и fallback                  |
| распределение `action_type_distribution` | влияет на калибровку scoring                      |
| фильтрацию действий                      | можно случайно потерять сильные сигналы           |

Если меняется контракт `item_popularity` или `action_type_distribution`, нужно проверить:

* [`data_contract.md`](../../../docs/data_contract.md);
* [`retrieval`](../retrieval/README.md);
* [`business`](../business/README.md);
* [`pipeline`](../pipeline/README.md);
* [`evaluation`](../evaluation/README.md).

## Быстрая проверка

Пример ручной проверки:

```python
from ozon_similar_products.data import load_events
from ozon_similar_products.preprocessing.clean_events import EventCleaner
from ozon_similar_products.features.item_popularity import ItemPopularityBuilder

raw_events = load_events(sample_days=1, sample_rows=1000)

cleaner = EventCleaner(
    item_action_types=["view", "click", "favorite", "to_cart"],
)
clean_events = cleaner.transform_day(raw_events)

builder = ItemPopularityBuilder(
    item_action_types=["view", "click", "favorite", "to_cart"],
)

item_popularity = builder.build_item_popularity(clean_events)
action_distribution = builder.build_action_type_distribution(
    clean_events,
    calibration_start="2024-04-01",
    calibration_end="2024-04-01",
)

print(item_popularity.head())
print(action_distribution)
```

Для полного запуска лучше использовать команды из [`../../../scripts/README.md`](../../../scripts/README.md).

## Связанные документы

| Документ                                                           | Что смотреть                                 |
|--------------------------------------------------------------------|----------------------------------------------|
| [`../data/README.md`](../data/README.md)                           | чтение исходных событий и товаров            |
| [`../preprocessing/README.md`](../preprocessing/README.md)         | создание `clean_events`                      |
| [`../retrieval/README.md`](../retrieval/README.md)                 | scoring, где используются статистики         |
| [`../business/README.md`](../business/README.md)                   | fallback-рекомендации на основе популярности |
| [`../pipeline/README.md`](../pipeline/README.md)                   | как статистики используются в полном запуске |
| [`../diagnostics/README.md`](../diagnostics/README.md)             | диагностика данных и результатов             |
| [`../../../configs/README.md`](../../../configs/README.md)         | настройки действий и scoring                 |
| [`../../../docs/data_contract.md`](../../../docs/data_contract.md) | контракты таблиц                             |
| [`../../../docs/architecture.md`](../../../docs/architecture.md)   | место слоя features в архитектуре            |

## Коротко

`features` считает статистики по очищенным событиям.

Главные результаты:

```text
item_popularity
action_type_distribution
```

`item_popularity` показывает, насколько товары популярны.

`action_type_distribution` показывает, как распределены типы действий.

Эти таблицы не являются рекомендациями и не содержат финальный `score`, но помогают scoring, fallback-слою и
диагностике.
