# Graph scoring improvements

Этот этап добавляет отдельный weighted-граф поверх прежних raw co-visitation
фактов. Raw counts остаются фактическими счетчиками встречаемости пары, а
weighted counts отражают силу связи после distance decay и time decay.

## Distance decay

`graph.distance_decay` ослабляет пары товаров, которые находятся далеко друг от
друга внутри одной сессии. `ItemPairBuilder` сортирует события внутри
`user_id, session_index` по `timestamp`, `item_id`, `action_type`, берет для
каждого товара earliest position, а signal type оставляет самым сильным
item-level действием.

Поддерживаются стратегии:

- `none`: `distance_weight = 1.0`.
- `weight_table`: bucket/floor lookup. Для distance берется ближайший меньший
  настроенный bucket, а distance больше максимального bucket получает `default`.
- `exponential`: `exp(-alpha * (position_distance - 1))`, ограниченный снизу
  `min_weight`.

`graph.distance_decay.max_distance` может отфильтровать пары дальше указанного
расстояния до записи daily pair stats.

## Time decay

`graph.time_decay` применяется в `PairAggregator`, где известны `pair_date` и
границы rolling window. `age_days = 0` для самого свежего дня окна.

Поддерживаются стратегии:

- `none`: `time_weight = 1.0`.
- `weight_table`: bucket/floor lookup по `age_days`; дни старше максимального
  bucket получают `default`.
- `exponential`: `0.5 ** (age_days / half_life_days)`, ограниченный снизу
  `min_weight`.

Time decay применяется только к weighted columns. Raw counts всегда остаются
обычными суммами.

## Raw and weighted counts

Daily stats и rolling aggregates хранят оба набора колонок:

- raw: `pair_count`, `view_count`, `click_count`, `favorite_count`,
  `to_cart_count`;
- weighted: `weighted_pair_count`, `weighted_view_count`,
  `weighted_click_count`, `weighted_favorite_count`,
  `weighted_to_cart_count`.

Так можно сравнивать старый raw graph и decayed weighted graph без потери
диагностики. Старые daily-count artifacts без weighted columns читаются
агрегатором как `weighted = raw`.

## Weighted scoring

По умолчанию scoring сохраняет старое поведение:

```yaml
graph:
  distance_decay:
    enabled: false
    strategy: none
  time_decay:
    enabled: false
    strategy: none

scoring:
  count_source: raw
  count_transform:
    method: log
    smoothing: 1.0
```

Чтобы считать score по weighted counts:

```yaml
scoring:
  count_source: weighted
```

Для `calibrated_multichannel` можно выбрать transform:

- `log`: `log(count + smoothing)`;
- `sqrt`: `sqrt(count)`;
- `linear`: `count`.

Raw thresholds `min_pair_count`, `min_unique_users`, `min_unique_sessions`
остаются reliability-фильтрами. Дополнительно доступны
`min_weighted_pair_count` до scoring и `min_score` после scoring.

## Evaluation semantics

Graph tuning parameters влияют на train graph и на итоговые recommendations, но
validation ground truth строится со стабильной pair semantics:

- distance decay disabled;
- time decay disabled;
- `max_distance` disabled.

Это сделано специально, чтобы offline metrics были сравнимыми между graph/scoring
trial-ами. Меняется train graph, но не validation target.

## Tuning

Graph-параметры меняют train artifacts, поэтому
`configs/tuning/search_space_graph_scoring.yaml` нужно запускать только через
обычный full tuning. Не используйте этот search space с `--fast-scoring-only`:
fast scoring-only mode переиспользует уже построенный граф и не перестраивает
daily pair stats. `graph.distance_decay.max_distance` можно тюнить именно потому,
что он теперь влияет только на train recommendations, а не на validation
ground truth.

Baseline full run:

```bash
uv run ozon-run-full 2024-04-23 \
  --lookback-days 1 \
  --validation-days 1 \
  --top-k 50 \
  --config-path configs/production.yaml \
  --run-name baseline-before-graph-improvements
```

Full graph/scoring tuning:

```bash
uv run ozon-run-tune 2024-04-23 \
  --lookback-days 1 \
  --validation-days 1 \
  --top-k 50 \
  --config-path configs/production.yaml \
  --search-space-path configs/tuning/search_space_graph_scoring.yaml \
  --max-trials 20 \
  --tuning-strategy random \
  --sweep-name graph-scoring-random
```
