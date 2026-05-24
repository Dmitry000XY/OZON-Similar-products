# OZON Similar Products

Offline-пайплайн для кейса **Ozon Fresh**: построение виджета **«Похожие товары по интересам пользователей»**.

Проект строит item-to-item рекомендации на основе поведения пользователей: для каждого товара (`item_id`) формируется
список похожих товаров, который затем можно использовать в виджете или проверять через lookup.

В постановке кейса используется название `sku`; в локальных данных проекта ему соответствует колонка `item_id`.

---

## Что делает проект

Pipeline строит baseline-рекомендации по цепочке:

```text
raw user actions
→ clean events
→ sessions
→ item-item pairs
→ pair aggregates
→ pair scores
→ top-K recommendations
→ saved artifacts
→ lookup
```

На выходе создаются:

```text
outputs/recommendations/runs/.../recommendations.parquet
outputs/recommendations/runs/.../similar_items.parquet
outputs/recommendations/runs/.../manifest.json
outputs/recommendations/latest/manifest.json
```

`latest/manifest.json` указывает на актуальную версию рекомендаций.

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

## Запуск baseline pipeline

Запуск полного MVP pipeline:

```bash
uv run python scripts/run_mvp_pipeline.py 2024-04-30 --lookback-days 1
```

или через package CLI

```bash
uv run ozon-run-mvp 2024-04-30 --lookback-days 1
```

Где:

```text
2024-04-30      # последняя дата train window
--lookback-days # размер rolling window в днях
```

По умолчанию используется config:

```text
configs/baseline.yaml
```

Можно передать другой config:

```bash
uv run python scripts/run_mvp_pipeline.py 2024-04-30 --lookback-days 1 --config-path configs/baseline.yaml
```

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
uv run python scripts/preview_latest_recommendations.py --manifest-path outputs/recommendations/latest/manifest.json
```

Успешный запуск должен показать, что в manifest `recommendations > 0`, а lookup возвращает список `similar_items`.

---

## Тесты и проверки

Запустить все тесты:

```bash
uv run pytest
```

Проверить pipeline runner:

```bash
uv run pytest tests/test_run_mvp.py
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
scripts/run_mvp_pipeline.py                   # запуск полного pipeline
scripts/preview_latest_recommendations.py     # просмотр результата и lookup
src/ozon_similar_products/pipeline/run_mvp.py # orchestration runner
src/ozon_similar_products/retrieval/          # pairs, scoring, top-K
src/ozon_similar_products/diagnostics/        # reusable EDA/profiling/session diagnostics
src/ozon_similar_products/business/           # fallback and business rules
src/ozon_similar_products/evaluation/         # offline metrics and scorecards
src/ozon_similar_products/output/             # writers, manifest, lookup
docs/archive/                                 # archived EDA code and historical notes
tests/                                        # unit и integration tests
```

---

## Текущий статус

Реализован MVP baseline:

- подготовка и чтение данных;
- очистка событий;
- построение сессий;
- построение item-item пар;
- агрегация пар;
- scoring;
- top-K selection;
- сохранение рекомендаций;
- manifest/latest snapshot;
- lookup похожих товаров;
- тесты ключевых слоёв.

Проект пока является offline baseline, а не online serving-системой.
