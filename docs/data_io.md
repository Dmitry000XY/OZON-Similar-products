# Работа с архивами и загрузчиками данных

Этот документ описывает только две вещи:

1. как подготовить локальные данные из архивов;
2. как читать подготовленные данные через `loaders.py`.

---

## 1. Где должны лежать архивы

Скачанные архивы нужно положить в папку:

```text
data/raw/archives/
```

Ожидаемые файлы:

```text
data/raw/archives/user_actions.tar.gz
data/raw/archives/product_information.tar.gz
```

Архивы не нужно распаковывать вручную через Проводник.

Для распаковки используется скрипт:

```text
scripts/prepare_raw_data.py
```

---

## 2. Что лежит внутри архивов

### `user_actions.tar.gz`

Архив содержит parquet-датасет с действиями пользователей.

После распаковки ожидается структура:

```text
data/raw/user_actions/
  user_actions_3_months/
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
```

Датасет партиционирован по:

```text
date
action_type
```

Это позволяет читать не весь датасет сразу, а отдельные даты или отдельные типы действий.

### `product_information.tar.gz`

Архив содержит папку:

```text
product_information/
```

Внутри неё лежит parquet-файл со справочником товаров.

После распаковки ожидается структура:

```text
data/raw/product_information/
  *.parquet
```

---

## 3. Как посмотреть содержимое архивов

Из корня проекта:

```powershell
uv run python scripts/prepare_raw_data.py --preview
```

Команда ничего не распаковывает, а только показывает первые пути внутри архивов.

---

## 4. Как распаковать архивы

Из корня проекта:

```powershell
uv run python scripts/prepare_raw_data.py
```

Скрипт:

- читает пути и имена архивов из `configs/paths.yaml` и `configs/data.yaml`;
- проверяет наличие архивов;
- безопасно распаковывает `.tar.gz`;
- создаёт ожидаемые папки в `data/raw/`;
- создаёт `.prepared.json` с информацией о подготовке.

---

## 5. Как распаковать заново

Если данные уже были распакованы, повторный запуск пропустит существующие папки.

Чтобы удалить старую распаковку и распаковать архивы заново:

```powershell
uv run python scripts/prepare_raw_data.py --force
```

Использовать `--force` стоит, если:

- архивы были заменены;
- структура данных изменилась;
- предыдущая распаковка была прервана;
- нужно гарантированно пересоздать `data/raw/user_actions/` и `data/raw/product_information/`.

---

## 6. Проверка после распаковки

После успешной подготовки должны существовать папки:

```text
data/raw/user_actions/
data/raw/product_information/
```

Также можно выполнить быструю проверку загрузчиков:

```powershell
uv run python -c "from ozon_similar_products.data import load_configs, load_events, load_products; cfg = load_configs(); print(load_products(cfg).shape); print(load_events(cfg, use_sample=True, sample_days=1, sample_rows=100000).shape)"
```

Ожидаемый результат примерно такой:

```text
(130035, 6)
(100000, 7)
```

Первый вывод — размер справочника товаров.

Второй вывод — sample событий пользователей.

---

## 7. Как импортировать загрузчики

Загрузчики лежат в:

```text
src/ozon_similar_products/data/loaders.py
```

Использовать их нужно через пакет:

```python
from ozon_similar_products.data import (
    load_configs,
    load_events,
    load_products,
    scan_events,
    scan_products,
)
```

---

## 8. Загрузка конфигов

Перед чтением данных нужно загрузить конфиги:

```python
from ozon_similar_products.data import load_configs

config = load_configs()
```

`load_configs()` читает:

```text
configs/paths.yaml
configs/data.yaml
```

Поэтому пути, имена архивов, glob-паттерны и ожидаемые колонки должны храниться в конфигах, а не хардкодиться в ноутбуках.

---

## 9. Загрузка справочника товаров

```python
from ozon_similar_products.data import load_configs, load_products

config = load_configs()

products = load_products(config)

print(products.shape)
print(products.head())
```

На текущей версии данных ожидаемый размер:

```text
(130035, 6)
```

Если нужно выбрать только часть колонок:

```python
products = load_products(
    config,
    columns=["item_id", "name"],
)
```

Названия колонок нужно сверить с фактической схемой справочника товаров.

---

## 10. Загрузка событий пользователей

Для EDA не стоит сразу читать полный датасет.

Даже один день может содержать больше 10 млн строк.

Рекомендуемый безопасный вариант:

```python
from ozon_similar_products.data import load_configs, load_events

config = load_configs()

events = load_events(
    config,
    use_sample=True,
    sample_days=1,
    sample_rows=100_000,
)

print(events.shape)
print(events.head())
```

Ожидаемый результат:

```text
(100000, 7)
```

---

## 11. Фактические колонки событий

На текущей версии данных `load_events()` возвращает колонки:

```text
user_id
date
timestamp
action_type
widget_name
search_query
item_id
```

Типы данных:

```text
user_id       Int32
date          Date
timestamp     Datetime[ns]
action_type   String
widget_name   String
search_query  String
item_id       Int32
```

---

## 12. Чтение событий по `action_type`

Один тип действия:

```python
events_click = load_events(
    config,
    use_sample=True,
    sample_days=1,
    action_types="click",
    sample_rows=100_000,
)
```

Несколько типов действий:

```python
events_subset = load_events(
    config,
    use_sample=True,
    sample_days=1,
    action_types=["view", "click", "to_cart"],
    sample_rows=100_000,
)
```

---

## 13. Чтение событий по датам

Конкретные даты:

```python
events = load_events(
    config,
    dates=["2024-03-01", "2024-03-02"],
    sample_rows=100_000,
)
```

Диапазон дат:

```python
events = load_events(
    config,
    start_date="2024-03-01",
    end_date="2024-03-07",
    sample_rows=100_000,
)
```

---

## 14. Lazy-загрузка для больших вычислений

`load_events()` сразу загружает данные в память.

Для больших операций лучше использовать `scan_events()`.

Он возвращает `polars.LazyFrame` и позволяет Polars оптимизировать чтение.

Пример:

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

Для справочника товаров есть аналогичная функция:

```python
from ozon_similar_products.data import load_configs, scan_products

config = load_configs()

products_lazy = scan_products(config)
```

---

## 15. Рекомендуемый шаблон для ноутбуков

В ноутбуках не нужно вручную искать parquet-файлы.

Используйте такой шаблон:

```python
from ozon_similar_products.data import load_configs, load_events, load_products

config = load_configs()

events = load_events(
    config,
    use_sample=True,
    sample_days=1,
    sample_rows=100_000,
)

products = load_products(config)
```

---

## 16. Что делать, если загрузка не работает

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

Сначала посмотрите структуру архива:

```powershell
uv run python scripts/prepare_raw_data.py --preview
```

Потом попробуйте распаковать заново:

```powershell
uv run python scripts/prepare_raw_data.py --force
```

### Загрузка слишком долгая

Не читайте весь датасет сразу.

Используйте:

```python
load_events(
    config,
    use_sample=True,
    sample_days=1,
    sample_rows=100_000,
)
```

или lazy-вариант:

```python
scan_events(config)
```

---

## 17. Что не нужно делать

Не нужно:

- распаковывать архивы вручную через Проводник;
- коммитить архивы в Git;
- коммитить распакованные parquet-файлы;
- писать `pl.read_parquet(...)` вручную в каждом ноутбуке;
- хардкодить пути к данным внутри EDA-ноутбуков.

Правильный путь:

```text
архивы -> scripts/prepare_raw_data.py -> loaders.py -> notebooks / baseline / pipeline
```
