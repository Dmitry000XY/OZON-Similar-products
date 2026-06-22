# Конвейер обработки

В этом модуле мы собираем все части проекта в один рабочий запуск.

Отдельные модули отвечают за свои этапы: `data` читает данные, `preprocessing` очищает события и строит сессии,
`retrieval` строит похожие товары, `business` добавляет резервные рекомендации, `output` сохраняет результат. Модуль
`pipeline` связывает эти шаги и следит, чтобы запуск был воспроизводимым.

## Что делает модуль

```text
сырые события
→ очищенные события
→ сессии
→ дневные статистики пар
→ агрегированные пары
→ score
→ top-K
→ fallback
→ сохранённые рекомендации
```

Главная функция модуля:

```python
run_pipeline(...)
```

Она запускает построение рекомендаций за выбранное временное окно и возвращает `PipelineRunResult` с путями к
сохранённым результатам.

## Основные файлы

| Файл                   | Что в нём находится                                                          |
|------------------------|------------------------------------------------------------------------------|
| `run_pipeline.py`      | полный запуск построения рекомендаций                                        |
| `artifact_manifest.py` | JSON-манифесты для переиспользуемых дневных артефактов                       |
| `scoring_output.py`    | запуск scoring, top-K, fallback и output поверх уже готовых train-артефактов |

## Главный сценарий

Обычный запуск идёт через скрипт:

```bash
uv run python scripts/run_pipeline.py 2024-04-23 --lookback-days 7 --top-k 20 --config-path configs/baseline.yaml
```

Внутри вызывается `run_pipeline`.

Пример прямого вызова из Python:

```python
from ozon_similar_products.pipeline.run_pipeline import run_pipeline

result = run_pipeline(
    train_until_date="2024-04-23",
    lookback_days=7,
    config_path="configs/baseline.yaml",
)
```

На выходе `result` содержит:

```text
run_id
run_dir
manifest_path
detailed_recommendations_path
enriched_recommendations_path
lookup_recommendations_path
manifest
```

## Временное окно

Конвейер строит рекомендации на rolling window.

Например:

```text
train_until_date = 2024-04-23
lookback_days = 7
```

Значит, в расчёт попадут события за период:

```text
2024-04-17 ... 2024-04-23
```

Функция сама считает `window_start` и `window_end`, а потом использует эти даты на всех этапах: чтение событий,
построение статистик, агрегация пар и сохранение манифеста.

## Этапы запуска

### 1. Чтение и очистка событий

Сначала конвейер читает пользовательские события по дням.

```text
data/raw/user_actions/
→ load_events
→ EventCleaner
→ clean events
```

События не загружаются одним большим куском за весь период. Конвейер идёт по дневным разделам, очищает каждый день и
сохраняет результат в промежуточные parquet-файлы.

Это снижает нагрузку на память и позволяет переиспользовать уже подготовленные дневные артефакты.

### 2. Построение сессий

После очистки событий строятся пользовательские сессии.

```text
clean events
→ SessionBuilder
→ sessions
```

В поточном режиме конвейер переносит активные сессии между днями. Это нужно для случаев, когда пользователь начал сессию
в конце одного дня, а продолжил в начале следующего.

Чтобы не держать всё в памяти, пользователи разбиваются на бакеты:

```yaml
pipeline:
  session_user_buckets: 64
  session_batch_size: 10000
```

### 3. Построение дневных статистик пар

Из завершённых сессий строятся компактные дневные статистики пар.

```text
sessions
→ ItemPairBuilder
→ daily pair stats
```

Конвейер сохраняет не только счётчики пар, но и ключи для точного подсчёта уникальных пользователей и сессий:

```text
counts
user_keys
session_keys
```

Так последующая агрегация может работать с компактными артефактами, а не со всеми сырыми строками пар.

### 4. Расчёт популярности товаров

По очищенным событиям считается популярность товаров и распределение типов действий.

```text
clean events
→ ItemPopularityBuilder
→ item_popularity
→ action_type_distribution
```

`item_popularity` нужен для резервных рекомендаций и возможной нормализации по популярности.

`action_type_distribution` используется для калибровки весов действий в `CoVisitationScorer`.

### 5. Агрегация пар

Дневные статистики пар объединяются за всё окно.

```text
daily pair stats
→ PairAggregator
→ pair_aggregates
```

Если включена агрегация по бакетам товаров, конвейер обрабатывает `item_id` частями:

```yaml
pipeline:
  aggregation_item_buckets: 1
```

При значении больше `1` агрегация, scoring и top-K выполняются по бакетам, чтобы снизить пиковое потребление памяти.

### 6. Расчёт score

Для агрегированных пар считается оценка похожести.

```text
pair_aggregates
→ CoVisitationScorer
→ pair_scores
```

Если в конфиге не заданы готовые `action_shares`, конвейер берёт их из `action_type_distribution`.

Так scoring может использовать фактическую частоту действий в текущем train-окне.

### 7. Выбор top-K

После scoring выбираются лучшие кандидаты для каждого товара.

```text
pair_scores
→ TopKSelector
→ behavioral recommendations
```

Параметры top-K берутся из блока `topk` или `pipeline`.

### 8. Резервные рекомендации

Если включён fallback, конвейер дополняет поведенческие рекомендации.

```text
behavioral recommendations
+ item_popularity
+ product_information
→ final recommendations
```

Fallback применяется после top-K. Он не меняет поведенческий `score`, а добавляет недостающие кандидаты с отдельным
`source`.

### 9. Сохранение результата

В конце конвейер сохраняет рекомендации в трёх форматах:

```text
detailed.parquet
enriched.parquet
lookup.parquet
```

И рядом сохраняет `manifest.json`.

Если `update_latest=True`, результат также публикуется в `outputs/latest/`.

## Что сохраняется после запуска

Обычная структура результата:

```text
outputs/
  runs/
    <run_id>/
      recommendations/
        detailed.parquet
        enriched.parquet
        lookup.parquet
      manifest.json

  latest/
    recommendations/
      detailed.parquet
      enriched.parquet
      lookup.parquet
    manifest.json
```

`outputs/runs/<run_id>/` хранит конкретный запуск.

`outputs/latest/` хранит последнюю опубликованную версию рекомендаций.

## Манифест запуска

`manifest.json` описывает, что было построено.

В нём сохраняются:

```text
run_id
generated_at
train_until_date
lookback_days
window_start
window_end
update_strategy
score_method
top_k
calibration_used
fallback_enabled
paths
rows
incremental
```

Блок `rows` помогает быстро проверить объём данных на каждом этапе:

```text
raw_events
clean_events
sessions
daily_pairs
pair_aggregates
pair_scores
recommendations
fallback_recommendations
```

Блок `incremental` показывает, какие дневные артефакты были переиспользованы, а какие построены заново.

## Переиспользуемые дневные артефакты

В `artifact_manifest.py` мы храним небольшие JSON-манифесты для дневных артефактов.

Они нужны для режима `incremental`.

Манифест содержит:

```text
artifact_type
date
schema_version
fingerprint
paths
rows
metadata
```

`fingerprint` строится из настроек, даты, входных файлов и параметров соответствующего этапа.

Если манифест совпадает с текущим запуском и все файлы на месте, конвейер может переиспользовать дневной артефакт вместо
пересборки.

## Стратегии обновления

В настройках есть параметр:

```yaml
pipeline:
  update_strategy: full_retrain
```

Поддерживаются значения:

```text
full_retrain
incremental
```

`full_retrain` пересобирает расчёт за всё выбранное окно.

`incremental` пытается переиспользовать дневные артефакты, если их манифесты валидны.

Для разработки и проверки проще использовать `full_retrain`.

Для ускорения повторных запусков на похожих данных нужен `incremental`.

## Почему pipeline не смешивает всю логику в себе

`pipeline` не содержит бизнес-смысл каждого этапа.

Он отвечает за порядок выполнения, пути, конфиги, промежуточные артефакты, манифесты и публикацию результата.

Сама логика остаётся в модулях:

```text
data          → чтение данных
preprocessing → очистка и сессии
features      → статистики
retrieval     → похожие товары
business      → fallback
output        → сохранение
```

Так проще менять отдельный этап, не переписывая весь запуск.

## Scoring-only запуск

В `scoring_output.py` есть helper `run_scoring_output_from_artifacts`.

Он нужен, когда train-артефакты уже построены, а мы хотим быстро пересчитать только:

```text
score
→ top-K
→ fallback
→ output
```

Это полезно для подбора параметров scoring и fallback: можно не пересобирать очистку событий, сессии и пары каждый раз.

На вход этому helper передаются уже готовые:

```text
pair_aggregates
item_popularity
action_distribution
```

А на выходе получается такой же `PipelineRunResult`, как у полного `run_pipeline`.

## Основные настройки

Чаще всего на поведение конвейера влияют эти блоки:

```yaml
pipeline:
  update_strategy: full_retrain
  session_timeout_minutes: 20
  max_items_per_session: 50
  session_batch_size: 10000
  session_user_buckets: 64
  aggregation_item_buckets: 1
  allow_empty_input: false
  allow_empty_latest_update: false

events:
  item_action_types:
    - view
    - click
    - favorite
    - to_cart

topk:
  top_k: 20
  source: behavioral
```

Подробное описание настроек находится в `configs/README.md`.

## Границы ответственности

Что делает `pipeline`:

* загружает конфиги;
* рассчитывает временное окно;
* вызывает этапы в правильном порядке;
* пишет промежуточные артефакты;
* валидирует возможность переиспользования артефактов;
* управляет incremental-режимом;
* собирает итоговый `manifest.json`;
* публикует результат в `outputs/latest/`.

Что не делает `pipeline`:

* не определяет контракты таблиц;
* не реализует очистку событий;
* не реализует scoring;
* не придумывает fallback-логику;
* не форматирует результат вручную;
* не читает готовые рекомендации для пользователя.

Эти задачи остаются в соответствующих модулях.

## Что менять осторожно

| Что менять                  | Почему осторожно                                           |
|-----------------------------|------------------------------------------------------------|
| `update_strategy`           | меняет поведение переиспользования артефактов              |
| `session_user_buckets`      | влияет на потоковую сборку сессий                          |
| `session_batch_size`        | влияет на память и скорость построения пар                 |
| `aggregation_item_buckets`  | меняет режим агрегации и scoring                           |
| fingerprints артефактов     | влияет на корректность incremental-режима                  |
| структуру `manifest.json`   | от неё зависят диагностика и downstream-чтение результатов |
| `update_latest`             | определяет, будет ли обновлён `outputs/latest/`            |
| `allow_empty_latest_update` | может опубликовать пустой результат как latest             |

Если меняется порядок этапов или структура промежуточных артефактов, нужно проверять `run_pipeline.py`, `run_full.py`,
tuning-сценарии и serving-слой.

## Быстрая проверка

Полный запуск построения рекомендаций:

```bash
uv run python scripts/run_pipeline.py 2024-04-23 --lookback-days 7 --top-k 20 --config-path configs/baseline.yaml
```

Посмотреть результат:

```bash
uv run python scripts/preview_latest_recommendations.py
```

Проверить, что появился run:

```text
outputs/runs/<run_id>/recommendations/
outputs/runs/<run_id>/manifest.json
outputs/latest/recommendations/
outputs/latest/manifest.json
```

## Связанные документы

| Документ                     | Что смотреть                                  |
|------------------------------|-----------------------------------------------|
| `../data/README.md`          | чтение и подготовка входных данных            |
| `../preprocessing/README.md` | очистка событий и построение сессий           |
| `../features/README.md`      | популярность товаров и распределение действий |
| `../retrieval/README.md`     | пары товаров, score и top-K                   |
| `../business/README.md`      | резервные рекомендации                        |
| `../output/README.md`        | сохранение результата                         |
| `../evaluation/README.md`    | проверка качества после запуска               |
| `../../../configs/README.md` | настройки конвейера                           |
| `../../../scripts/README.md` | команды запуска                               |

## Коротко

Мы используем `pipeline`, чтобы собрать все этапы проекта в один воспроизводимый запуск.

Этот модуль не заменяет отдельные слои, а связывает их между собой.

Он читает данные, вызывает нужные классы, сохраняет промежуточные и итоговые артефакты, пишет манифест и публикует
последнюю версию рекомендаций.
