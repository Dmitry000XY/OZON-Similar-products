# Скрипты запуска

В этой папке лежат пользовательские скрипты для запуска основных сценариев проекта.

Скрипты нужны, чтобы не вызывать внутренние модули пакета напрямую. Они дают простой вход для частых действий:
подготовить данные, проверить структуру проекта, построить рекомендации, запустить оценку качества, подобрать параметры
и посмотреть результат.

## Основные сценарии

```text id="tcww8y"
scripts/
  prepare_raw_data.py
  check_project_structure.py
  run_pipeline.py
  run_full.py
  run_tune.py
  compare_tuning.py
  preview_latest_recommendations.py
```

| Скрипт                              | Для чего нужен                                               |
|-------------------------------------|--------------------------------------------------------------|
| `prepare_raw_data.py`               | подготовить локальные данные из исходных архивов             |
| `check_project_structure.py`        | проверить структуру проекта, наличие папок, модулей и данных |
| `run_pipeline.py`                   | построить рекомендации без отдельной оценки качества         |
| `run_full.py`                       | запустить полный сценарий: рекомендации и оценка качества    |
| `run_tune.py`                       | подобрать параметры по заданному пространству поиска         |
| `compare_tuning.py`                 | посмотреть и отсортировать результаты подбора параметров     |
| `preview_latest_recommendations.py` | вывести последние готовые рекомендации в удобном виде        |

## Рекомендуемый порядок запуска

Для первого локального запуска обычно достаточно такой последовательности:

```bash id="cum947"
uv run python scripts/prepare_raw_data.py
uv run python scripts/check_project_structure.py
uv run python scripts/run_pipeline.py 2024-04-23 --lookback-days 7 --top-k 20 --config-path configs/baseline.yaml
uv run python scripts/preview_latest_recommendations.py
```

Если нужно не только построить рекомендации, но и проверить качество на следующем временном периоде, используйте полный
сценарий:

```bash id="ostzno"
uv run python scripts/run_full.py 2024-04-23 --lookback-days 1 --validation-days 1 --top-k 20 --config-path configs/production.yaml
```

## Подготовка данных

### `prepare_raw_data.py`

Скрипт готовит локальные данные из исходных архивов.

Ожидается, что архивы лежат здесь:

```text id="rllok2"
data/raw/archives/
```

Ожидаемые файлы:

```text id="7ty27o"
product_information.tar.gz
user_actions.tar.gz
```

Запуск:

```bash id="9z7347"
uv run python scripts/prepare_raw_data.py
```

Посмотреть содержимое архивов без распаковки:

```bash id="qm1xtw"
uv run python scripts/prepare_raw_data.py --preview
```

Пересобрать подготовленные данные заново:

```bash id="9q59e8"
uv run python scripts/prepare_raw_data.py --force
```

После успешного запуска в `data/raw/` должны появиться подготовленные parquet-данные.

## Проверка структуры проекта

### `check_project_structure.py`

Скрипт проверяет, что в проекте есть обязательные папки, Python-модули и исходные данные.

Запуск:

```bash id="54v8w3"
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

### `run_pipeline.py`

Скрипт запускает основной конвейер обработки и строит рекомендации.

Пример:

```bash id="qfkt04"
uv run python scripts/run_pipeline.py 2024-04-23 --lookback-days 7 --top-k 20 --config-path configs/baseline.yaml
```

Что происходит внутри:

```text id="qfkjkp"
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

## Полный запуск с оценкой качества

### `run_full.py`

Скрипт запускает полный сценарий:

1. строит рекомендации на обучающем периоде;
2. проверяет их на следующем временном периоде;
3. сохраняет результаты и метрики.

Пример:

```bash id="v79v8x"
uv run python scripts/run_full.py 2024-04-23 --lookback-days 1 --validation-days 1 --top-k 20 --config-path configs/production.yaml
```

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

## Подбор параметров

### `run_tune.py`

Скрипт запускает подбор параметров.

Пример:

```bash id="qmw1d4"
uv run python scripts/run_tune.py 2024-04-23 --lookback-days 1 --validation-days 1 --top-k 20 --config-path configs/production.yaml --search-space-path configs/tuning/search_space.yaml --max-trials 30 --tuning-strategy random
```

Результаты сохраняются в:

```text id="g84dxl"
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

```text id="y7gk4k"
grid
random
successive_halving
simulated_annealing
```

Для первого запуска обычно удобнее использовать `random`: он быстрее даёт несколько разных вариантов и помогает понять,
какие параметры влияют на качество.

## Сравнение результатов подбора

### `compare_tuning.py`

Скрипт читает `results.csv` из последнего запуска подбора параметров и показывает лучшие варианты.

Запуск по последнему результату:

```bash id="pjcqiy"
uv run python scripts/compare_tuning.py
```

Явно указать файл с результатами:

```bash id="sxpap1"
uv run python scripts/compare_tuning.py --results-path outputs/tuning/<sweep_id>/results.csv
```

Отсортировать по другой метрике:

```bash id="bqgg9v"
uv run python scripts/compare_tuning.py --sort-by ndcg_at_k --top-n 10
```

Этот скрипт полезен после `run_tune.py`, когда нужно быстро посмотреть, какие параметры дали лучший результат.

## Просмотр готовых рекомендаций

### `preview_latest_recommendations.py`

Скрипт показывает последнюю опубликованную версию рекомендаций из `outputs/latest/`.

Посмотреть общий пример:

```bash id="h2u7o4"
uv run python scripts/preview_latest_recommendations.py
```

Посмотреть рекомендации для конкретного товара:

```bash id="h73q0h"
uv run python scripts/preview_latest_recommendations.py --item-id 113
```

Используйте этот скрипт после `run_pipeline.py` или `run_full.py`, чтобы быстро проверить, что рекомендации сохранились
и выглядят разумно.

## Альтернативные команды

Часть сценариев доступна не только через `scripts/`, но и как консольные команды пакета.

```bash id="npm15d"
uv run ozon-run-pipeline
uv run ozon-run-full
uv run ozon-run-tune
uv run ozon-preview-recommendations
```

На практике для документации и ручного запуска удобнее использовать скрипты из этой папки, потому что путь явно
показывает, какой сценарий запускается.

## Что создаётся после запуска

Основные результаты сохраняются в `outputs/`.

```text id="m9fr7m"
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

## Когда какой скрипт использовать

```text id="k43z0w"
подготовить данные             → prepare_raw_data.py
проверить структуру проекта     → check_project_structure.py
построить рекомендации          → run_pipeline.py
построить и проверить качество  → run_full.py
подобрать параметры             → run_tune.py
сравнить попытки подбора         → compare_tuning.py
посмотреть последние рекомендации → preview_latest_recommendations.py
```

## Связанные документы

| Документ                                            | Что смотреть                            |
|-----------------------------------------------------|-----------------------------------------|
| `../README.md`                                      | общий быстрый запуск проекта            |
| `../configs/README.md`                              | настройки запусков и подбора параметров |
| `../docs/data_io.md`                                | подготовка исходных данных              |
| `../docs/local_runner.md`                           | локальный запуск проекта                |
| `../src/ozon_similar_products/pipeline/README.md`   | как устроен полный конвейер обработки   |
| `../src/ozon_similar_products/evaluation/README.md` | как считается качество рекомендаций     |
| `../src/ozon_similar_products/serving/README.md`    | как читать готовые рекомендации         |

## Коротко

```text id="r8zb3n"
1. prepare_raw_data.py
2. check_project_structure.py
3. run_pipeline.py или run_full.py
4. preview_latest_recommendations.py
```

Для экспериментов с качеством:

```text id="mlleez"
run_tune.py
→ compare_tuning.py
→ перенос лучших параметров в configs/production.yaml
```
