# Слой данных

В пакете собраны функции для подготовки и чтения данных, которые используются
в MVP-пайплайне. Публичные функции доступны через `ozon_similar_products.data`.

## Модули

- `archives.py` подготавливает сырые `.tar.gz` архивы в parquet-датасеты и пишет маркеры `.prepared.json`.
- `config.py` (в корне пакета) — единый проектный loader конфигов и путей.
- `data/config.py` — только совместимая обёртка для старых импортов.
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

Если терминал открыт в папке `docs/modules/`, перейдите в корень проекта
относительным путём:

```bash
cd ../..
```

```bash
uv run python scripts/prepare_raw_data.py
```
