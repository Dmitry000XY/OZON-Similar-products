# Слой данных

В пакете собраны функции для подготовки и чтения данных, которые используются
в MVP-пайплайне. Публичные функции остаются доступными через
`ozon_similar_products.data`.

## Модули

- `archives.py` подготавливает сырые `.tar.gz` архивы в локальные parquet
  датасеты и пишет маркеры `.prepared.json`.
- `config.py` загружает YAML-конфиги и резолвит пути проекта для задач data-слоя.
- `partitions.py` работает с Hive-партициями по датам и типам действий.
- `readers.py` сканирует и читает датасеты товаров и событий с валидацией.
- `schemas.py` содержит контракты колонок из `configs/data.yaml`.
- `validation.py` валидирует обязательные колонки для DataFrame и LazyFrame.

## Публичный API

```python
from ozon_similar_products.data import (
    load_configs,
    load_events,
    load_products,
    scan_events,
    scan_products,
)
```

Для подготовки архивов используйте CLI-обертку:

```bash
uv run python scripts/prepare_raw_data.py
```
