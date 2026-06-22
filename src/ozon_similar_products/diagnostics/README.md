# Диагностика данных

Модуль `diagnostics` содержит вспомогательные функции для проверки данных, parquet-файлов и логики сессий.

Этот слой не строит рекомендации и не меняет данные. Его задача — помогать быстро отвечать на вопросы:

* какие колонки есть в таблице;
* сколько пропусков в важных полях;
* как распределены действия пользователей;
* сколько строк лежит в parquet-разделах;
* какие временные разрывы возникают между событиями пользователя;
* разумно ли выбран `session_timeout_minutes`.

## Что делает модуль

```text
raw / clean events / parquet dataset
→ diagnostics helpers
→ компактные диагностические таблицы
```

Функции модуля работают с Polars `DataFrame` и `LazyFrame`, а часть parquet-проверок работает напрямую по путям к
файлам.

## Основные файлы

| Файл                                     | Что в нём находится                               |
|------------------------------------------|---------------------------------------------------|
| [`profiling.py`](profiling.py)           | профилирование таблиц и parquet-датасетов         |
| [`session_checks.py`](session_checks.py) | проверка временных разрывов и сессионных маркеров |
| [`__init__.py`](__init__.py)             | публичный экспорт диагностических функций         |

## Публичные функции

Из модуля можно импортировать:

```python
from ozon_similar_products.diagnostics import (
    action_profile,
    add_session_markers,
    null_profile,
    parquet_dataset_overview,
    parquet_partition_profile,
    partition_row_counts,
    schema_overview,
    time_diff_summary,
    time_diff_summary_by_partition,
)
```

## Проверка схемы таблицы

### `schema_overview`

`schema_overview` возвращает список колонок и типов данных.

Пример:

```python
from ozon_similar_products.diagnostics import schema_overview

schema = schema_overview(events)
print(schema)
```

Результат — таблица вида:

```text
column      | dtype
user_id     | Int64
timestamp   | Datetime
action_type | String
item_id     | Int64
```

Это удобно использовать в EDA, когда нужно быстро понять, что реально лежит в таблице.

## Проверка пропусков

### `null_profile`

`null_profile` считает число и долю пропусков по выбранным колонкам.

Пример:

```python
from ozon_similar_products.diagnostics import null_profile

profile = null_profile(
    events,
    columns=["user_id", "timestamp", "action_type", "item_id"],
)

print(profile)
```

На выходе получается таблица:

```text
column
row_count
null_count
null_share
```

Эта проверка полезна перед очисткой событий. Например, если много строк без `item_id`, это объясняет, почему после
`EventCleaner` стало меньше данных.

## Профиль действий

### `action_profile`

`action_profile` показывает, какие типы действий есть в таблице и насколько они заполнены.

Пример:

```python
from ozon_similar_products.diagnostics import action_profile

profile = action_profile(events)
print(profile)
```

Функция группирует строки по `action_type` и считает:

```text
rows
users
items
item_id_missing_rows
search_query_missing_rows
share
item_id_missing_share
search_query_missing_share
```

Эта диагностика помогает понять:

* какие действия доминируют в логах;
* у каких действий чаще отсутствует `item_id`;
* почему `search` обычно не попадает в товарные события;
* достаточно ли сильных сигналов вроде `click`, `favorite`, `to_cart`.

## Проверка parquet-датасета

### `parquet_files`

Внутренняя функция `parquet_files` находит parquet-файлы внутри директории или возвращает сам файл, если на вход передан
путь к parquet-файлу.

Обычно напрямую удобнее использовать более высокоуровневые функции ниже.

### `parquet_partition_profile`

`parquet_partition_profile` строит профиль parquet-файлов и извлекает Hive-разделы из путей.

Пример:

```python
from ozon_similar_products.diagnostics import parquet_partition_profile

profile = parquet_partition_profile("data/raw/user_actions")
print(profile)
```

Результат содержит:

```text
file_path
rows
file_size_bytes
date
action_type
```

Если в пути есть части вида `date=2024-04-23` или `action_type=view`, они будут вынесены в отдельные колонки.

### `partition_row_counts`

`partition_row_counts` агрегирует количество строк и размер файлов по разделам.

Пример:

```python
from ozon_similar_products.diagnostics import partition_row_counts

counts = partition_row_counts(
    "data/raw/user_actions",
    partition_columns=("date", "action_type"),
)

print(counts)
```

Это удобно для проверки, что данные действительно есть по нужным датам и типам действий.

### `parquet_dataset_overview`

`parquet_dataset_overview` возвращает общий размер parquet-датасета.

Пример:

```python
from ozon_similar_products.diagnostics import parquet_dataset_overview

overview = parquet_dataset_overview("data/raw/user_actions")
print(overview)
```

На выходе одна строка:

```text
files
rows
file_size_bytes
```

Эта функция полезна для быстрой оценки объёма данных перед тяжёлым запуском.

## Проверка сессий

### `add_session_markers`

`add_session_markers` сортирует события внутри пользователя и добавляет диагностические колонки:

```text
time_diff_seconds
is_new_session
session_index
```

Пример:

```python
from ozon_similar_products.diagnostics import add_session_markers

marked = add_session_markers(
    events,
    timeout_minutes=20,
)

print(marked.collect().head())
```

Функция не заменяет `SessionBuilder`. Она нужна для диагностики временных разрывов и проверки выбранного session
timeout.

## Сводка временных разрывов

### `time_diff_summary`

`time_diff_summary` считает общую сводку временных разрывов между событиями пользователя.

Пример:

```python
from ozon_similar_products.diagnostics import time_diff_summary

summary = time_diff_summary(
    events,
    timeout_minutes=20,
)

print(summary)
```

В результате есть поля:

```text
events
time_diffs
negative_time_diffs
zero_time_diffs
gaps_over_timeout
sessions
timeout_seconds
p50_seconds
p75_seconds
p90_seconds
p95_seconds
p99_seconds
```

Эта проверка помогает понять:

* сколько событий попало в расчёт;
* есть ли отрицательные временные разрывы;
* сколько событий имеют одинаковый timestamp;
* сколько разрывов больше session timeout;
* сколько сессий получится при выбранном пороге.

## Сводка по разделам

### `time_diff_summary_by_partition`

`time_diff_summary_by_partition` считает такую же сводку, но отдельно по разделу, чаще всего по дате.

Пример:

```python
from ozon_similar_products.diagnostics import time_diff_summary_by_partition

summary = time_diff_summary_by_partition(
    events,
    partition_col="event_date",
    timeout_minutes=20,
)

print(summary)
```

Это полезно, если нужно найти день, где резко изменилась структура событий или появились странные временные разрывы.

## Когда использовать diagnostics

Модуль полезен в нескольких сценариях.

### Перед первым запуском проекта

Проверить, что данные распакованы и читаются:

```python
from ozon_similar_products.diagnostics import parquet_dataset_overview

print(parquet_dataset_overview("data/raw/user_actions"))
print(parquet_dataset_overview("data/raw/product_information"))
```

### Перед настройкой `EventCleaner`

Понять, какие действия есть в raw events:

```python
from ozon_similar_products.data import load_events
from ozon_similar_products.diagnostics import action_profile

events = load_events(sample_days=1, sample_rows=10000)

print(action_profile(events))
```

### Перед выбором session timeout

Посмотреть распределение временных разрывов:

```python
from ozon_similar_products.diagnostics import time_diff_summary

print(time_diff_summary(events, timeout_minutes=20))
print(time_diff_summary(events, timeout_minutes=30))
```

### При отладке больших запусков

Понять, в каких разделах больше всего строк:

```python
from ozon_similar_products.diagnostics import partition_row_counts

counts = partition_row_counts(
    "data/raw/user_actions",
    partition_columns=("date", "action_type"),
)

print(counts)
```

## Где находится в проекте

`diagnostics` — вспомогательный слой рядом с основным конвейером.

```text
data
→ preprocessing
→ features
→ retrieval
→ business
→ output
→ serving

diagnostics
  ↳ проверяет данные и промежуточные результаты
```

Он может использоваться до, во время или после основного запуска, но не является обязательным этапом построения
рекомендаций.

## Границы ответственности

Что делает `diagnostics`:

* показывает схему таблицы;
* считает пропуски по колонкам;
* строит профиль действий;
* читает parquet-метаданные;
* считает строки и размеры файлов по разделам;
* добавляет диагностические маркеры сессий;
* считает сводки временных разрывов.

Что не делает `diagnostics`:

* не готовит архивы;
* не очищает события;
* не строит production-сессии;
* не считает популярность товаров;
* не строит пары товаров;
* не рассчитывает `score`;
* не сохраняет итоговые рекомендации;
* не заменяет тесты и контракты данных.

## Что менять осторожно

| Что менять                       | Почему осторожно                                      |
|----------------------------------|-------------------------------------------------------|
| названия диагностических колонок | их могут использовать ноутбуки и проверки             |
| логику `add_session_markers`     | она должна соответствовать смыслу session timeout     |
| расчёт `time_diff_summary`       | влияет на интерпретацию сессионных разрывов           |
| чтение parquet-метаданных        | должно работать без полной загрузки больших датасетов |
| extraction Hive-разделов из пути | влияет на профили по `date` и `action_type`           |

Если меняется логика сессий в [`preprocessing`](../preprocessing/README.md), стоит проверить, что диагностические
функции в [`session_checks.py`](session_checks.py) всё ещё помогают корректно интерпретировать временные разрывы.

## Быстрая проверка

Пример быстрой диагностики raw events:

```python
from ozon_similar_products.data import load_events
from ozon_similar_products.diagnostics import (
    action_profile,
    null_profile,
    schema_overview,
    time_diff_summary,
)

events = load_events(sample_days=1, sample_rows=10000)

print(schema_overview(events))
print(null_profile(events, columns=["user_id", "timestamp", "action_type", "item_id"]))
print(action_profile(events))
print(time_diff_summary(events, timeout_minutes=20))
```

## Связанные документы

| Документ                                                           | Что смотреть                            |
|--------------------------------------------------------------------|-----------------------------------------|
| [`../data/README.md`](../data/README.md)                           | как читаются исходные данные            |
| [`../preprocessing/README.md`](../preprocessing/README.md)         | как очищаются события и строятся сессии |
| [`../pipeline/README.md`](../pipeline/README.md)                   | как запускается полный конвейер         |
| [`../../../notebooks/README.md`](../../../notebooks/README.md)     | как использовать диагностику в EDA      |
| [`../../../docs/data_contract.md`](../../../docs/data_contract.md) | контракты таблиц и колонок              |
| [`../../../scripts/README.md`](../../../scripts/README.md)         | команды подготовки и проверки проекта   |

## Коротко

`diagnostics` — это набор вспомогательных функций для проверки данных и сессий.

Он помогает понять структуру таблиц, пропуски, распределение действий, размер parquet-датасетов и временные разрывы
между событиями.

Модуль не строит рекомендации и не участвует в production-логике напрямую, но помогает быстрее находить проблемы в
данных и параметрах запуска.
