# OZON Similar Products

Проект для кейса **Ozon Fresh**: offline-пайплайн для виджета **«Похожие товары по интересам пользователей»**.

Цель проекта — заранее построить item-to-item рекомендации: для каждого товара сформировать короткий список похожих товаров на основе пользовательского поведения и сохранить результат как воспроизводимый baseline-артефакт.

В постановке кейса используется название `sku`, в локальных данных проекта ему соответствует колонка `item_id`.

---

## Текущий статус

Проект содержит реализованный MVP baseline на уровне основных модулей и orchestration runner-а.

Реализовано:

- загрузка и подготовка сырых parquet-данных;
- контракты DataFrame-таблиц и валидация схем;
- EDA/profiling helpers;
- очистка пользовательских событий;
- построение пользовательских сессий;
- построение directed multichannel item-item pairs;
- агрегация пар по rolling window;
- scoring pair aggregates;
- выбор top-K похожих товаров;
- сохранение detailed и compact recommendation outputs;
- manifest/latest snapshot для воспроизводимости;
- lookup-интерфейс для получения похожих товаров по `item_id`;
- официальный `run_mvp_pipeline()` для сборки baseline от raw events до saved recommendations;
- unit и integration tests для ключевых слоёв.

Текущий следующий шаг качества — smoke run на небольшом окне реальных данных и manual/offline evaluation качества рекомендаций.

---

## Идея baseline

Baseline строится как offline item-to-item retrieval.

Основная идея:

1. Берём пользовательские события.
2. Оставляем товарные действия.
3. Собираем короткие пользовательские сессии.
4. Внутри сессий строим направленные пары товаров `item_id -> similar_item_id`.
5. Агрегируем пары за rolling window.
6. Считаем score для каждой пары.
7. Для каждого `item_id` выбираем top-K кандидатов.
8. Сохраняем detailed output для анализа и compact output для lookup.
9. Через `SimilarItemsLookup` быстро получаем похожие товары без пересчёта pipeline.

Baseline не является персонализированной рекомендательной системой. Он заранее строит похожие товары для каждого товара, а не рекомендации для конкретного пользователя.

---

## Данные

В проекте используются два архива:

- `product_information.tar.gz` — справочник товаров;
- `user_actions.tar.gz` — логи действий пользователей.

После подготовки сырые данные лежат в `data/raw/` и не попадают в Git:

```text
data/raw/archives/              # исходные архивы
data/raw/product_information/   # распакованный справочник товаров
data/raw/user_actions/          # распакованные действия пользователей
```

Основные локальные идентификаторы:

```text
sku в постановке задачи  -> item_id в локальных данных
```

---

## Установка

Проект использует `uv`.

```bash
uv sync
```

Основные зависимости проекта:

- `polars`;
- `pyarrow`;
- `pyyaml`.

Dev-зависимости:

- `pytest`;
- `pytest-cov`;
- `ruff`;
- `pyrefly`;
- notebook tooling.

---

## Подготовка данных

Помести архивы в директорию, ожидаемую конфигом, затем выполни:

```bash
uv run python scripts/prepare_raw_data.py
```

Проверить структуру проекта и наличие подготовленных данных:

```bash
uv run python scripts/check_project_structure.py
```

---

## Основные конфиги

### `configs/data.yaml`

Описывает источники данных, ожидаемые архивы, директории и колонки.

### `configs/paths.yaml`

Описывает структуру проекта, директории данных, output-директории и ожидаемые Python-модули.

### `configs/baseline.yaml`

Содержит параметры baseline:

```yaml
pipeline:
  session_timeout_minutes: 30
  max_items_per_session: 50
  top_k: 20
  lookback_days: 30
```

Также здесь задаются item action types, signal priority, scoring method, business weights, calibration settings, thresholds, processed artifact paths и recommendation output paths.

---

## Контракты данных

Ключевые контракты лежат в `src/ozon_similar_products/data/schemas.py`.

### Clean events

```text
user_id
event_date
timestamp
action_type
item_id
search_query
widget_name
```

### Sessions

```text
user_id
session_id
event_date
timestamp
action_type
item_id
```

### Daily item pairs

```text
pair_date
item_id
similar_item_id
session_id
user_id
source_action_type
target_action_type
signal_type
```

### Pair aggregates

```text
item_id
similar_item_id
pair_count
view_count
click_count
favorite_count
to_cart_count
unique_users
unique_sessions
window_start
window_end
```

### Pair scores

```text
item_id
similar_item_id
score
pair_count
view_count
click_count
favorite_count
to_cart_count
unique_users
unique_sessions
```

### Recommendations

```text
item_id
similar_item_id
score
rank
source
```

### Compact widget output

```text
item_id
similar_items_sku_list
```

---

## Архитектура pipeline

```text
raw user_actions
  -> EventCleaner
  -> events_clean

events_clean
  -> SessionBuilder
  -> sessions

sessions
  -> ItemPairBuilder
  -> daily item pairs

daily item pairs
  -> PairAggregator
  -> pair aggregates

pair aggregates
  -> CoVisitationScorer
  -> pair scores

pair scores
  -> TopKSelector
  -> recommendations

recommendations
  -> RecommendationWriter
  -> detailed recommendations
  -> compact widget output
  -> manifest/latest

latest manifest or compact output
  -> SimilarItemsLookup
  -> get_similar_items(item_id)
```

---

## Реализованные слои

### Data loading and profiling

Модули в `src/ozon_similar_products/data/` отвечают за чтение YAML-конфигов, работу с путями, подготовку архивов, поиск parquet partitions, чтение product information и user actions, profiling helpers, DataFrame contracts и validation.

### `EventCleaner`

Очищает raw user actions: валидирует вход, удаляет дубли, нормализует `timestamp`, фильтрует item action types, убирает строки без `item_id`, формирует `event_date` и приводит output к `CLEAN_EVENTS_COLUMNS`.

### `SessionBuilder`

Строит пользовательские сессии: сортирует события пользователя, считает time gaps, начинает новую сессию при превышении timeout, формирует `session_id` и возвращает `SESSIONS_COLUMNS`.

### `ItemPairBuilder`

Строит направленные item-item pairs: схлопывает повторные действия по товару внутри сессии до strongest signal, отбрасывает слишком короткие и слишком длинные сессии, строит directed pairs `item_id -> similar_item_id` и сохраняет action channel через `signal_type`.

### `PairAggregator`

Агрегирует daily pairs за rolling window: считает `pair_count`, `view_count`, `click_count`, `favorite_count`, `to_cart_count`, `unique_users`, `unique_sessions`, `window_start` и `window_end`.

### `CoVisitationScorer`

Считает score для item-item pairs.

Поддерживаемые методы:

- `pair_count`;
- `calibrated_multichannel`.

`calibrated_multichannel` использует channel counts, business weights и calibration settings, но downstream-слои получают уже готовый `score`.

### `TopKSelector`

Получает `pair_scores` и строит `recommendations`: валидирует вход, удаляет self-pairs и null-кандидатов, применяет optional thresholds, дедуплицирует пары, стабильно сортирует кандидатов, назначает `rank`, оставляет top-K, добавляет `source = behavioral` и сохраняет channel diagnostics для manual review.

### `RecommendationWriter`

Сохраняет:

- detailed recommendations parquet;
- compact widget parquet;
- run manifest;
- latest manifest.

Detailed output нужен для анализа качества и ручной проверки.

Compact output нужен для быстрого lookup:

```text
item_id -> similar_items_sku_list
```

### `SimilarItemsLookup`

Читает сохранённые compact recommendations или `manifest.json` и возвращает похожие товары:

```python
lookup = SimilarItemsLookup("outputs/recommendations/latest/manifest.json")
lookup.get_similar_items(item_id=123, top_k=10)
```

Если `item_id` отсутствует в output, возвращается пустой список.

### `run_mvp_pipeline`

Официальный runner baseline. Он выполняет полный MVP-проход:

```text
load config
load raw events for rolling window
clean events
build sessions
build item pairs
aggregate pairs
build item popularity
build action type distribution
score pairs
select top-K
save processed artifacts
save detailed recommendations
save widget recommendations
save manifest
update latest manifest
```

---

## Запуск полного MVP pipeline

После подготовки данных можно запустить pipeline через CLI-скрипт:

```bash
uv run python scripts/run_mvp_pipeline.py   --train-until-date YYYY-MM-DD   --lookback-days 30
```

Например:

```bash
uv run python scripts/run_mvp_pipeline.py   --train-until-date 2026-05-01   --lookback-days 30
```

Можно передать другой config:

```bash
uv run python scripts/run_mvp_pipeline.py   --train-until-date 2026-05-01   --lookback-days 7   --config-path configs/baseline.yaml
```

`train_until_date` должен соответствовать датам, которые реально есть в `user_actions`.

---

## Output artifacts

Ожидаемая структура output:

```text
outputs/
  recommendations/
    runs/
      run_YYYY-MM-DD_lb30/
        detailed/
          recommendations.parquet
        widget/
          similar_items.parquet
        manifest.json
    latest/
      manifest.json
```

Runner также может сохранять processed artifacts:

```text
data/processed/events_clean/
data/processed/sessions/
data/processed/item_pairs/
data/processed/pair_aggregates/
data/processed/item_popularity/
data/processed/action_type_distribution/
```

`latest/manifest.json` указывает на актуальный compact output, который может читать `SimilarItemsLookup`.

---

## Manifest

Manifest нужен для воспроизводимости.

Он фиксирует:

- `run_id`;
- время создания;
- дату окончания train window;
- `lookback_days`;
- `window_start`;
- `window_end`;
- `top_k`;
- `score_method`;
- сведения о calibration;
- thresholds;
- пути к saved artifacts;
- количество строк на ключевых этапах pipeline.

---

## Проверка проекта

Запустить все тесты:

```bash
uv run pytest
```

Проверить output/recommendation слой:

```bash
uv run pytest   tests/test_topk.py   tests/test_recommendation_writer.py   tests/test_lookup.py   tests/test_recommendation_manifest.py   tests/test_recommendation_output_integration.py
```

Проверить runner:

```bash
uv run pytest tests/test_run_mvp_pipeline.py
```

Проверить lint:

```bash
uv run ruff check src scripts tests
```

Проверить type checking:

```bash
uv run pyrefly check
```

---

## Что уже можно проверить

### Output layer integration

```bash
uv run pytest tests/test_recommendation_output_integration.py
```

Этот тест проверяет mini end-to-end цепочку output layer:

```text
synthetic pair_scores
  -> TopKSelector
  -> save_detailed
  -> save_widget_format
  -> save_manifest
  -> update_latest_manifest
  -> SimilarItemsLookup
  -> get_similar_items
```

### Full runner smoke test

```bash
uv run pytest tests/test_run_mvp_pipeline.py
```

Этот тест проверяет, что `run_mvp_pipeline()` может пройти полный synthetic сценарий, сохранить artifacts, обновить latest manifest и отдать рекомендации через `SimilarItemsLookup`.

---

## Текущие ограничения

- Проект пока не является online serving-системой.
- Персонализация пользователей не реализуется в MVP.
- Fallback по category/brand/popularity пока не входит в основной baseline.
- `update_strategy.py` содержит заготовки под future full-retrain/incremental strategies и не является обязательной частью текущего MVP execution path.
- Качество рекомендаций нужно дополнительно проверять manual review-таблицами с товарными полями.
- После synthetic tests нужен smoke run на небольшом окне реальных данных.

---

## Рекомендуемые следующие шаги

1. Прогнать `tests/test_run_mvp_pipeline.py`.
2. Запустить `run_mvp_pipeline()` на небольшом реальном окне данных.
3. Проверить `outputs/recommendations/latest/manifest.json`.
4. Проверить несколько товаров через `SimilarItemsLookup`.
5. Добавить manual review notebook/report с `item_id`, `similar_item_id`, `score`, `rank`, `source` и товарными полями из `product_information`.
6. После этого переходить к offline evaluation и fallback-логике.

---

## Полезные команды

```bash
# Установка зависимостей
uv sync

# Подготовка raw data
uv run python scripts/prepare_raw_data.py

# Проверка структуры проекта
uv run python scripts/check_project_structure.py

# Запуск полного MVP pipeline
uv run python scripts/run_mvp_pipeline.py --train-until-date YYYY-MM-DD --lookback-days 30

# Все тесты
uv run pytest

# Линтер
uv run ruff check src scripts tests

# Type checker
uv run pyrefly check
```

---

## Краткий статус

Реализованы основные building blocks offline baseline, output layer и полный runner.

Baseline уже имеет:

- контракты;
- preprocessing;
- sessionization;
- item-pair generation;
- pair aggregation;
- scoring;
- top-K selection;
- saved outputs;
- manifest/latest;
- lookup;
- `run_mvp_pipeline()`;
- tests.

Следующий ключевой шаг — подтвердить качество и стабильность pipeline через smoke run на реальных данных и manual/offline evaluation.
