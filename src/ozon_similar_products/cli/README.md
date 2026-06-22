# CLI-команды проекта

В этой папке находятся console entrypoints проекта.

CLI-слой нужен для пользовательских запусков из терминала. Он не содержит основную бизнес-логику: команды только
разбирают аргументы, загружают конфиги и вызывают функции из модулей [`pipeline`](../pipeline/README.md), [
`evaluation`](../evaluation/README.md), [`output`](../output/README.md) и [`serving`](../serving/README.md).

## Доступные команды

Команды зарегистрированы в `pyproject.toml`.

| Команда                        | Python entrypoint                                        | Назначение                                               |
|--------------------------------|----------------------------------------------------------|----------------------------------------------------------|
| `ozon-run-pipeline`            | `ozon_similar_products.cli.run_pipeline:main`            | построить рекомендации за train-окно                     |
| `ozon-run-full`                | `ozon_similar_products.cli.run_full:main`                | построить рекомендации и сразу посчитать offline-метрики |
| `ozon-run-tune`                | `ozon_similar_products.cli.run_tune:main`                | запустить подбор параметров по search space              |
| `ozon-preview-recommendations` | `ozon_similar_products.cli.preview_recommendations:main` | посмотреть последние рекомендации и проверить lookup     |

Запускать их лучше через `uv run`.

## `ozon-run-pipeline`

Команда запускает полный pipeline построения рекомендаций без validation-оценки.

Пример запуска: `uv run ozon-run-pipeline 2024-04-23 --lookback-days 7 --top-k 20 --config-path configs/baseline.yaml`

Что делает команда:

* читает конфиг;
* при необходимости переопределяет `top_k`;
* вызывает `run_pipeline`;
* строит рекомендации на rolling window;
* сохраняет `detailed.parquet`, `enriched.parquet`, `lookup.parquet` и `manifest.json`.

Основные аргументы:

| Аргумент           | Что означает                            |
|--------------------|-----------------------------------------|
| `train_until_date` | конец train-окна в формате `YYYY-MM-DD` |
| `--lookback-days`  | размер train-окна в днях                |
| `--top-k`          | переопределение размера top-K           |
| `--config-path`    | путь к YAML-конфигу                     |

Подробнее о полном pipeline: [`../pipeline/README.md`](../pipeline/README.md).

## `ozon-run-full`

Команда запускает построение рекомендаций и offline-оценку качества.

Пример запуска:
`uv run ozon-run-full 2024-04-23 --lookback-days 7 --validation-days 1 --top-k 20 --config-path configs/production.yaml`

Что делает команда:

* строит рекомендации на train-периоде;
* строит validation-период сразу после `train_until_date`;
* собирает validation pair counts;
* строит ground truth;
* считает offline-метрики;
* сохраняет `metrics.json`, `scorecard.json` и `evaluation_manifest.json`;
* может опубликовать результат в `outputs/latest`.

Основные аргументы:

| Аргумент                      | Что означает                                         |
|-------------------------------|------------------------------------------------------|
| `train_until_date`            | конец train-окна в формате `YYYY-MM-DD`              |
| `--lookback-days`             | размер train-окна                                    |
| `--validation-days`           | сколько дней после train использовать для validation |
| `--top-k`                     | размер top-K                                         |
| `--config-path`               | путь к YAML-конфигу                                  |
| `--run-name`                  | дополнительная метка запуска                         |
| `--output-dir`                | директория для run-результатов                       |
| `--latest-dir`                | директория для публикации latest                     |
| `--keep-evaluation-artifacts` | сохранить debug-артефакты evaluation                 |

Подробнее об оценке качества: [`../evaluation/README.md`](../evaluation/README.md) и [
`../../../docs/evaluation_metrics.md`](../../../docs/evaluation_metrics.md).

## `ozon-run-tune`

Команда запускает подбор параметров по search space.

Пример запуска:
`uv run ozon-run-tune 2024-04-23 --lookback-days 7 --validation-days 1 --top-k 20 --config-path configs/production.yaml --search-space-path configs/tuning/search_space.yaml --max-trials 30 --tuning-strategy random`

Что делает команда:

* читает базовый конфиг;
* читает search space;
* генерирует trial-конфиги;
* запускает полный evaluation run или fast scoring-only trial;
* сохраняет результаты каждого trial;
* выбирает лучший trial по objective;
* пишет `results.csv`, `best_config.yaml`, `best_metrics.json` и `summary.json`.

Поддерживаемые стратегии:

| Стратегия             | Что делает                                                                     |
|-----------------------|--------------------------------------------------------------------------------|
| `grid`                | перебирает комбинации параметров по сетке                                      |
| `random`              | случайно выбирает варианты из search space                                     |
| `successive_halving`  | сначала проверяет больше конфигов на меньшем ресурсе, затем дооценивает лучших |
| `simulated_annealing` | исследует соседние конфиги с вероятностным принятием ухудшений                 |

Основные аргументы:

| Аргумент              | Что означает                                     |
|-----------------------|--------------------------------------------------|
| `train_until_date`    | конец train-окна                                 |
| `--lookback-days`     | размер train-окна                                |
| `--validation-days`   | размер validation-окна                           |
| `--top-k`             | размер top-K                                     |
| `--config-path`       | базовый конфиг                                   |
| `--search-space-path` | YAML с пространством поиска                      |
| `--max-trials`        | максимум trial-запусков                          |
| `--tuning-strategy`   | стратегия подбора                                |
| `--output-dir`        | директория для sweep-результатов                 |
| `--sweep-name`        | человекочитаемая метка sweep                     |
| `--fast-scoring-only` | ускоренный режим без пересборки train-артефактов |

Важно: `--fast-scoring-only` не поддерживает `successive_halving`.

Подробнее о настройках tuning: [`../../../configs/README.md`](../../../configs/README.md).

## `ozon-preview-recommendations`

Команда показывает последние сохранённые рекомендации и проверяет `SimilarItemsLookup`.

Пример запуска: `uv run ozon-preview-recommendations`

Пример с конкретным товаром: `uv run ozon-preview-recommendations --item-id 153774 --top-k 10`

Что делает команда:

* читает `outputs/latest/manifest.json`;
* печатает краткую информацию о run;
* показывает preview подробных рекомендаций;
* показывает compact lookup-таблицу;
* выбирает `item_id`;
* проверяет `SimilarItemsLookup`;
* выводит список похожих товаров.

Основные аргументы:

| Аргумент          | Что означает                                |
|-------------------|---------------------------------------------|
| `--manifest-path` | путь к latest manifest                      |
| `--item-id`       | товар, для которого нужно показать похожие  |
| `--top-k`         | сколько похожих товаров вернуть             |
| `--head`          | сколько строк показывать в preview-таблицах |

Подробнее о serving-слое: [`../serving/README.md`](../serving/README.md).

## Как устроены CLI-файлы

| Файл                                                       | Что делает                                        |
|------------------------------------------------------------|---------------------------------------------------|
| [`run_pipeline.py`](run_pipeline.py)                       | CLI-обёртка над `pipeline.run_pipeline`           |
| [`run_full.py`](run_full.py)                               | полный запуск с offline evaluation                |
| [`run_tune.py`](run_tune.py)                               | подбор параметров по search space                 |
| [`preview_recommendations.py`](preview_recommendations.py) | просмотр latest-рекомендаций                      |
| [`scoring_only_tuning.py`](scoring_only_tuning.py)         | ускоренный tuning поверх готовых train-артефактов |

## Что CLI не должен делать

CLI не должен содержать основную логику проекта.

Он не должен:

* реализовывать очистку событий;
* строить пары товаров вручную;
* считать `score` внутри argparse-скриптов;
* дублировать fallback-логику;
* вручную форматировать output-файлы;
* менять контракты таблиц.

CLI должен быть тонким слоем:

1. разобрать аргументы;
2. загрузить конфиг;
3. при необходимости применить override;
4. вызвать функцию из нужного модуля;
5. вернуть код завершения.

## Где лежат результаты

| Команда                        | Основные результаты                                                           |
|--------------------------------|-------------------------------------------------------------------------------|
| `ozon-run-pipeline`            | `outputs/runs/<run_id>/`, `outputs/latest/`                                   |
| `ozon-run-full`                | `outputs/runs/<run_id>/recommendations/`, `outputs/runs/<run_id>/evaluation/` |
| `ozon-run-tune`                | `outputs/tuning/<sweep_id>/`                                                  |
| `ozon-preview-recommendations` | ничего не сохраняет, только читает latest-артефакты                           |

## Что менять осторожно

| Что менять               | Почему осторожно                                             |
|--------------------------|--------------------------------------------------------------|
| имена console scripts    | на них ссылаются README, workflow и пользователи             |
| аргументы CLI            | могут сломаться команды в документации                       |
| default `config-path`    | влияет на поведение запуска без явного конфига               |
| override `top_k`         | должен согласованно применяться к pipeline, top-K и fallback |
| структуру tuning outputs | её используют сравнение запусков и отчёты                    |
| preview latest logic     | она зависит от структуры manifest и output                   |

Если меняется CLI-команда, нужно проверить:

* [`../../../README.md`](../../../README.md);
* [`../../../scripts/README.md`](../../../scripts/README.md);
* [`../../../configs/README.md`](../../../configs/README.md);
* [`../pipeline/README.md`](../pipeline/README.md);
* [`../evaluation/README.md`](../evaluation/README.md);
* GitHub Actions workflow для локального runner, если команда используется там.

## Связанные документы

| Документ                                                         | Что смотреть                                       |
|------------------------------------------------------------------|----------------------------------------------------|
| [`../pipeline/README.md`](../pipeline/README.md)                 | полный pipeline построения рекомендаций            |
| [`../evaluation/README.md`](../evaluation/README.md)             | offline-оценка качества                            |
| [`../serving/README.md`](../serving/README.md)                   | чтение готового lookup-результата                  |
| [`../../../scripts/README.md`](../../../scripts/README.md)       | пользовательские сценарии запуска                  |
| [`../../../configs/README.md`](../../../configs/README.md)       | конфиги pipeline, evaluation и tuning              |
| [`../../../docs/local_runner.md`](../../../docs/local_runner.md) | тяжёлые локальные запуски через self-hosted runner |

## Коротко

CLI-команды — это удобный вход в проект из терминала.

Основные команды:

* `ozon-run-pipeline` — построить рекомендации;
* `ozon-run-full` — построить рекомендации и оценить качество;
* `ozon-run-tune` — подобрать параметры;
* `ozon-preview-recommendations` — посмотреть latest-рекомендации.

Основная логика остаётся в модулях проекта, а CLI только связывает аргументы командной строки с этими модулями.
