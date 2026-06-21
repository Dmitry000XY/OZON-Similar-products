# Тюнинг и оценка fallback

Этот документ описывает, как оценивается fallback и как тюнить scoring/fallback параметры без полного пересчёта train
artifacts на каждый trial.

## Где находится fallback

Fallback запускается после `TopKSelector` и перед записью рекомендаций:

```text
pair scores
-> TopKSelector
-> FallbackLayer
-> RecommendationWriter
```

Поведенческие рекомендации остаются первыми. Если у товара меньше `top_k` рекомендаций, fallback заполняет недостающие
ранги через индексы популярности:

1. `fallback_category_type_popular`
2. `fallback_category_popular`
3. `fallback_type_popular`
4. `fallback_brand_popular`, если уровень включен
5. `fallback_global_popular`

Текущая реализация строит fallback-индексы один раз за запуск, а затем использует прямые lookup-операции для каждого
source item. Это убирает полный просмотр каталога кандидатов для каждого item-а и каждого fallback-уровня.

## Два режима тюнинга

### Full tuning

Обычный запуск `ozon-run-tune` без `--fast-scoring-only` пересчитывает весь pipeline для каждого trial:

```text
raw events -> clean events -> sessions -> pairs -> aggregation -> scoring -> top-K -> fallback -> evaluation
```

Этот режим нужен, если search space меняет параметры, влияющие на train artifacts: например `pipeline.lookback_days`,
`pipeline.session_timeout_minutes`, session builder или item-pair builder настройки.

### Fast scoring-only tuning

`--fast-scoring-only` сначала один раз строит train artifacts и validation artifacts, а затем для каждого trial
выполняет только:

```text
prebuilt pair aggregates -> scoring -> top-K -> fallback -> evaluation
```

Этот режим безопасен только для параметров с префиксами:

- `scoring.`
- `topk.`
- `business.fallback.`

Параметры вроде `pipeline.lookback_days` или `pipeline.session_timeout_minutes` в fast scoring-only режиме запрещены,
потому что они требуют пересборки train artifacts.

## Search spaces

### `configs/tuning/search_space_scoring_core.yaml`

Компактный быстрый search space для подбора scoring-параметров. Fallback в нём зафиксирован как выключенный:

```yaml
business.fallback.enabled: [false]
```

Используйте его, когда нужно быстро сравнить веса действий, `beta` и normalization без влияния fallback.

### `configs/tuning/search_space_scoring_fallback.yaml`

Fast-safe search space для совместного подбора scoring и fallback. Он добавляет к scoring-параметрам
fallback-переключатели:

```yaml
business.fallback.enabled: [false, true]
business.fallback.enable_brand: [false, true]
business.fallback.include_cold_start_items: [false, true]
business.fallback.include_catalog_only_sources: [false]
business.fallback.min_item_events: [1, 2, 5]
business.fallback.metadata_candidate_pool_size: [50, 100, 200]
business.fallback.global_candidate_pool_size: [100, 200, 500]
```

`include_catalog_only_sources` намеренно зафиксирован в `false`, потому что этот режим может сильно увеличить runtime и
размер выходных рекомендаций. Если цель — покрыть весь каталог, включайте этот параметр только в отдельном кастомном
эксперименте.

Два параметра размера пула предпочтительнее, чем тюнить только `candidate_pool_size`:

- `metadata_candidate_pool_size` ограничивает каждый metadata-индекс, например bucket по категории и типу;
- `global_candidate_pool_size` ограничивает переиспользуемый глобальный список популярных товаров;
- если один из этих параметров не задан, fallback использует `candidate_pool_size`.

## Fallback-метрики

Офлайн-оценка считает старые глобальные ranking-метрики и дополнительные fallback-диагностики. Fallback-метрики попадают
во все места, где сериализуется `OfflineMetrics`, включая:

- `outputs/runs/<run_id>/evaluation/metrics.json`
- `outputs/tuning/<sweep_id>/results.csv`
- `outputs/tuning/<sweep_id>/best_metrics.json`

Метрики доли fallback-уровней:

```text
fallback_category_type_share_at_k
fallback_category_share_at_k
fallback_type_share_at_k
fallback_brand_share_at_k
fallback_global_share_at_k
```

Каждое значение — это доля рекомендаций с точным `source` среди всех строк с `rank <= top_k`.

Агрегированные метрики качества fallback:

```text
fallback_hit_rate_at_k
fallback_recall_at_k
fallback_to_cart_hit_rate_at_k
fallback_to_cart_recall_at_k
```

Эти метрики используют только fallback-строки, то есть строки, где `source != "behavioral"`. Общие
`fallback_hit_rate_at_k` и `fallback_recall_at_k` считаются по ranking ground truth, поэтому view-only совпадения не
завышают fallback quality.

`to_cart` fallback-метрики ограничивают ground truth строками, где `to_cart_count > 0`.

Если ground truth пустой, метрики качества fallback возвращаются как `null`, а метрики долей fallback-уровней продолжают
считаться, если рекомендации есть.

## Целевая функция fallback-тюнинга

Для `search_space_scoring_fallback.yaml` целевая функция остаётся сбалансированной вокруг бизнес-качества:

- основная метрика: `to_cart_hit_rate_at_k`
- вспомогательные метрики: `strong_ndcg_at_k`, `strong_recall_at_k`, `strong_mrr_at_k`, `coverage_at_k`,
  `to_cart_recall_at_k`, `fallback_hit_rate_at_k`
- штрафные метрики: `popularity_bias_at_k`, `fallback_global_share_at_k`
- ограничение: `min_coverage_at_k: 0.01`

Это значит, что fallback не становится главной целью оптимизации. Он используется как вспомогательный сигнал, а высокая
зависимость от глобально-популярной подстановки штрафуется.

## Типовые команды

Полный запуск с оценкой:

```bash
uv run ozon-run-full 2024-04-23 --lookback-days 1 --validation-days 1 --top-k 20 --config-path configs/production.yaml --run-name pr48-smoke
```

Быстрый tuning только scoring:

```bash
uv run ozon-run-tune 2024-04-23 --lookback-days 1 --validation-days 1 --top-k 20 --config-path configs/production.yaml --search-space-path configs/tuning/search_space_scoring_core.yaml --max-trials 2 --tuning-strategy grid --fast-scoring-only --sweep-name pr48-fast-core-smoke
```

Быстрый tuning scoring + fallback:

```bash
uv run ozon-run-tune 2024-04-23 --lookback-days 1 --validation-days 1 --top-k 20 --config-path configs/production.yaml --search-space-path configs/tuning/search_space_scoring_fallback.yaml --max-trials 4 --tuning-strategy random --fast-scoring-only --sweep-name pr48-fast-fallback-smoke
```

Full tuning для параметров, влияющих на train artifacts:

```bash
uv run ozon-run-tune 2024-04-23 --lookback-days 1 --validation-days 1 --top-k 20 --config-path configs/production.yaml --search-space-path configs/tuning/search_space.yaml --max-trials 10 --tuning-strategy random --sweep-name full-tuning-smoke
```
