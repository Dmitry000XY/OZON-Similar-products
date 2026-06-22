# OZON Similar Products

Offline-пайплайн для кейса **Ozon Fresh**: построение виджета **«Похожие товары по интересам пользователей»**.

Проект строит item-to-item рекомендации на основе поведения пользователей: для каждого товара (`item_id`) формируется
список похожих товаров, который затем можно использовать в виджете или проверять через lookup.

В постановке кейса используется название `sku`; в локальных данных проекта ему соответствует колонка `item_id`.

---

## Что делает проект

Pipeline строит baseline-рекомендации по цепочке:

```text
daily raw user actions
→ daily clean events parquet
→ streaming sessions with cross-day carry-over
→ compact daily item-pair stats
→ pair aggregates over the rolling window
→ lazy pair scoring
→ top-K recommendations
→ saved artifacts
→ lookup
```

Крупные промежуточные слои записываются как parquet-артефакты и переиспользуются через path/lazy scan там, где это
возможно. Это снижает потребление RAM на длинных rolling window и позволяет не держать в памяти full-window raw events,
full-window item pairs и full-window sessions.

На выходе создаются:

```text
outputs/runs/.../recommendations/detailed.parquet
outputs/runs/.../recommendations/lookup.parquet
outputs/runs/.../manifest.json
outputs/latest/manifest.json
```

`latest/manifest.json` указывает на актуальную версию рекомендаций.

Промежуточные pipeline-артефакты по умолчанию пишутся в `data/processed/`:

```text
data/processed/events_clean/date=YYYY-MM-DD.parquet
data/processed/sessions/date=YYYY-MM-DD.parquet
data/processed/item_pairs/counts/date=YYYY-MM-DD.parquet
data/processed/item_pairs/user_keys/date=YYYY-MM-DD.parquet
data/processed/item_pairs/session_keys/date=YYYY-MM-DD.parquet
data/processed/pair_aggregates/window_start=..._window_end=....parquet
data/processed/item_popularity/window_start=..._window_end=....parquet
data/processed/action_type_distribution/window_start=..._window_end=....parquet
```

`item_pairs/counts` хранит агрегированные дневные счётчики по directed item-pair.

`item_pairs/user_keys` и `item_pairs/session_keys` хранят deduplicated keys для точного подсчёта `unique_users` и
`unique_sessions` на rolling window.

В manifest поле `rows.daily_pairs` сохраняет старый смысл: количество raw directed pair rows, которое было бы построено
до compact aggregation. При этом сами raw daily pair rows больше не являются основным сохраняемым артефактом pipeline.

---

## Установка

```bash
uv sync
```

---

## Подготовка данных

Положи исходные архивы в директорию:

```text
data/raw/archives/
```

Ожидаемые архивы:

```text
product_information.tar.gz
user_actions.tar.gz
```

Подготовить raw parquet data:

```bash
uv run python scripts/prepare_raw_data.py
```

Проверить структуру проекта и наличие данных:

```bash
uv run python scripts/check_project_structure.py
```

---

## Запуски

Построить только рекомендации на train window:

```bash
uv run python scripts/run_pipeline.py 2024-04-23 --lookback-days 7 --top-k 20 --config-path configs/baseline.yaml
```

Полный запуск для demo/защиты: рекомендации строятся на train window, затем validation window вычисляется автоматически
как следующие `validation_days` дней после `train_until_date`, после чего считаются offline metrics.

```bash
uv run python scripts/run_full.py 2024-04-23 --lookback-days 1 --validation-days 1 --top-k 20 --config-path configs/production.yaml
```

Для примера выше train window будет `2024-04-23 .. 2024-04-23`, а validation window:
`2024-04-24 .. 2024-04-24`.

Запуск tuning по явному search space:

```bash
uv run python scripts/run_tune.py 2024-04-23 --lookback-days 1 --validation-days 1 --top-k 20 --config-path configs/production.yaml --search-space-path configs/tuning/search_space.yaml --max-trials 30 --tuning-strategy random
```

На текущем этапе `full` и `tune` безопаснее запускать с `lookback_days=1`: 7-day OOM в pair aggregation считается
отдельной задачей и в этот change set не входит.

Офлайн-оценка по умолчанию использует `evaluation.relevance_mode: binary`: любая наблюдаемая validation pair считается
релевантной с `relevance=1.0`, а информация о действиях сохраняется в ground truth через `view_count`, `click_count`,
`favorite_count`, `to_cart_count`. Graded relevance с ручными весами остается опциональным диагностическим режимом.

Основные offline metrics теперь делятся на три слоя:

- general ranking metrics: `hit_rate_at_k`, `recall_at_k`, `ndcg_at_k`, `mrr_at_k`, `coverage_at_k`;
- action-specific metrics: `view_*`, `click_*`, `favorite_*`, `to_cart_*`, где business-фокусом остаются
  `to_cart_hit_rate_at_k` и `to_cart_recall_at_k`.
- fallback-метрики: `fallback_*_share_at_k`, `fallback_hit_rate_at_k`, `fallback_recall_at_k`,
  `fallback_to_cart_hit_rate_at_k`, `fallback_to_cart_recall_at_k`.

Tuning использует balanced objective:

- основная метрика: `to_cart_hit_rate_at_k`;
- вспомогательные метрики: `ndcg_at_k`, `recall_at_k`, `mrr_at_k`, `coverage_at_k`, `to_cart_recall_at_k`,
  `fallback_hit_rate_at_k`;
- штрафные метрики: `popularity_bias_at_k`, `fallback_global_share_at_k`;
- итоговый `objective_score` считается как primary-gated geometric mean и пишется в `results.csv`, `best_metrics.json`.

`configs/tuning/search_space.yaml` теперь поддерживает `choice`, `int_range`, `float_range`, `log_float_range`, а
`run_tune.py` умеет `grid`, truly-random `random`, `successive_halving` и `simulated_annealing`.
Тюнинг fallback подробнее описан в `docs/fallback_tuning_evaluation.md`.

Основные outputs:

```text
data/processed/                 # reusable intermediate pipeline tables
outputs/runs/<run_id>/          # one full run: config, manifest, recommendations, evaluation
outputs/latest/                 # latest full run snapshot
outputs/tuning/<sweep_id>/      # tuning results, best_config.yaml, best_metrics.json
```

`configs/production.yaml` изначально совпадает с baseline. После tuning выбранный
`outputs/tuning/<sweep_id>/best_config.yaml`
можно перенести в `configs/production.yaml` отдельным осознанным изменением.

В GitHub Actions доступны только режимы `full` и `tune`.

---

## Посмотреть результат

После запуска проверь latest manifest и таблицы рекомендаций:

```bash
uv run python scripts/preview_latest_recommendations.py
# или через package CLI
uv run ozon-preview-recommendations
```

Скрипт выводит:

```text
RUN / WINDOW / SCORE / TOP_K
ROWS по этапам pipeline
preview detailed recommendations
preview compact lookup output
пример SimilarItemsLookup
```

Вернуть больше похожих товаров:

```bash
uv run python scripts/preview_latest_recommendations.py --top-k 20
```

Посмотреть lookup для конкретного товара:

```bash
uv run python scripts/preview_latest_recommendations.py --item-id 113
```

Использовать другой manifest:

```bash
uv run python scripts/preview_latest_recommendations.py --manifest-path outputs/latest/manifest.json
```

Успешный запуск должен показать, что в manifest `recommendations > 0`, а lookup возвращает список `similar_items`.

## Demo UI

Для презентации доступно Streamlit demo-приложение:

```bash
uv run streamlit run apps/demo/app.py -- --manifest-path outputs/latest/manifest.json
```

Оно позволяет искать товар по `item_id` или названию, смотреть похожие товары, run summary и placeholder под Gephi graph.

---

## Оптимизированный pipeline

Текущая версия pipeline ориентирована на длинные rolling window и большие объёмы пользовательских событий.

Основные оптимизации:

- raw events читаются по дням, а не одним full-window DataFrame;
- clean events сразу пишутся как daily parquet checkpoints;
- сессии строятся streaming-проходом по daily clean partitions;
- активные сессии, которые могут продолжиться на следующий день, переносятся через cross-day carry-over;
- сессии используют компактную идентичность через `user_id`, `session_index`, `session_start_date` вместо строкового
  `session_id`;
- raw daily item-pair rows не сохраняются как основной артефакт;
- вместо них пишутся compact daily pair stats: `counts`, `user_keys`, `session_keys`;
- pair aggregation строится из compact stats paths;
- pair scoring выполняется lazy-планом перед top-K selection.

Такая схема уменьшает memory pressure в наиболее тяжёлых местах pipeline: sessionization, pair building, pair
aggregation и scoring.

---

## Тесты и проверки

Запустить все тесты:

```bash
uv run pytest
```

Проверить pipeline runner:

```bash
uv run pytest tests/test_run_pipeline.py tests/test_run_full.py tests/test_run_tune.py
```

Проверить retrieval layer:

```bash
uv run pytest tests/test_build_pairs.py tests/test_aggregate_pairs.py tests/test_scoring.py tests/test_topk.py
```

Проверить recommendation output layer:

```bash
uv run pytest tests/test_topk.py tests/test_recommendation_writer.py tests/test_lookup.py tests/test_recommendation_manifest.py tests/test_recommendation_output_integration.py
```

Lint:

```bash
uv run ruff check src scripts tests
```

Type checking:

```bash
uv run pyrefly check src scripts tests
```

---

## Основные файлы

```text
configs/baseline.yaml                         # параметры baseline
configs/production.yaml                       # параметры production full run
configs/tuning/search_space.yaml              # явное пространство tuning
scripts/run_pipeline.py                       # построение рекомендаций
scripts/run_full.py                           # рекомендации + offline evaluation
scripts/run_tune.py                           # подбор параметров
scripts/preview_latest_recommendations.py     # просмотр результата и lookup
src/ozon_similar_products/preprocessing/      # clean events и session builder
src/ozon_similar_products/features/           # item popularity и calibration stats
src/ozon_similar_products/retrieval/          # pairs, aggregation, scoring, top-K
src/ozon_similar_products/diagnostics/        # reusable EDA/profiling/session diagnostics
src/ozon_similar_products/business/           # fallback and business rules
src/ozon_similar_products/evaluation/         # offline metrics and scorecards
src/ozon_similar_products/output/             # writers, manifest, lookup
docs/archive/                                 # archived EDA code and historical notes
tests/                                        # unit и integration tests
```

---

## Текущий статус

Реализован offline baseline:

- подготовка и чтение данных;
- очистка событий по дневным partition-ам;
- streaming-построение сессий с переносом активной сессии между днями;
- компактная идентичность сессии через `user_id`, `session_index`, `session_start_date`;
- построение compact daily item-pair stats вместо сохранения raw daily pair rows;
- агрегация пар из compact stats;
- item popularity и action-type calibration stats;
- lazy scoring перед top-K selection;
- top-K selection;
- сохранение рекомендаций;
- manifest/latest snapshot;
- lookup похожих товаров;
- тесты ключевых слоёв.

Проект пока является offline baseline, а не online serving-системой.
