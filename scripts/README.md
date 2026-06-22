# Скрипты запуска

В этой папке лежат пользовательские скрипты для запуска основных сценариев проекта.

Скрипты нужны, чтобы не вызывать внутренние модули пакета напрямую. Они дают простой вход для частых действий:
подготовить данные, проверить структуру проекта, построить рекомендации, запустить оценку качества, подобрать параметры
и посмотреть результат.

Для основных сценариев есть console commands из [`../pyproject.toml`](../pyproject.toml):

```bash
uv run ozon-run-pipeline
uv run ozon-run-full
uv run ozon-run-tune
uv run ozon-preview-recommendations
```

Их стоит использовать в документации и ручных запусках чаще, чем прямой вызов `python scripts/...`.

## Основные сценарии

```text
scripts/
  prepare_raw_data.py
  check_project_structure.py
  run_pipeline.py
  run_full.py
  run_tune.py
  compare_tuning.py
  preview_latest_recommendations.py
```

| Сценарий                     | Рекомендуемая команда                              | Для чего нужен                                               |
|------------------------------|----------------------------------------------------|--------------------------------------------------------------|
| подготовка данных            | `uv run python scripts/prepare_raw_data.py`        | подготовить локальные данные из исходных архивов             |
| проверка структуры           | `uv run python scripts/check_project_structure.py` | проверить структуру проекта, наличие папок, модулей и данных |
| построение рекомендаций      | `uv run ozon-run-pipeline`                         | построить рекомендации без отдельной оценки качества         |
| полный запуск                | `uv run ozon-run-full`                             | построить рекомендации и оценить качество                    |
| подбор параметров            | `uv run ozon-run-tune`                             | подобрать параметры по заданному пространству поиска         |
| сравнение tuning-результатов | `uv run python scripts/compare_tuning.py`          | посмотреть и отсортировать результаты подбора параметров     |
| просмотр рекомендаций        | `uv run ozon-preview-recommendations`              | вывести последние готовые рекомендации в удобном виде        |

## Рекомендуемый порядок запуска

Для первого локального запуска обычно достаточно такой последовательности:

```bash
uv run python scripts/prepare_raw_data.py
uv run python scripts/check_project_structure.py
uv run ozon-run-pipeline 2024-04-23 --lookback-days 7 --top-k 20 --config-path configs/baseline.yaml
uv run ozon-preview-recommendations
```

Если нужно не только построить рекомендации, но и проверить качество на следующем временном периоде, используйте полный
сценарий:

```bash
uv run ozon-run-full 2024-04-23 --lookback-days 1 --validation-days 1 --top-k 20 --config-path configs/production.yaml
```

## Подготовка данных

### `prepare_raw_data.py`

Скрипт готовит локальные данные из исходных архивов.

Ожидается, что архивы лежат здесь:

```text
data/raw/archives/
```

Ожидаемые файлы:

```text
product_information.tar.gz
user_actions.tar.gz
```

Запуск:

```bash
uv run python scripts/prepare_raw_data.py
```

Посмотреть содержимое архивов без распаковки:

```bash
uv run python scripts/prepare_raw_data.py --preview
```

Пересобрать подготовленные данные заново:

```bash
uv run python scripts/prepare_raw_data.py --force
```

После успешного запуска в `data/raw/` должны появиться подготовленные parquet-данные.

Связанные документы:

* [`../data/raw/README.md`](../data/raw/README.md);
* [`../src/ozon_similar_products/data/README.md`](../src/ozon_similar_products/data/README.md);
* [`../docs/data_contract.md`](../docs/data_contract.md).

## Проверка структуры проекта

### `check_project_structure.py`

Скрипт проверяет, что в проекте есть обязательные папки, Python-модули и исходные данные.

Запуск:

```bash
uv run python scripts/check_project_structure.py
```

Этот скрипт полезно запускать после:

* первого клонирования репозитория;
* подготовки данных;
* изменения структуры папок;
* удаления старых файлов;
* добавления новых модулей.

Если что-то отсутствует, команда покажет это в отчёте.

## Построение рекомендаций

### `ozon-run-pipeline`

Основной пользовательский способ запуска конвейера:

```bash
uv run ozon-run-pipeline 2024-04-23 --lookback-days 7 --top-k 20 --config-path configs/baseline.yaml
```

Эта команда соответствует сценарию [`run_pipeline.py`](run_pipeline.py).

Что происходит внутри:

```text
сырые события
→ очищенные события
→ пользовательские сессии
→ пары товаров
→ агрегированные пары
→ оценка похожести
→ лучшие рекомендации
→ резервные рекомендации
→ сохранение результата
```

Используйте этот сценарий, когда нужно быстро построить рекомендации и посмотреть результат без отдельной проверки
качества.

Основные параметры:

| Параметр          | Что означает                                       |
|-------------------|----------------------------------------------------|
| `run_date`        | дата, относительно которой строится временное окно |
| `--lookback-days` | сколько дней брать в расчёт                        |
| `--top-k`         | сколько рекомендаций строить для каждого товара    |
| `--config-path`   | какой файл настроек использовать                   |

Подробнее:

* [`../src/ozon_similar_products/pipeline/README.md`](../src/ozon_similar_products/pipeline/README.md);
* [`../docs/architecture.md`](../docs/architecture.md);
* [`../docs/data_contract.md`](../docs/data_contract.md).

## Полный запуск с оценкой качества

### `ozon-run-full`

Полный сценарий:

1. строит рекомендации на обучающем периоде;
2. проверяет их на следующем временном периоде;
3. сохраняет результаты и метрики.

Пример:

```bash
uv run ozon-run-full 2024-04-23 --lookback-days 1 --validation-days 1 --top-k 20 --config-path configs/production.yaml
```

Эта команда соответствует сценарию [`run_full.py`](run_full.py).

Используйте этот сценарий, когда нужно не просто получить рекомендации, а понять, насколько хорошо они работают на
будущих действиях пользователей.

Основные параметры:

| Параметр            | Что означает                                              |
|---------------------|-----------------------------------------------------------|
| `run_date`          | дата запуска                                              |
| `--lookback-days`   | сколько дней использовать для построения рекомендаций     |
| `--validation-days` | сколько следующих дней использовать для проверки качества |
| `--top-k`           | сколько рекомендаций проверять                            |
| `--config-path`     | какой файл настроек использовать                          |

Подробнее:

* [`../src/ozon_similar_products/evaluation/README.md`](../src/ozon_similar_products/evaluation/README.md);
* [`../docs/evaluation_metrics.md`](../docs/evaluation_metrics.md);
* [`../configs/README.md`](../configs/README.md).

## Подбор параметров

### `ozon-run-tune`

Команда запускает подбор параметров.

Пример:

```bash
uv run ozon-run-tune 2024-04-23 --lookback-days 1 --validation-days 1 --top-k 20 --config-path configs/production.yaml --search-space-path configs/tuning/search_space.yaml --max-trials 30 --tuning-strategy random
```

Эта команда соответствует сценарию [`run_tune.py`](run_tune.py).

Результаты сохраняются в:

```text
outputs/tuning/<sweep_id>/
  results.csv
  best_config.yaml
  best_metrics.json
```

Основные параметры:

| Параметр              | Что означает                            |
|-----------------------|-----------------------------------------|
| `--search-space-path` | файл с пространством подбора параметров |
| `--max-trials`        | максимальное число попыток              |
| `--tuning-strategy`   | стратегия перебора параметров           |

Поддерживаемые стратегии:

```text
grid
random
successive_halving
simulated_annealing
```

Для первого запуска обычно удобнее использовать `random`: он быстрее даёт несколько разных вариантов и помогает понять,
какие параметры влияют на качество.

Подробнее:

* [`../configs/README.md`](../configs/README.md);
* [`../configs/tuning/search_space.yaml`](../configs/tuning/search_space.yaml);
* [`../docs/evaluation_metrics.md`](../docs/evaluation_metrics.md).

## Сравнение результатов подбора

### `compare_tuning.py`

Скрипт читает `results.csv` из последнего запуска подбора параметров и показывает лучшие варианты.

Запуск по последнему результату:

```bash
uv run python scripts/compare_tuning.py
```

Явно указать файл с результатами:

```bash
uv run python scripts/compare_tuning.py --results-path outputs/tuning/<sweep_id>/results.csv
```

Отсортировать по другой метрике:

```bash
uv run python scripts/compare_tuning.py --sort-by ndcg_at_k --top-n 10
```

Этот скрипт полезен после `ozon-run-tune`, когда нужно быстро посмотреть, какие параметры дали лучший результат.

## Просмотр готовых рекомендаций

### `ozon-preview-recommendations`

Команда показывает последнюю опубликованную версию рекомендаций из `outputs/latest/`.

Посмотреть общий пример:

```bash
uv run ozon-preview-recommendations
```

Посмотреть рекомендации для конкретного товара:

```bash
uv run ozon-preview-recommendations --item-id 113
```

Эта команда соответствует сценарию [`preview_latest_recommendations.py`](preview_latest_recommendations.py).

Используйте её после `ozon-run-pipeline` или `ozon-run-full`, чтобы быстро проверить, что рекомендации сохранились и
выглядят разумно.

Подробнее:

* [`../src/ozon_similar_products/output/README.md`](../src/ozon_similar_products/output/README.md);
* [`../src/ozon_similar_products/serving/README.md`](../src/ozon_similar_products/serving/README.md).

## Что создаётся после запуска

Основные результаты сохраняются в `outputs/`.

```text
outputs/
  runs/
    <run_id>/
      recommendations/
      manifest.json
  latest/
    recommendations/
    manifest.json
  tuning/
    <sweep_id>/
      results.csv
      best_config.yaml
      best_metrics.json
```

`outputs/latest/` содержит последний опубликованный результат.

`outputs/tuning/` содержит результаты подбора параметров.

Подробнее о выходных файлах:

* [`../src/ozon_similar_products/output/README.md`](../src/ozon_similar_products/output/README.md);
* [`../docs/data_contract.md`](../docs/data_contract.md).

## Когда какую команду использовать

```text
подготовить данные               → uv run python scripts/prepare_raw_data.py
проверить структуру проекта       → uv run python scripts/check_project_structure.py
построить рекомендации            → uv run ozon-run-pipeline
построить и проверить качество    → uv run ozon-run-full
подобрать параметры               → uv run ozon-run-tune
сравнить попытки подбора          → uv run python scripts/compare_tuning.py
посмотреть последние рекомендации → uv run ozon-preview-recommendations
```

## Связанные документы

| Документ                                                                                                 | Что смотреть                                      |
|----------------------------------------------------------------------------------------------------------|---------------------------------------------------|
| [`../README.md`](../README.md)                                                                           | общий быстрый запуск проекта                      |
| [`../configs/README.md`](../configs/README.md)                                                           | настройки запусков и подбора параметров           |
| [`../docs/architecture.md`](../docs/architecture.md)                                                     | общий путь данных                                 |
| [`../docs/data_contract.md`](../docs/data_contract.md)                                                   | контракты входных и выходных таблиц               |
| [`../docs/evaluation_metrics.md`](../docs/evaluation_metrics.md)                                         | метрики качества                                  |
| [`../docs/incremental_update.md`](../docs/incremental_update.md)                                         | incremental-режим                                 |
| [`../docs/local_runner.md`](../docs/local_runner.md)                                                     | локальный self-hosted runner для тяжёлых запусков |
| [`../src/ozon_similar_products/pipeline/README.md`](../src/ozon_similar_products/pipeline/README.md)     | как устроен полный конвейер обработки             |
| [`../src/ozon_similar_products/evaluation/README.md`](../src/ozon_similar_products/evaluation/README.md) | как считается качество рекомендаций               |
| [`../src/ozon_similar_products/serving/README.md`](../src/ozon_similar_products/serving/README.md)       | как читать готовые рекомендации                   |

## Коротко

```text
1. uv run python scripts/prepare_raw_data.py
2. uv run python scripts/check_project_structure.py
3. uv run ozon-run-pipeline или uv run ozon-run-full
4. uv run ozon-preview-recommendations
```

Для экспериментов с качеством:

```text
uv run ozon-run-tune
→ uv run python scripts/compare_tuning.py
→ перенос лучших параметров в configs/production.yaml
```
