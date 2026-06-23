# Полный итог тюнингов и экспериментов

Документ фиксирует все основные эксперименты по проекту `OZON-Similar-products`: от baseline и настройки scorer до fallback-тюнинга, итогового конфига и отложенных гипотез.

## 1. Задача и общий подход

Цель проекта — построить item-to-item recommender для формата:

```text
sku -> similar_items_sku_list
```

Это не персонализация по `user_id`, а поиск похожих товаров по поведению пользователей.

Основной pipeline:

```text
сырые события
  -> очищенные события
  -> сессии
  -> item-item co-visitation пары
  -> дневная статистика пар / rolling aggregates
  -> scoring
  -> top-K рекомендации
  -> fallback для sparse / cold-start товаров
```

Основная идея: если товары часто встречаются рядом в пользовательских сессиях, между ними есть behavioral-связь. Метаданные используются не как основная модель, а как fallback-слой для редких товаров.

## 2. Основные метрики

| Метрика | Что означает |
|---|---|
| `to_cart_hit_rate_at_k` | доля source-товаров, для которых в top-K найден хотя бы один будущий `to_cart` candidate |
| `to_cart_recall_at_k` | доля найденных будущих `to_cart`-связей |
| `strong_ndcg_at_k` | качество ранжирования по сильным действиям: `click`, `favorite`, `to_cart` |
| `coverage_at_k` | доля товаров, для которых удалось построить рекомендации |
| `fallback_share_at_k` | доля рекомендаций, добавленных fallback-слоем |
| `fallback_global_share_at_k` | доля рекомендаций из global popular fallback |
| `popularity_bias_at_k` | насколько рекомендации смещены в сторону популярных товаров |

Для выбора модели важны не только hit/recall, но и баланс: высокий `fallback_share_at_k` или `fallback_global_share_at_k` может означать, что модель начинает подменять behavioral-рекомендации популярными товарами.

## 3. Что тюнили

Всего по доступным `results.csv` было проанализировано:

| Группа | Запуски | Trials |
|---|---:|---:|
| Первые sweeps по scorer / thresholds / popularity / fallback | 8 | 818 |
| Финальный fallback sweep | 1 | 120 |
| **Итого** | **9** | **938** |

## 4. Baseline

Первый baseline — простой co-visitation count:

```text
score(A, B) = pair_count(A, B)
```

Плюсы:

- простой;
- быстрый;
- хорошо объясняется;
- даёт понятную начальную точку.

Главный минус: baseline не различает силу действий. `view`, `click`, `favorite`, `to_cart` влияют на связь одинаково, хотя бизнес-смысл у них разный.

Вывод: `pair_count` подходит как baseline, но финальная модель должна быть action-aware.

## 5. Тюнинг scorer

### 5.1 Гипотеза: разные действия имеют разную ценность

Проверяли `calibrated_multichannel` scorer:

```text
score =
  w_view * view_count
+ w_click * click_count
+ w_favorite * favorite_count
+ w_to_cart * to_cart_count
```

Лучшие найденные веса:

| Действие | Вес | Интерпретация |
|---|---:|---|
| `view` | 0.5 | слабый, но полезный массовый сигнал |
| `click` | 1.5 | более явный интерес |
| `favorite` | 3.0 | сильный сигнал заинтересованности |
| `to_cart` | 4.0 | самый бизнес-важный сигнал |

Вывод:

- `view` не стоит занулять: он шумный, но даёт coverage и массовую статистику;
- `click/favorite/to_cart` должны быть сильнее `view`;
- лучший scorer остаётся простым и хорошо объяснимым.

### 5.2 Count transform: `linear` vs `sqrt` vs `log`

Проверялись разные преобразования pair counts.

| Transform | Trials | Лучший `to_cart_hit_rate_at_k` | Медианный hit | Лучший `strong_ndcg_at_k` |
|---|---:|---:|---:|---:|
| `linear` | 173 | 0.7908 | 0.7717 | 0.4400 |
| `sqrt` | 112 | 0.7809 | 0.7481 | 0.4088 |
| `log` | 195 | 0.7723 | 0.7483 | 0.4175 |

Вывод: на текущих данных лучше всего сработал `linear`. Частые co-visitation связи оказались полезным сигналом, а `log/sqrt` слишком сильно сжимали сильные пары.

### 5.3 Frequency boost / `beta`

Проверяли усиление редких действий через `beta`.

| `beta` | Лучший hit | Лучший NDCG | Trials |
|---:|---:|---:|---:|
| 0.00 | 0.7908 | 0.4400 | 65 |
| 0.10 | 0.7900 | 0.4399 | 56 |
| 0.25 | 0.7774 | 0.4153 | 79 |
| 0.40 | 0.7827 | 0.4046 | 39 |
| 0.50 | 0.7788 | 0.4016 | 56 |
| 0.65 | 0.7795 | 0.4147 | 72 |
| 0.75 | 0.7771 | 0.4069 | 63 |
| 1.00 | 0.7786 | 0.4079 | 50 |

Вывод: `beta = 0.0` оказался лучшим. Дополнительный frequency boost не дал прироста, поэтому финальный scorer проще: важность действий задаётся business weights.

### 5.4 Popularity normalization

Гипотеза: можно штрафовать слишком популярные товары, чтобы рекомендации меньше смещались в популярные SKU.

Результат на popularity-normalization sweep:

| Popularity norm | Power | To-cart hit | Strong NDCG | Popularity bias | Fallback share |
|---|---:|---:|---:|---:|---:|
| Off | 0.00 | 0.8062 | 0.4858 | 0.0931 | 0.3754 |
| On | 0.25 | 0.8046 | 0.4803 | 0.0838–0.0856 | 0.3756–0.3772 |
| On | 0.50 | 0.7706 | 0.4180–0.4205 | 0.0889 | 0.6281 |
| On | 0.75 | 0.7099 | 0.3084 | 0.0972 | 0.9850 |
| On | 1.00 | 0.7049 | 0.3062 | 0.0976 | 0.9980 |

Вывод: popularity normalization не дала устойчивого улучшения. Мягкая нормализация немного снижала popularity bias, но не улучшала главные ranking metrics. Сильная нормализация ломала качество и резко увеличивала зависимость от fallback.

Финальное решение:

```yaml
scoring:
  normalize_by_item_popularity: false
```

## 6. Тюнинг thresholds

Проверялись ограничения:

```yaml
scoring:
  min_pair_count
  min_unique_users
  min_unique_sessions
  min_score
```

Для чистого behavioral scorer лучше всего выглядел мягкий вариант:

```yaml
scoring:
  min_pair_count: 1
  min_unique_users: 1
  min_unique_sessions: 1
```

Результат лучшего no-fallback behavioral scorer:

| Метрика | Значение |
|---|---:|
| `to_cart_hit_rate_at_k` | 0.7860 |
| `to_cart_recall_at_k` | 0.1813 |
| `strong_ndcg_at_k` | 0.4330 |
| `coverage_at_k` | 0.9246 |
| `fallback_share_at_k` | 0.0000 |

Вывод: слишком жёсткие thresholds могут убирать шум, но вместе с этим снижают coverage и увеличивают потребность в fallback. Для behavioral scorer лучше не ужесточать thresholds слишком сильно.

## 7. Сводка основных sweeps

| Эксперимент | Стратегия | Lookback / validation | Trials | Лучший trial | To-cart hit | To-cart recall | Strong NDCG | Coverage | Fallback share | Главный вывод |
|---|---|---:|---:|---|---:|---:|---:|---:|---:|---|
| Широкий scorer sweep | random | 1d / 1d | 120 | `trial_0005` | 0.7452 | 0.1598 | 0.4141 | 0.8068 | 0.0000 | ранний scorer tuning, слабее позднего linear setup |
| Широкий scorer sweep | simulated annealing | 1d / 1d | 150 | `trial_0125` | 0.7500 | 0.1626 | 0.4197 | 0.8124 | 0.0000 | linear начал выглядеть перспективнее |
| Фокусный sweep весов / log | random | 1d / 1d | 60 | `trial_0054` | 0.7697 | 0.1694 | 0.4175 | 0.8609 | 0.0000 | log улучшил ранние sweeps, но не стал финальным лучшим вариантом |
| Широкий scorer sweep | simulated annealing | 1d / 1d | 150 | `trial_0149` | 0.7908 | 0.1848 | 0.4394 | 0.9278 | 0.0000 | лучший паттерн чистого behavioral scorer |
| Thresholds + fallback ON | grid | 1d / 1d | 140 | `trial_0054` | 0.8062 | 0.1957 | 0.4858 | 0.9733 | 0.3754 | fallback улучшает метрики, но работает агрессивно |
| Min-score + fallback ON | random | 1d / 1d | 30 | `trial_0030` | 0.8062 | 0.1957 | 0.4858 | 0.9733 | 0.3754 | min_score почти не изменил выбранный optimum |
| Popularity norm + fallback ON | grid | 1d / 1d | 24 | `trial_0012` | 0.8062 | 0.1957 | 0.4858 | 0.9733 | 0.3754 | popularity normalization осталась выключенной |
| Thresholds + fallback OFF | grid | 1d / 1d | 144 | `trial_0003` | 0.7860 | 0.1813 | 0.4330 | 0.9246 | 0.0000 | чистый no-fallback reference |
| Финальный fallback sweep | random | 7d / 1d | 120 | `trial_0090` | 0.8351 | 0.2184 | 0.4809 | 0.9871 | 0.2666 | лучший balanced fallback |

## 8. Fallback tuning

### 8.1 Гипотеза

Behavioral graph не всегда набирает полный и качественный top-K для редких товаров. Fallback может добивать неполные выдачи через metadata-кандидатов.

Fallback cascade:

```text
category + type
  -> category
  -> type
  -> brand
  -> global popular
```

Важно: fallback не должен заменять основную модель. Он должен быть controlled post-processing layer.

### 8.2 Ранний результат fallback

На быстрых 1d/1d sweeps fallback повысил метрики, но был довольно агрессивным:

| Метрика | No fallback | Fallback ON |
|---|---:|---:|
| `to_cart_hit_rate_at_k` | 0.7860 | 0.8062 |
| `to_cart_recall_at_k` | 0.1813 | 0.1957 |
| `strong_ndcg_at_k` | 0.4330 | 0.4858 |
| `coverage_at_k` | 0.9246 | 0.9733 |
| `fallback_share_at_k` | 0.0000 | 0.3754 |

Вывод: fallback повышает offline metrics и coverage, но при большой доле начинает доминировать над behavioral-рекомендациями.

### 8.3 Финальный fallback sweep

Последний запуск:

| Поле | Значение |
|---|---|
| Sweep | `sweep_2026-06-23_12-01-25Z_random_train-2024-04-23_lookback-7d_validation-1d_top-20_run-28024562339-attempt-1` |
| Стратегия | `random` |
| Trials | 120 / 120 |
| Fast scoring only | `true` |
| Train until | `2024-04-23` |
| Lookback days | 7 |
| Validation days | 1 |
| Top-K | 20 |

Лучший balanced trial: `trial_0090`.

| Метрика | Значение |
|---|---:|
| `objective_score` | 0.316480 |
| `to_cart_hit_rate_at_k` | 0.8351 |
| `to_cart_recall_at_k` | 0.2184 |
| `strong_ndcg_at_k` | 0.4809 |
| `coverage_at_k` | 0.9871 |
| `fallback_share_at_k` | 0.2666 |
| `fallback_global_share_at_k` | 0.0000 |
| `popularity_bias_at_k` | 0.0701 |

Состав fallback:

| Источник fallback | Доля |
|---|---:|
| `category_type` | 0.2406 |
| `category` | 0.0223 |
| `type` | 0.0000 |
| `brand` | 0.0037 |
| `global` | 0.0000 |

### 8.4 Max hit-rate vs balanced fallback

Максимальный hit-rate получили более агрессивные trials, например `trial_0104` / `trial_0069`.

| Метрика | Best balanced `trial_0090` | Max hit-rate trial |
|---|---:|---:|
| `objective_score` | 0.3165 | 0.2900 |
| `to_cart_hit_rate_at_k` | 0.8351 | 0.8370 |
| `to_cart_recall_at_k` | 0.2184 | 0.2194 |
| `strong_ndcg_at_k` | 0.4809 | 0.4825 |
| `coverage_at_k` | 0.9871 | 0.9872 |
| `fallback_share_at_k` | 0.2666 | 0.3169 |
| `fallback_global_share_at_k` | 0.0000 | 0.1550 |
| `popularity_bias_at_k` | 0.0701 | 0.1601 |

Вывод: max hit-rate даёт небольшой прирост hit, но за счёт global fallback и более высокого popularity bias. Для финальной версии предпочтительнее balanced trial.

### 8.5 Выводы по параметрам fallback

#### Cold-start sources

| `include_cold_start_items` | Trials | Средний objective | Средний hit | Средний NDCG | Средний coverage | Средний fallback share |
|---|---:|---:|---:|---:|---:|---:|
| `false` | 58 | 0.2832 | 0.8294 | 0.4786 | 0.9596 | 0.0958 |
| `true` | 62 | 0.3021 | 0.8355 | 0.4813 | 0.9872 | 0.3026 |

Вывод: `include_cold_start_items = true` — самый сильный fallback-сигнал. Он заметно повышает coverage и немного улучшает ranking metrics.

#### Global fallback

| `enable_global` | Trials | Средний objective | Средний hit | Средний coverage | Средний fallback share | Средний global fallback share |
|---|---:|---:|---:|---:|---:|---:|
| `false` | 58 | 0.2974 | 0.8323 | 0.9725 | 0.1667 | 0.0000 |
| `true` | 62 | 0.2889 | 0.8328 | 0.9752 | 0.2363 | 0.0878 |

Вывод: global fallback может слегка поднять hit в отдельных trials, но ухудшает баланс, повышает global fallback share и popularity bias. В финальной версии `enable_global` лучше выключить.

#### Type / brand fallback

| Параметр | Вывод |
|---|---|
| `enable_type` | почти не менял среднее качество; можно держать выключенным для простоты |
| `enable_brand` | небольшой плюс в среднем, но не ключевой параметр |
| `candidate_pool_size` | 100–500 работают близко; best balanced использует 500 |
| `metadata_candidate_pool_size` | 100–500 работают близко; best balanced использует 100 |
| `min_item_events` | 1–5 дают похожие результаты; best balanced использует 2 |

## 9. Финальный recommended config

### 9.1 Scoring

```yaml
scoring:
  method: calibrated_multichannel
  count_source: weighted
  count_transform:
    method: linear
    smoothing: 0.1
  business_weights:
    view: 0.5
    click: 1.5
    favorite: 3.0
    to_cart: 4.0
  beta: 0.0
  reference_action_type: view
  min_pair_count: 2
  min_unique_users: 3
  min_unique_sessions: 1
  min_weighted_pair_count: null
  min_score: 0.01
  normalize_by_item_popularity: false
```

Примечание: в best config стоит `count_source: weighted`, но `distance_decay`, `time_decay` и `widget_context` в этом run выключены. Поэтому это не является доказательством преимущества graph-weighted counts; фактически scorer остаётся calibrated multichannel with linear counts.

### 9.2 Fallback

```yaml
business:
  fallback:
    enabled: true
    top_k: 50
    candidate_pool_size: 500
    global_candidate_pool_size: 1000
    metadata_candidate_pool_size: 100
    min_item_events: 2
    enable_category_type: true
    enable_category: true
    enable_type: false
    enable_brand: true
    enable_global: false
    include_cold_start_items: true
    include_catalog_only_sources: false
```

## 10. Отложенные гипотезы

| Гипотеза | Статус | Почему отложили |
|---|---|---|
| `widget_name` context | не проверено полноценно | может учитывать, откуда пришло действие: поиск, карточка товара, рекомендации; не хватило времени |
| `distance_decay` | пока без заметного прироста | текущие сессии ограничены и часто короткие; нужен больший session length / более дорогой прогон |
| `time_decay` | не проверено полноценно | быстрые scorer-only experiments не подходят; нужен lookback 14–30 дней |
| graph tuning | отложено | требует пересборки pair aggregates, поэтому дороже scoring-only tuning |
| embeddings / ALS / Word2Vec | future work | можно сравнить с co-visitation как отдельный retrieval branch |

## 11. Финальные выводы

1. **Baseline работает как простая отправная точка**, но не учитывает разную силу пользовательских действий.
2. **Лучший основной scorer — `calibrated_multichannel`**: он использует все каналы поведения, но по-разному их взвешивает.
3. **Лучшие action weights:** `view=0.5`, `click=1.5`, `favorite=3.0`, `to_cart=4.0`.
4. **`linear` count transform лучше `log/sqrt`** на текущих sweep-ах.
5. **`beta=0.0`**: frequency boost не дал прироста.
6. **Popularity normalization выключена**: она ухудшала ranking metrics и увеличивала зависимость от fallback.
7. **Fallback полезен**, особенно для sparse/cold-start товаров, но должен быть контролируемым.
8. **Global fallback лучше выключить**: он даёт риск popularity-driven recommendations.
9. **Финальный balanced fallback** повышает coverage до ~0.987 при controlled fallback share ~0.267 и нулевом global fallback share.
10. **Модель остаётся интерпретируемой**: behavioral scorer + controlled metadata fallback.

## 12. Формулировка для защиты

> Мы начали с простого pair-count co-visitation baseline, а затем перешли к action-aware scorer, потому что разные действия пользователя имеют разный бизнес-смысл. Лучший scorer использует все behavioral-каналы, но сильнее взвешивает `click`, `favorite` и `to_cart`. Преобразования `log/sqrt`, frequency boost и popularity normalization не улучшили качество на validation, поэтому финальный scorer оставлен простым и интерпретируемым. Fallback используется только как контролируемый post-processing слой для sparse/cold-start товаров. Финальный balanced fallback улучшает coverage и при этом избегает global popular рекомендаций.

## 13. Ограничения

1. Финальный fallback sweep использовал `lookback_days = 7`, `validation_days = 1`.
2. Для финального подтверждения стоит прогнать `validation_days = 3` или несколько validation dates.
3. Последний fallback sweep содержит только trials с включённым fallback, поэтому всё ещё нужен строгий fallback-on/off ablation на том же resource window.
4. Graph-гипотезы требуют более дорогих экспериментов, потому что pair aggregates нужно пересобирать.
5. Offline-метрики — это proxy; для production-валидации нужен online A/B test.

## 14. Рекомендуемые следующие эксперименты

1. Строгий финальный ablation на одном и том же окне:
   - best scorer без fallback;
   - best scorer + balanced fallback;
   - best scorer + aggressive fallback.
2. Более длинная validation:
   - `lookback_days = 14` или `30`;
   - `validation_days = 3`.
3. Graph features:
   - `time_decay`;
   - `distance_decay`;
   - `widget_context`.
4. Hybrid ranking:
   - behavioral score;
   - metadata similarity;
   - popularity / freshness features.
5. Online-метрики:
   - CTR;
   - add-to-cart rate;
   - conversion proxy;
   - catalog coverage;
   - recommendation diversity.
