# Архитектура

Проект реализует offline pipeline похожих товаров для Ozon Fresh.

## Текущий MVP-пайплайн

```text
raw user actions
→ EventCleaner
→ SessionBuilder
→ ItemPopularityBuilder
→ ItemPairBuilder
→ PairAggregator
→ CoVisitationScorer
→ TopKSelector
→ FallbackLayer (optional)
→ RecommendationWriter
→ SimilarItemsLookup
```

`FallbackLayer` является отдельным post-top-k слоем и не должен
встраиваться в preprocessing/retrieval этапы.

## Структура пакета после рефакторинга

```text
data/          # loading, schemas, validation
preprocessing/ # clean events, sessions
features/      # production feature artifacts
retrieval/     # pairs, aggregates, scoring, top-k
diagnostics/   # reusable EDA/profiling/session checks
business/      # fallback/business rules (PR3 fallback layer)
evaluation/    # offline metrics/scorecards (PR2 skeleton)
output/        # writers, manifest, compatibility lookup import
serving/       # runtime lookup APIs
cli/           # package CLI entrypoints
pipeline/      # orchestration
docs/archive/  # archived EDA code and history
```

## Единый слой конфигурации и совместимые обёртки

- Единый источник правды для загрузки конфигов: `src/ozon_similar_products/config.py`.
- Публичный API для production-кода: `PROJECT_ROOT`, `resolve_config_path`,
  `load_yaml_config`, `load_paths_config`, `load_data_config`, `load_configs`,
  `resolve_project_path`, `get_path_from_config`.
- `src/ozon_similar_products/data/config.py` сохранён только как совместимый
  shim для старых импортов в ноутбуках/тестах; логики загрузки YAML там больше нет.
- `src/ozon_similar_products/output/lookup.py` также является совместимым re-export:
  канонический runtime-lookup живёт в `src/ozon_similar_products/serving/lookup.py`.

## Ограничения fallback-реализации

`FallbackLayer` в текущем виде — MVP/local-слой (по умолчанию выключен). Он
использует Python-level сборку строк и не рассчитан на production-scale каталоги
без отдельного Polars-native переписывания merge-этапа.

## Архивный EDA-код

- Legacy EDA helpers хранятся в `docs/archive/code/`.
- Архивный код не участвует в runtime pipeline.
- Архивный код нельзя импортировать из `src/`, `tests/`, `scripts/`.

## Критерии приёмки PR1 (кратко)

1. В `src/ozon_similar_products` нет `eda_*` модулей вне `diagnostics/`.
2. Reusable EDA helpers перенесены в `diagnostics/`.
3. Legacy EDA code перемещён в `docs/archive/code/` и не собирается pytest.
4. `src/ozon_similar_products/data/loaders.py` удалён после миграции импортов.
5. `README.md`, `docs/architecture.md`, `scripts/README.md` обновлены под актуальную структуру.
6. Проходят `uv run ruff check src scripts tests` и `uv run pytest`.

## Чеклист перед PR1

1. Зафиксировать текущие EDA-импорты в `tests/` и `notebooks/`.
2. Перенести/удалить только подтверждённые точки импорта.
3. После переноса проверить, что активные тесты используют только новые пути.

## Дальнейший рефакторинг (отдельные PR)

- PR4: offline evaluation metrics;
- PR5: decide Python version policy (`>=3.14` vs wider compatibility);
- optional глубокий split retrieval на graph/scoring/ranking.
