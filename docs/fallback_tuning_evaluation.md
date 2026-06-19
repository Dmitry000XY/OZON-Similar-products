# Тюнинг и оценка fallback

Этот документ описывает, как оценивается и тюнится оптимизированный
`FallbackLayer`. Он дополняет `configs/tuning/search_space.yaml` и CLI для
полного запуска и тюнинга.

## Где находится fallback

Fallback запускается после `TopKSelector` и перед записью рекомендаций:

```text
pair scores
-> TopKSelector
-> FallbackLayer
-> RecommendationWriter
```

Поведенческие рекомендации остаются первыми. Если у товара меньше `top_k`
рекомендаций, fallback заполняет недостающие ранги через индексы популярности:

1. `fallback_category_type_popular`
2. `fallback_category_popular`
3. `fallback_type_popular`
4. `fallback_brand_popular`, если уровень включен
5. `fallback_global_popular`

Оптимизированная реализация строит fallback-индексы один раз за запуск, а затем
использует прямые lookup-операции для каждого source item. Это убирает полный
просмотр каталога кандидатов для каждого item-а и каждого fallback-уровня.

## Параметры тюнинга fallback

`configs/tuning/search_space.yaml` содержит компактный набор fallback-параметров
для `ozon-run-tune` / `scripts/run_tune.py`:

```yaml
business.fallback.enabled: [false, true]
business.fallback.enable_category_type: [true]
business.fallback.enable_category: [true]
business.fallback.enable_type: [true]
business.fallback.enable_brand: [false, true]
business.fallback.enable_global: [true]
business.fallback.include_cold_start_items: [false, true]
business.fallback.include_catalog_only_sources: [false]
business.fallback.min_item_events: [1, 2, 5]
business.fallback.metadata_candidate_pool_size: [50, 100, 200]
business.fallback.global_candidate_pool_size: [100, 200, 500]
```

`include_catalog_only_sources` в тюнинге намеренно зафиксирован в `false` по
умолчанию, потому что этот режим может сильно увеличить runtime и размер
выходных рекомендаций. Если цель - покрыть весь каталог, параметр можно включить
в отдельном кастомном search space.

Два параметра размера пула предпочтительнее, чем тюнить только
`candidate_pool_size`:

- `metadata_candidate_pool_size` ограничивает каждый metadata-индекс, например
  bucket по категории и типу.
- `global_candidate_pool_size` ограничивает переиспользуемый глобальный список
  популярных товаров.
- Если один из этих параметров не задан, fallback использует
  `candidate_pool_size`.

## Fallback-метрики

Офлайн-оценка считает старые глобальные ranking-метрики и дополнительные
fallback-диагностики. Fallback-метрики попадают во все места, где сериализуется
`OfflineMetrics`, включая:

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

Каждое значение - это доля рекомендаций с точным `source` среди всех строк с
`rank <= top_k`.

Агрегированные метрики качества fallback:

```text
fallback_hit_rate_at_k
fallback_recall_at_k
fallback_to_cart_hit_rate_at_k
fallback_to_cart_recall_at_k
```

Эти метрики используют только fallback-строки, то есть строки, где
`source != "behavioral"`. Знаменатель для `fallback_recall_at_k` остается полным
ground truth набором конкретного исходного товара, поэтому метрика отвечает на
вопрос: какую часть validation truth удалось восстановить именно
fallback-кандидатами.

`to_cart` fallback-метрики ограничивают ground truth строками, где
`to_cart_count > 0`.

Если ground truth пустой, метрики качества fallback возвращаются как `null`, а
метрики долей fallback-уровней продолжают считаться, если рекомендации есть.

## Целевая функция тюнинга

Дефолтная целевая функция тюнинга остается сбалансированной вокруг бизнес-качества:

- основная метрика: `to_cart_hit_rate_at_k`
- вспомогательные метрики: `ndcg_at_k`, `recall_at_k`, `mrr_at_k`,
  `coverage_at_k`, `to_cart_recall_at_k`, `fallback_hit_rate_at_k`
- штрафные метрики: `popularity_bias_at_k`, `fallback_global_share_at_k`
- ограничение: `min_coverage_at_k: 0.0`

Это значит, что fallback не становится главной целью оптимизации. Он используется
как вспомогательный сигнал, а высокая зависимость от глобально-популярной подстановки
штрафуется.

## Типовые команды

Полный запуск с оценкой:

```bash
uv run python scripts/run_full.py 2024-04-23 --lookback-days 1 --validation-days 1 --top-k 20 --config-path configs/production.yaml
```

Случайный запуск тюнинга:

```bash
uv run python scripts/run_tune.py 2024-04-23 --lookback-days 1 --validation-days 1 --top-k 20 --config-path configs/production.yaml --search-space-path configs/tuning/search_space.yaml --max-trials 30 --tuning-strategy random
```

Сравнение результатов тюнинга:

```bash
uv run python scripts/compare_tuning.py --sort-by objective_score
```
