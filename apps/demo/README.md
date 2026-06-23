# Demo UI похожих товаров

Streamlit-приложение для просмотра рекомендаций, которые строит offline-pipeline проекта.

Приложение читает готовые parquet-артефакты рекомендаций, позволяет найти товар по `item_id` или названию и показывает
список похожих товаров с рангом, score, источником рекомендации и кратким объяснением.

## Установка

```bash
uv sync
```

## Запуск latest-артефакта

По умолчанию приложение читает `outputs/latest/manifest.json`.

```bash
uv run streamlit run apps/demo/app.py
```

То же самое с явным manifest:

```powershell
uv run streamlit run apps/demo/app.py -- ^
--manifest-path outputs/latest/manifest.json
```

## Запуск конкретного parquet-файла

Открыть enriched-рекомендации:

```powershell
uv run streamlit run apps/demo/app.py -- ^
--enriched-path outputs/runs/<run_id>/recommendations/enriched.parquet
```

Открыть detailed-рекомендации:

```powershell
uv run streamlit run apps/demo/app.py -- ^
--detailed-path outputs/runs/<run_id>/recommendations/detailed.parquet
```

Ограничить количество показываемых рекомендаций:

```powershell
uv run streamlit run apps/demo/app.py -- ^
--manifest-path outputs/latest/manifest.json ^
--top-k 20
```

`--enriched-path` имеет приоритет над `--manifest-path`. Если передан только manifest, приложение сначала ищет
`enriched.parquet`, затем использует `detailed.parquet`.

## Ожидаемые артефакты

Основной вариант:

```text
outputs/runs/<run_id>/recommendations/enriched.parquet
```

Ожидаемые колонки:

```text
item_id
item_name
similar_item_id
similar_item_name
rank
score
source
```

Если `enriched.parquet` отсутствует, можно использовать:

```text
outputs/runs/<run_id>/recommendations/detailed.parquet
```

В этом случае названия товаров будут показаны как `None`.

## Вкладки

- `Похожие товары` / `Similar items`: поиск, случайный товар, карточка выбранного товара и таблица похожих товаров.
- `Сводка запуска` / `Run summary`: параметры запуска, пути к артефактам, строки по этапам pipeline и метрики, если они
  есть.
- `О демо` / `About`: краткое описание логики рекомендаций и источников.

## Язык интерфейса

В верхней части страницы есть переключатель `EN / RU`. Он меняет основные подписи, объяснения источников и названия
колонок.

## Возможные проблемы

### `ModuleNotFoundError: No module named 'apps'`

Запускайте приложение из корня репозитория:

```bash
uv run streamlit run apps/demo/app.py
```

Также проверьте, что в ветке есть файлы:

```text
apps/__init__.py
apps/demo/__init__.py
```

### Не найден `outputs/latest/manifest.json`

Сначала постройте рекомендации:

```bash
uv run ozon-run-full 2024-04-23 --lookback-days 1 --validation-days 1 --top-k 20 --config-path configs/production.yaml
```

Или передайте конкретный parquet-файл через `--enriched-path`.
