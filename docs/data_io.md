# Работа с архивами и загрузчиками данных

Короткая инструкция: как положить архивы, подготовить данные и читать их через `loaders.py`.

## Быстрый старт

Выполните эти шаги из корня проекта.

```powershell
cd D:\ITMO\OZON-Similar-products
```

Положите архивы в папку:

```text
data/raw/archives/
```

Должны получиться такие файлы:

```text
data/raw/archives/user_actions.tar.gz
data/raw/archives/product_information.tar.gz
```

Подготовьте данные:

```powershell
uv run python scripts/prepare_raw_data.py
```

Проверьте, что загрузчики работают:

```powershell
uv run python -c "from ozon_similar_products.data import load_configs, load_events, load_products; cfg = load_configs(); print(load_products(cfg).shape); print(load_events(cfg, use_sample=True, sample_days=1, sample_rows=100000).shape)"
```

Ожидаемый результат:

```text
(130035, 6)
(100000, 7)
```

Если эти две строки вывелись, данные подготовлены корректно.

> В PyCharm команды из блоков `powershell` можно запускать прямо из Markdown-файла кнопкой запуска рядом с блоком, если такая кнопка отображается. Если кнопки нет, скопируйте команду в терминал PyCharm.

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
- создаёт служебный файл `.prepared.json`.

Архивы не нужно распаковывать вручную через Проводник.

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

### Посмотреть содержимое архивов

```powershell
cd D:\ITMO\OZON-Similar-products
uv run python scripts/prepare_raw_data.py --preview
```

Эта команда ничего не распаковывает. Она только показывает первые пути внутри архивов.

### Распаковать архивы

```powershell
cd D:\ITMO\OZON-Similar-products
uv run python scripts/prepare_raw_data.py
```

### Распаковать заново

```powershell
cd D:\ITMO\OZON-Similar-products
uv run python scripts/prepare_raw_data.py --force
```

Используйте `--force`, если архивы были заменены или предыдущая распаковка была прервана.

---

## Как читать данные в Python

Загрузчики лежат в файле:

```text
src/ozon_similar_products/data/loaders.py
```

В коде импортируйте их так:

```python
from ozon_similar_products.data import load_configs, load_events, load_products
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

## Загрузка справочника товаров

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

## Загрузка событий пользователей

Для EDA не загружайте весь датасет сразу. Даже один день может содержать больше 10 млн строк.

Безопасный вариант для первого запуска:

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

Ожидаемый размер sample:

```text
(100000, 7)
```

Фактические колонки событий:

```text
user_id
date
timestamp
action_type
widget_name
search_query
item_id
```

---

## Полезные примеры

### Загрузить только один тип действия

```python
events_click = load_events(
    config,
    use_sample=True,
    sample_days=1,
    action_types="click",
    sample_rows=100_000,
)
```

### Загрузить несколько типов действий

```python
events_subset = load_events(
    config,
    use_sample=True,
    sample_days=1,
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

---

## Lazy-загрузка для больших вычислений

`load_events()` сразу загружает данные в память.

Для больших операций используйте `scan_events()`. Он возвращает `polars.LazyFrame`.

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

## Рекомендуемый шаблон для ноутбуков

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

```powershell
cd D:\ITMO\OZON-Similar-products
uv run python scripts/prepare_raw_data.py --preview
```

Потом распакуйте заново:

```powershell
cd D:\ITMO\OZON-Similar-products
uv run python scripts/prepare_raw_data.py --force
```

### Загрузка слишком долгая

Ограничьте sample:

```python
events = load_events(
    config,
    use_sample=True,
    sample_days=1,
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

- распаковывать архивы вручную через Проводник;
- коммитить архивы в Git;
- коммитить распакованные parquet-файлы;
- хардкодить абсолютные пути к данным в ноутбуках;
- вручную писать чтение parquet-файлов в каждом ноутбуке.

Правильный путь:

```text
архивы -> scripts/prepare_raw_data.py -> loaders.py -> notebooks / baseline / pipeline
```