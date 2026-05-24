# Architecture

Проект реализует offline pipeline похожих товаров для Ozon Fresh.

## Current MVP pipeline

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

## Package structure after cleanup

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

## Archived EDA code

- Legacy EDA helpers хранятся в `docs/archive/code/`.
- Архивный код не участвует в runtime pipeline.
- Архивный код нельзя импортировать из `src/`, `tests/`, `scripts/`.

## PR1 acceptance criteria (compact)

1. В `src/ozon_similar_products` нет `eda_*` модулей вне `diagnostics/`.
2. Reusable EDA helpers перенесены в `diagnostics/`.
3. Legacy EDA code перемещён в `docs/archive/code/` и не собирается pytest.
4. `src/ozon_similar_products/data/loaders.py` удалён после миграции импортов.
5. `README.md`, `docs/architecture.md`, `scripts/README.md` обновлены под актуальную структуру.
6. Проходят `uv run ruff check src scripts tests` и `uv run pytest`.

## PR1 preflight checklist

1. Зафиксировать текущие EDA-импорты в `tests/` и `notebooks/`.
2. Перенести/удалить только подтверждённые точки импорта.
3. После переноса проверить, что активные тесты используют только новые пути.

## Future refactor (separate PRs)

- PR4: offline evaluation metrics;
- PR5: decide Python version policy (`>=3.14` vs wider compatibility);
- optional глубокий split retrieval на graph/scoring/ranking.
