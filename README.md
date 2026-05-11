# OZON Similar Products

Проект для кейса **Ozon Fresh**: offline-пайплайн для виджета **«Похожие товары по интересам пользователей»**.

Цель проекта — заранее построить item-to-item рекомендации: для каждого товара сформировать короткий список похожих товаров на основе пользовательского поведения и сохранить результат как воспроизводимый baseline-артефакт.

В постановке кейса используется название `sku`, в локальных данных проекта ему соответствует колонка `item_id`.

---

## Текущий статус

Проект уже прошёл стадию каркаса и содержит реализованные основные слои baseline:

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
- unit и integration tests для ключевых слоёв.

Важно: большинство отдельных модулей baseline уже реализовано, но официальный orchestration entrypoint `run_mvp_pipeline()` пока остаётся точкой будущей сборки полного запуска от raw data до saved recommendations.

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

Baseline не является персонализированным recommender system. Он заранее строит похожие товары для каждого товара, а не рекомендации для конкретного пользователя.

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

Скрипт проверяет:

- наличие ожидаемых директорий;
- наличие основных Python-модулей;
- количество parquet-файлов в `product_information` и `user_actions`;
- наличие исходных архивов.

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

Также здесь задаются:

- item action types;
- signal priority для построения item pairs;
- scoring method;
- business weights;
- calibration settings;
- thresholds;
- output paths.

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

Текущая архитектура baseline:

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

Модули в `src/ozon_similar_products/data/` отвечают за:

- чтение YAML-конфигов;
- работу с путями;
- подготовку архивов;
- поиск parquet partitions;
- чтение product information и user actions;
- profiling helpers для EDA;
- DataFrame contracts и validation.

### `EventCleaner`

Очищает raw user actions:

- валидирует вход;
- удаляет дубли;
- нормализует `timestamp`;
- фильтрует item action types;
- убирает строки без `item_id`;
- формирует `event_date`;
- приводит output к `CLEAN_EVENTS_COLUMNS`.

### `SessionBuilder`

Строит пользовательские сессии:

- сортирует события пользователя;
- считает time gaps;
- начинает новую сессию при превышении timeout;
- формирует `session_id`;
- возвращает `SESSIONS_COLUMNS`.

### `ItemPairBuilder`

Строит направленные item-item pairs:

- схлопывает повторные действия по товару внутри сессии до strongest signal;
- отбрасывает слишком короткие и слишком длинные сессии;
- строит directed pairs `item_id -> similar_item_id`;
- сохраняет action channel через `signal_type`.

### `PairAggregator`

Агрегирует daily pairs за rolling window:

- считает `pair_count`;
- считает `view_count`, `click_count`, `favorite_count`, `to_cart_count`;
- считает `unique_users`;
- считает `unique_sessions`;
- сохраняет `window_start` и `window_end`.

### `CoVisitationScorer`

Считает score для item-item pairs.

Поддерживаемые методы:

- `pair_count`;
- `calibrated_multichannel`.

`calibrated_multichannel` использует channel counts, business weights и calibration settings, но downstream-слои получают уже готовый `score`.

### `TopKSelector`

Получает `pair_scores` и строит `recommendations`:

- валидирует вход;
- удаляет `item_id == similar_item_id`;
- удаляет null `item_id`, `similar_item_id`, `score`;
- применяет optional thresholds;
- дедуплицирует пары;
- стабильно сортирует кандидатов;
- назначает `rank`;
- оставляет top-K;
- добавляет `source = behavioral`;
- сохраняет channel diagnostics для manual review.

### `RecommendationWriter`

Сохраняет результаты:

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

---

## Output artifacts

Ожидаемая структура output:

```text
outputs/
  recommendations/
    detailed/
      ...
    widget/
      ...
    latest/
      manifest.json
```

Для конкретного run-а могут сохраняться:

```text
recommendations.parquet       # detailed table
similar_items.parquet         # compact lookup table
manifest.json                 # параметры и пути run-а
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
- `top_k`;
- `score_method`;
- scoring parameters;
- calibration parameters;
- thresholds;
- пути к saved artifacts.

Пример полей:

```text
run_id
created_at
train_until_date
lookback_days
top_k
score_method
business_weights
beta
reference_action_type
max_frequency_boost
action_shares_used_for_calibration
calibration_start
calibration_end
min_pair_count
min_unique_users
min_unique_sessions
paths
```

---

## Проверка проекта

Запустить все тесты:

```bash
uv run pytest
```

Проверить только output/recommendation слой:

```bash
uv run pytest \
  tests/test_topk.py \
  tests/test_recommendation_writer.py \
  tests/test_lookup.py \
  tests/test_recommendation_manifest.py \
  tests/test_recommendation_output_integration.py
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

Можно проверить отдельные слои на synthetic DataFrame через unit tests.

Особенно важный тест:

```bash
uv run pytest tests/test_recommendation_output_integration.py
```

Он проверяет mini end-to-end цепочку output layer:

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

---

## Что ещё нужно сделать

Главная оставшаяся задача — реализовать официальный pipeline runner:

```text
src/ozon_similar_products/pipeline/run_mvp.py
```

Сейчас отдельные слои baseline уже реализованы, но полный запуск одной функцией ещё должен быть собран.

Ожидаемая роль `run_mvp_pipeline()`:

```text
load config
load data for rolling window
clean events
build sessions
build item pairs
aggregate pairs
score pairs
select top-K
save detailed output
save compact output
save manifest
update latest snapshot
```

После реализации runner-а нужно добавить full end-to-end тест на synthetic raw events и smoke run на небольшом сэмпле реальных данных.

---

## Рекомендуемый следующий PR

```text
Implement MVP pipeline runner
```

Что стоит включить:

- реализацию `run_mvp_pipeline()`;
- небольшой script или CLI для запуска;
- full pipeline integration test на synthetic data;
- обновление README с командой запуска полного baseline.

---

## Текущие ограничения

- Проект пока не является online serving-системой.
- Персонализация пользователей не реализуется в MVP.
- Fallback по category/brand/popularity пока не входит в основной baseline.
- `run_mvp_pipeline()` ещё нужно собрать, чтобы baseline запускался одной командой.
- Качество рекомендаций нужно дополнительно проверять manual review-таблицами с товарными полями.

---

## Полезные команды

```bash
# Установка зависимостей
uv sync

# Подготовка raw data
uv run python scripts/prepare_raw_data.py

# Проверка структуры проекта
uv run python scripts/check_project_structure.py

# Все тесты
uv run pytest

# Линтер
uv run ruff check src scripts tests

# Type checker
uv run pyrefly check
```

---

## Краткий статус

Реализованы основные building blocks offline baseline и output layer.

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
- tests.

Следующий ключевой шаг — собрать всё в полный `run_mvp_pipeline()`.
