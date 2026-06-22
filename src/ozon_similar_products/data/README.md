# Слой данных

Модуль `data` отвечает за входные данные проекта **OZON Similar Products**: подготовку исходных архивов, поиск
parquet-файлов, чтение таблиц и проверку ожидаемых колонок.

Этот слой стоит первым в конвейере обработки. Его задача — дать остальному проекту понятный и проверенный вход: товары,
пользовательские события и набор общих схем, на которые дальше опираются очистка событий, построение сессий, расчёт
похожести и сохранение рекомендаций.

## Что делает модуль

Модуль `data` отвечает за четыре вещи:

1. Подготовить исходные архивы с данными.
2. Найти нужные parquet-файлы после распаковки.
3. Прочитать товары и пользовательские события.
4. Проверить, что в таблицах есть ожидаемые колонки.

Публичный вход в модуль находится в [`__init__.py`](__init__.py).

```python
from ozon_similar_products.data import (
    load_configs,
    load_events,
    load_products,
    scan_events,
    scan_products,
)
```

## Какие данные мы ожидаем

Проект работает с двумя основными наборами данных.

```text
product_information
user_actions
```

`product_information` — таблица с товарами.

`user_actions` — пользовательские события: просмотры, клики, добавления в избранное, добавления в корзину и другие
действия, которые приходят в исходных логах.

Названия архивов, ожидаемые колонки и известные типы действий описаны в [
`../../../configs/data.yaml`](../../../configs/data.yaml).

Контракты таблиц описаны в [`../../../docs/data_contract.md`](../../../docs/data_contract.md).

## Как данные попадают в проект

Исходные архивы нужно положить в папку:

```text
data/raw/archives/
```

Ожидаемые архивы:

```text
product_information.tar.gz
user_actions.tar.gz
```

После этого данные готовятся командой:

```bash
uv run python scripts/prepare_raw_data.py
```

Во время подготовки мы:

* проверяем, что архив существует;
* безопасно распаковываем `.tar.gz`;
* ищем parquet-файлы внутри распакованной структуры;
* переносим содержимое в целевую папку;
* удаляем служебные `_SUCCESS`-файлы;
* записываем маркер `.prepared.json`;
* оставляем `.gitkeep`, чтобы пустые папки сохранялись в репозитории.

Если данные уже подготовлены, повторный запуск не распаковывает архив заново. Для полной пересборки используется флаг:

```bash
uv run python scripts/prepare_raw_data.py --force
```

Посмотреть содержимое архивов без распаковки:

```bash
uv run python scripts/prepare_raw_data.py --preview
```

Подробнее:

* [`../../../data/raw/README.md`](../../../data/raw/README.md);
* [`../../../scripts/README.md`](../../../scripts/README.md);
* [`../../../configs/README.md`](../../../configs/README.md).

## Основные файлы модуля

| Файл                             | Что в нём находится                                   |
|----------------------------------|-------------------------------------------------------|
| [`__init__.py`](__init__.py)     | публичные функции чтения данных                       |
| [`archives.py`](archives.py)     | подготовка исходных архивов и запись `.prepared.json` |
| [`config.py`](config.py)         | совместимая обёртка для старых импортов               |
| [`partitions.py`](partitions.py) | работа с дневными разделами пользовательских событий  |
| [`readers.py`](readers.py)       | чтение товаров и пользовательских событий             |
| [`schemas.py`](schemas.py)       | названия колонок и контракты таблиц                   |
| [`validation.py`](validation.py) | проверки, что в таблицах есть нужные колонки          |

## Чтение пользовательских событий

Для пользовательских событий есть два варианта чтения.

### `scan_events`

Функция возвращает ленивую таблицу Polars.

```python
from ozon_similar_products.data import scan_events

events = scan_events(
    start_date="2024-04-01",
    end_date="2024-04-07",
    action_types=["view", "click", "favorite", "to_cart"],
)
```

Этот вариант лучше использовать в конвейере обработки, потому что данные не загружаются в память сразу.

### `load_events`

Функция загружает события в память.

```python
from ozon_similar_products.data import load_events

events = load_events(
    start_date="2024-04-01",
    end_date="2024-04-07",
    action_types=["view", "click"],
)
```

По умолчанию `load_events` читает только первый доступный день, чтобы случайно не загрузить весь датасет в память. Если
передать явный диапазон дат, функция читает именно этот диапазон.

## Чтение товаров

Для товаров тоже есть два варианта.

### `scan_products`

Функция возвращает ленивую таблицу Polars.

```python
from ozon_similar_products.data import scan_products

products = scan_products()
```

### `load_products`

Функция загружает таблицу товаров в память.

```python
from ozon_similar_products.data import load_products

products = load_products()
```

Если нужны только отдельные колонки, их можно указать явно:

```python
products = load_products(
    columns=["item_id", "name", "brand", "category_name"],
)
```

## Как устроены разделы событий

Пользовательские события читаются из структуры с разделами по датам и типам действий.

Ожидаемый вид:

```text
data/raw/user_actions/
  date=2024-04-01/
    action_type=view/
      ...
    action_type=click/
      ...
  date=2024-04-02/
    action_type=view/
      ...
```

Модуль [`partitions.py`](partitions.py) умеет:

* найти доступные даты;
* выбрать конкретные даты;
* выбрать диапазон дат;
* ограничить чтение первыми N днями;
* отфильтровать события по типам действий;
* собрать список подходящих parquet-файлов.

Если по заданным фильтрам parquet-файлы не найдены, модуль явно сообщает об ошибке.

## Схемы и колонки

В [`schemas.py`](schemas.py) хранятся общие названия колонок, которые используются в разных частях проекта.

Например:

```text
RAW_EVENTS_COLUMNS
CLEAN_EVENTS_COLUMNS
PRODUCT_INFORMATION_COLUMNS
SESSIONS_COLUMNS
ITEM_POPULARITY_COLUMNS
DAILY_ITEM_PAIRS_COLUMNS
PAIR_AGGREGATES_COLUMNS
PAIR_SCORES_COLUMNS
RECOMMENDATIONS_COLUMNS
WIDGET_OUTPUT_COLUMNS
```

Это нужно, чтобы разные слои проекта опирались на одни и те же контракты.

Например, если слой очистки событий создаёт таблицу `clean_events`, следующие этапы ожидают в ней не произвольные поля,
а конкретный набор колонок из `CLEAN_EVENTS_COLUMNS`.

Подробное описание таблиц находится в [`../../../docs/data_contract.md`](../../../docs/data_contract.md).

## Проверка таблиц

В [`validation.py`](validation.py) собраны проверки колонок.

Главная идея простая: если таблица не содержит обязательные поля, лучше упасть сразу на границе слоя, чем получить
непонятную ошибку дальше по конвейеру обработки.

Примеры проверок:

```python
from ozon_similar_products.data.validation import (
    validate_raw_events,
    validate_product_information,
    validate_clean_events,
)

validate_raw_events(events)
validate_product_information(products)
validate_clean_events(clean_events)
```

Проверки работают и с обычными таблицами Polars, и с ленивыми таблицами.

## Границы ответственности

Модуль `data` не очищает события и не строит рекомендации.

Он отвечает только за входной слой:

```text
архивы
→ parquet-файлы
→ чтение таблиц
→ проверка колонок
```

Что делает `data`:

* готовит архивы;
* читает товары;
* читает пользовательские события;
* выбирает нужные даты и типы действий;
* проверяет наличие обязательных колонок;
* хранит общие контракты таблиц.

Что не делает `data`:

* не фильтрует события по бизнес-логике;
* не строит пользовательские сессии;
* не считает популярность товаров;
* не строит пары товаров;
* не рассчитывает похожесть;
* не сохраняет итоговые рекомендации.

Эти задачи выполняются в следующих модулях:

| Задача                         | Модуль                                        |
|--------------------------------|-----------------------------------------------|
| очистка событий                | [`preprocessing`](../preprocessing/README.md) |
| построение сессий              | [`preprocessing`](../preprocessing/README.md) |
| популярность товаров           | [`features`](../features/README.md)           |
| пары товаров и похожесть       | [`retrieval`](../retrieval/README.md)         |
| резервные рекомендации         | [`business`](../business/README.md)           |
| сохранение результата          | [`output`](../output/README.md)               |
| получение готовых рекомендаций | [`serving`](../serving/README.md)             |

## Типовой путь данных

```text
data/raw/archives/
  product_information.tar.gz
  user_actions.tar.gz

→ scripts/prepare_raw_data.py

data/raw/
  product_information/
  user_actions/

→ ozon_similar_products.data.readers

Polars DataFrame / LazyFrame
```

Дальше эти таблицы передаются в следующие этапы конвейера обработки.

## Что менять осторожно

В этом модуле особенно важно аккуратно относиться к контрактам данных.

| Что менять                                                 | Почему осторожно                                                                                 |
|------------------------------------------------------------|--------------------------------------------------------------------------------------------------|
| [`../../../configs/data.yaml`](../../../configs/data.yaml) | от него зависят ожидаемые архивы, колонки и типы действий                                        |
| [`schemas.py`](schemas.py)                                 | эти колонки используют следующие слои проекта                                                    |
| [`validation.py`](validation.py)                           | слишком слабая проверка пропустит ошибочные данные, слишком строгая может сломать рабочий запуск |
| [`partitions.py`](partitions.py)                           | от выбора файлов зависит, какие события попадут в обработку                                      |
| [`readers.py`](readers.py)                                 | это общий вход для товаров и пользовательских событий                                            |

Если меняется структура входных данных, нужно проверить не только этот модуль, но и [
`preprocessing`](../preprocessing/README.md), [`features`](../features/README.md), [`retrieval`](../retrieval/README.md)
и [`evaluation`](../evaluation/README.md).

## Быстрая проверка

Подготовить данные:

```bash
uv run python scripts/prepare_raw_data.py
```

Проверить структуру проекта и наличие данных:

```bash
uv run python scripts/check_project_structure.py
```

Прочитать небольшой пример событий:

```python
from ozon_similar_products.data import load_events

events = load_events(sample_days=1, sample_rows=1000)
print(events.head())
```

Прочитать товары:

```python
from ozon_similar_products.data import load_products

products = load_products()
print(products.head())
```

## Связанные документы

| Документ                                                           | Что смотреть                                  |
|--------------------------------------------------------------------|-----------------------------------------------|
| [`../../../data/raw/README.md`](../../../data/raw/README.md)       | куда класть исходные архивы                   |
| [`../../../configs/README.md`](../../../configs/README.md)         | настройки путей, данных и запусков            |
| [`../../../docs/data_contract.md`](../../../docs/data_contract.md) | подробные контракты таблиц                    |
| [`../../../docs/architecture.md`](../../../docs/architecture.md)   | место слоя данных в архитектуре               |
| [`../../../scripts/README.md`](../../../scripts/README.md)         | команды подготовки данных                     |
| [`../preprocessing/README.md`](../preprocessing/README.md)         | что происходит с событиями после чтения       |
| [`../pipeline/README.md`](../pipeline/README.md)                   | как слой данных используется в полном запуске |

## Коротко

Мы используем модуль `data` как единый вход в данные проекта.

Он готовит архивы, находит parquet-файлы, читает товары и события, проверяет колонки и отдаёт дальше уже понятные
таблицы.

Всё, что связано с очисткой, сессиями, похожестью и рекомендациями, начинается после этого слоя.
