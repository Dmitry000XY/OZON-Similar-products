# Работа с архивами и загрузчиками данных

Документ описывает, как подготовить локальные данные из архивов и как читать их через data readers.

Все команды ниже должны выполняться **из корня проекта** — из папки, где лежат `pyproject.toml`,
`configs/`, `scripts/` и `src/`.

## Быстрый старт

Если терминал открыт в папке `docs/`, перейдите в корень проекта относительным путём:

```bash
cd ..
```

Проверьте, что вы находитесь в корне проекта:

```bash
uv run python -c "from pathlib import Path; root = Path.cwd(); required = ['pyproject.toml', 'configs', 'scripts', 'src']; print(root); missing = [path for path in required if not (root / path).exists()]; assert not missing, f'Missing project files or dirs: {missing}'; print('Project root OK')"
```

Положите архивы в папку:

```text
data/raw/archives/
```

Ожидаемые файлы:

```text
data/raw/archives/user_actions.tar.gz
data/raw/archives/product_information.tar.gz
```

Подготовьте данные:

```bash
uv run python scripts/prepare_raw_data.py
```

Проверьте, что загрузчики работают:

```bash
uv run python -c "from ozon_similar_products.data import load_configs, load_events, load_products; cfg = load_configs(); print(load_products(cfg).shape); print(load_events(cfg, sample_rows=100000).shape)"
```

Ожидаемый результат:

```text
(130035, 6)
(100000, 7)
```

Если эти две строки вывелись, архивы распакованы корректно, а загрузчики видят подготовленные parquet-файлы.

> В PyCharm команды из блоков `bash` можно запускать кнопкой запуска рядом с блоком Markdown, если она отображается.
> Если кнопки нет, откройте терминал PyCharm в корне проекта и выполните команду вручную.

---

## Что делает скрипт подготовки

Для подготовки данных используется скрипт:

```text
scripts/prepare_raw_data.py
```

Он:

- читает пути и имена архивов из `configs/paths.yaml` и `configs/data.yaml`;
- проверяет, что архивы лежат в `data/raw/archives/`;
- распаковывает архивы в `data/raw/`;
- проверяет, что после распаковки появились parquet-файлы;
- удаляет служебные файлы `_SUCCESS`;
- создаёт служебный файл `.prepared.json`.

Архивы не нужно распаковывать вручную через Проводник или файловый менеджер.

---

## Структура после подготовки

После успешного запуска ожидается такая структура:

```text
data/
  raw/
    archives/
      user_actions.tar.gz
      product_information.tar.gz

    user_actions/
      date=2024-03-01/
        action_type=click/
          *.parquet
        action_type=favorite/
          *.parquet
        action_type=search/
          *.parquet
        action_type=to_cart/
          *.parquet
        action_type=view/
          *.parquet
      date=2024-03-02/
      ...

    product_information/
      *.parquet
```

`user_actions` — parquet-датасет с действиями пользователей. Он партиционирован по `date` и `action_type`.

`product_information` — parquet-файл или набор parquet-файлов со справочником товаров.

---

## Команды для подготовки данных

Все команды выполняются из корня проекта.

### Посмотреть содержимое архивов

```bash
uv run python scripts/prepare_raw_data.py --preview
```

Команда ничего не распаковывает. Она только показывает первые пути внутри архивов.

### Распаковать архивы

```bash
uv run python scripts/prepare_raw_data.py
```

Если данные уже подготовлены и есть `.prepared.json`, повторный запуск пропустит готовые датасеты.

### Распаковать заново

```bash
uv run python scripts/prepare_raw_data.py --force
```

Используйте `--force`, если архивы были заменены, предыдущая распаковка была прервана или нужно гарантированно
пересобрать папки `data/raw/user_actions/` и `data/raw/product_information/`.

---

## Как читать данные в Python

Чтение данных лежит в файле:

```text
src/ozon_similar_products/data/readers.py
```

Импортируйте публичные функции так:

```python
from ozon_similar_products.data import (
    load_configs,
    load_events,
    load_products,
    scan_events,
    scan_products,
)
```

Сначала загрузите конфиги:

```python
config = load_configs()
```

`load_configs()` читает:

```text
configs/paths.yaml
configs/data.yaml
```

Пути к данным, имена архивов, glob-паттерны и ожидаемые колонки должны храниться в конфигах, а не в ноутбуках.

---

## Публичные функции data readers

### `load_configs(config_dir="configs", project_root=None)`

Загружает конфиги проекта.

| Параметр       | Что означает                                                                                      |
|----------------|---------------------------------------------------------------------------------------------------|
| `config_dir`   | Папка с конфигами относительно корня проекта. Обычно менять не нужно.                             |
| `project_root` | Явный путь к корню проекта. Если не передан, корень ищется автоматически по `configs/paths.yaml`. |

Возвращает словарь с ключами:

```text
project_root
paths
data
```

---

### `load_products(config=None, columns=None, validate=True)`

Читает справочник товаров в память и возвращает `polars.DataFrame`.

| Параметр   | Что означает                                                               |
|------------|----------------------------------------------------------------------------|
| `config`   | Конфиг из `load_configs()`. Если не передан, будет загружен автоматически. |
| `columns`  | Список колонок, которые нужно оставить. Если `None`, читаются все колонки. |
| `validate` | Проверять ли наличие ожидаемых колонок из `configs/data.yaml`.             |

Пример:

```python
from ozon_similar_products.data import load_configs, load_products

config = load_configs()
products = load_products(config)

print(products.shape)
print(products.head())
```

На текущих данных ожидаемый размер:

```text
(130035, 6)
```

---

### `scan_products(config=None, columns=None, validate=True)`

Лениво читает справочник товаров и возвращает `polars.LazyFrame`.

Используйте `scan_products()`, если дальше планируются фильтрации, join, group by или другие операции Polars, которые не
нужно выполнять сразу.

| Параметр   | Что означает                                                               |
|------------|----------------------------------------------------------------------------|
| `config`   | Конфиг из `load_configs()`. Если не передан, будет загружен автоматически. |
| `columns`  | Список колонок, которые нужно оставить. Если `None`, читаются все колонки. |
| `validate` | Проверять ли наличие ожидаемых колонок из `configs/data.yaml`.             |

Пример:

```python
from ozon_similar_products.data import load_configs, scan_products

config = load_configs()
products_lazy = scan_products(config)
```

---

### `load_events(...)`

Читает действия пользователей в память и возвращает `polars.DataFrame`.

Сигнатура:

```python
load_events(
    config=None,
    *,
    use_sample=True,
    sample_days=1,
    sample_rows=None,
    dates=None,
    start_date=None,
    end_date=None,
    action_types=None,
    columns=None,
    validate=True,
)
```

| Параметр       | Что означает                                                                                                                |
|----------------|-----------------------------------------------------------------------------------------------------------------------------|
| `config`       | Конфиг из `load_configs()`. Если не передан, будет загружен автоматически.                                                  |
| `use_sample`   | Включает безопасный режим по умолчанию: если даты явно не указаны, читается только `sample_days` первых доступных дней.     |
| `sample_days`  | Сколько первых дней читать в sample-режиме. По умолчанию `1`.                                                               |
| `sample_rows`  | Ограничение на число строк после выбора файлов. Например, `100_000` для быстрой EDA.                                        |
| `dates`        | Явный список дат, например `["2024-03-01", "2024-03-02"]`. Если передан, `sample_days` по умолчанию не обрезает список дат. |
| `start_date`   | Начало диапазона дат включительно.                                                                                          |
| `end_date`     | Конец диапазона дат включительно.                                                                                           |
| `action_types` | Один тип действия или список типов: `"click"` или `["view", "click"]`.                                                      |
| `columns`      | Список колонок, которые нужно оставить. Если `None`, читаются все колонки.                                                  |
| `validate`     | Проверять ли наличие ожидаемых колонок из `configs/data.yaml`.                                                              |

Важно про `use_sample=True`:

- это защита от случайной загрузки всего большого датасета;
- по умолчанию без явных дат читается только первый доступный день;
- если передать `dates`, `start_date` или `end_date`, явный выбор дат имеет приоритет над default sample-режимом;
- `sample_rows` применяется после выбора дат и типов действий: он ограничивает число возвращаемых строк, но не список
  дат.

Безопасный вариант для первого запуска:

```python
from ozon_similar_products.data import load_configs, load_events

config = load_configs()

events = load_events(
    config,
    sample_rows=100_000,
)

print(events.shape)
print(events.head())
```

Эквивалентно более явной записи:

```python
events = load_events(
    config,
    use_sample=True,
    sample_days=1,
    sample_rows=100_000,
)
```

Ожидаемый размер sample:

```text
(100000, 7)
```

---

### `scan_events(...)`

Лениво читает действия пользователей и возвращает `polars.LazyFrame`.

Сигнатура:

```python
scan_events(
    config=None,
    *,
    dates=None,
    start_date=None,
    end_date=None,
    action_types=None,
    sample_days=None,
    columns=None,
    validate=True,
)
```

| Параметр       | Что означает                                                                                                                                                      |
|----------------|-------------------------------------------------------------------------------------------------------------------------------------------------------------------|
| `config`       | Конфиг из `load_configs()`. Если не передан, будет загружен автоматически.                                                                                        |
| `dates`        | Явный список дат.                                                                                                                                                 |
| `start_date`   | Начало диапазона дат включительно.                                                                                                                                |
| `end_date`     | Конец диапазона дат включительно.                                                                                                                                 |
| `action_types` | Один тип действия или список типов.                                                                                                                               |
| `sample_days`  | Сколько первых дат оставить после фильтрации. В отличие от `load_events()`, здесь нет `use_sample`. Если нужно ограничение по дням, передайте `sample_days` явно. |
| `columns`      | Список колонок, которые нужно оставить.                                                                                                                           |
| `validate`     | Проверять ли наличие ожидаемых колонок из `configs/data.yaml`.                                                                                                    |

Используйте `scan_events()` для больших вычислений:

```python
from ozon_similar_products.data import load_configs, scan_events

config = load_configs()

events_lazy = scan_events(
    config,
    start_date="2024-03-01",
    end_date="2024-03-07",
    action_types=["view", "click", "to_cart"],
)

result = (
    events_lazy
    .group_by("item_id", "action_type")
    .len()
    .collect()
)

print(result.head())
```

---

## Колонки датасетов

Точный контракт колонок хранится в `configs/data.yaml`. Быстро посмотреть фактические колонки можно так:

```bash
uv run python -c "from ozon_similar_products.data import load_configs, load_events, load_products; cfg = load_configs(); print('products:', load_products(cfg).columns); print('events:', load_events(cfg, sample_rows=1).columns)"
```

### `user_actions`

Фактические колонки событий:

| Колонка        | Описание                                                                               |
|----------------|----------------------------------------------------------------------------------------|
| `user_id`      | Идентификатор пользователя.                                                            |
| `date`         | Дата события. Также используется как партиция в parquet-датасете.                      |
| `timestamp`    | Точное время события.                                                                  |
| `action_type`  | Тип действия пользователя: например, `view`, `click`, `search`, `to_cart`, `favorite`. |
| `widget_name`  | Название виджета или зоны интерфейса, где произошло действие.                          |
| `search_query` | Поисковый запрос пользователя, если он применим к событию.                             |
| `item_id`      | Идентификатор товара. Используется для связи с `product_information`.                  |

### `product_information`

Фактические колонки справочника товаров:

| Колонка         | Описание                                                                          |
|-----------------|-----------------------------------------------------------------------------------|
| `item_id`       | Внутренний идентификатор товара. Используется для связи с `user_actions.item_id`. |
| `name`          | Название товара.                                                                  |
| `brand`         | Бренд товара.                                                                     |
| `type`          | Тип товара.                                                                       |
| `category_id`   | Идентификатор категории товара.                                                   |
| `category_name` | Название категории товара.                                                        |

---

## Полезные примеры

### Загрузить только один тип действия

```python
events_click = load_events(
    config,
    action_types="click",
    sample_rows=100_000,
)
```

### Загрузить несколько типов действий

```python
events_subset = load_events(
    config,
    action_types=["view", "click", "to_cart"],
    sample_rows=100_000,
)
```

### Загрузить конкретные даты

```python
events = load_events(
    config,
    dates=["2024-03-01", "2024-03-02"],
    sample_rows=100_000,
)
```

### Загрузить диапазон дат

```python
events = load_events(
    config,
    start_date="2024-03-01",
    end_date="2024-03-07",
    sample_rows=100_000,
)
```

### Выбрать только нужные колонки

```python
events = load_events(
    config,
    columns=["user_id", "timestamp", "action_type", "item_id"],
    sample_rows=100_000,
)

products = load_products(
    config,
    columns=["item_id", "sku"],
)
```

---

## Рекомендуемый шаблон для ноутбуков

```python
from ozon_similar_products.data import load_configs, load_events, load_products

config = load_configs()

events = load_events(
    config,
    sample_rows=100_000,
)

products = load_products(config)
```

В ноутбуках не нужно вручную искать parquet-файлы и писать `pl.read_parquet(...)`.

---

## Что делать, если что-то пошло не так

### Не найден архив

Проверьте, что архивы лежат здесь:

```text
data/raw/archives/
```

И называются именно так:

```text
user_actions.tar.gz
product_information.tar.gz
```

### Не найдены parquet-файлы

Посмотрите содержимое архивов:

```bash
uv run python scripts/prepare_raw_data.py --preview
```

Потом распакуйте заново:

```bash
uv run python scripts/prepare_raw_data.py --force
```

### Загрузка слишком долгая

Ограничьте sample:

```python
events = load_events(
    config,
    sample_rows=100_000,
)
```

Или используйте lazy-загрузку:

```python
events_lazy = scan_events(config)
```

---

## Что не нужно делать

Не нужно:

- распаковывать архивы вручную через Проводник или файловый менеджер;
- коммитить архивы в Git;
- коммитить распакованные parquet-файлы;
- хардкодить абсолютные пути к данным в ноутбуках;
- вручную писать чтение parquet-файлов в каждом ноутбуке.

Правильный путь:

```text
архивы -> scripts/prepare_raw_data.py -> data/readers.py -> notebooks / baseline / pipeline
```
