# Оценка качества

В этом модуле мы проверяем, насколько хорошо построенные рекомендации совпадают с будущими действиями пользователей.

Основной конвейер строит похожие товары на одном временном периоде. После этого мы берём следующий период и смотрим,
появились ли рекомендованные товары в реальных пользовательских действиях. Так мы получаем offline-оценку качества без
запуска A/B-теста.

Подробное описание метрик находится в [`../../../docs/evaluation_metrics.md`](../../../docs/evaluation_metrics.md).

## Что делает модуль

```text
train period
→ построение рекомендаций
→ validation period
→ ground truth
→ offline metrics
→ scorecard / tracking
```

Модуль `evaluation` отвечает за несколько задач:

1. Разделить данные на обучающий и валидационный периоды.
2. Построить ground truth по будущим действиям пользователей.
3. Посчитать метрики качества рекомендаций.
4. Сохранить результат эксперимента в удобном виде.
5. Переиспользовать валидационные артефакты через локальный кэш.

## Основные файлы

| Файл                                                 | Что в нём находится                                      |
|------------------------------------------------------|----------------------------------------------------------|
| [`split.py`](split.py)                               | разбиение данных на train и validation по датам          |
| [`ground_truth.py`](ground_truth.py)                 | построение эталонных релевантных пар из будущих действий |
| [`metrics.py`](metrics.py)                           | расчёт offline-метрик                                    |
| [`scorecard.py`](scorecard.py)                       | объект с результатом эксперимента                        |
| [`tracking.py`](tracking.py)                         | сохранение JSON и CSV-индекса экспериментов              |
| [`validation_cache.py`](validation_cache.py)         | кэширование валидационных пар и ground truth             |
| [`validation_semantics.py`](validation_semantics.py) | фиксированные правила построения validation ground truth |

## Общая идея проверки

Мы не проверяем рекомендации на тех же данных, на которых они были построены.

Вместо этого используем временное разбиение:

```text
прошлые действия пользователей → строим рекомендации
будущие действия пользователей → проверяем, попали ли в них рекомендации
```

Например:

```text
2024-04-01 ... 2024-04-22 → train
2024-04-23                 → validation
```

Если для товара `A` мы рекомендовали товар `B`, а в валидационном периоде пользователи действительно взаимодействовали с
`A` и `B` в одном контексте, такая рекомендация считается попаданием.

## Разбиение по времени

### `TemporalSplitConfig`

В [`split.py`](split.py) описано разбиение на обучающую и валидационную части.

```python
from datetime import date

from ozon_similar_products.evaluation import (
    TemporalSplitConfig,
    split_train_validation,
)

split_config = TemporalSplitConfig(
    train_until_date=date(2024, 4, 22),
    validation_start_date=date(2024, 4, 23),
    validation_end_date=date(2024, 4, 23),
)

train_events, validation_events = split_train_validation(
    frame=events,
    config=split_config,
)
```

Обучающая часть содержит строки с датой меньше или равной `train_until_date`.

Валидационная часть содержит строки от `validation_start_date` до `validation_end_date` включительно.

Такой подход нужен, чтобы не подглядывать в будущее при построении рекомендаций.

## Ground truth

### `build_ground_truth_from_daily_pair_counts`

Ground truth — это таблица пар товаров, которые считаются релевантными на валидационном периоде.

Мы строим её не через полный повторный self-join сессий, а из компактных дневных счётчиков пар. Это быстрее и использует
ту же семантику пар, что и основной конвейер.

Пример:

```python
from ozon_similar_products.evaluation import build_ground_truth_from_daily_pair_counts

ground_truth = build_ground_truth_from_daily_pair_counts(
    daily_pair_counts=validation_pair_counts,
    relevance_mode="graded",
    action_weights={
        "view": 0.1,
        "click": 0.3,
        "favorite": 0.6,
        "to_cart": 1.0,
    },
    min_relevance=0.0,
)
```

На выходе получается таблица:

| Поле                 | Что означает                                         |
|----------------------|------------------------------------------------------|
| `item_id`            | исходный товар                                       |
| `relevant_item_id`   | товар, который считаем релевантным в будущем периоде |
| `relevance`          | сила релевантности                                   |
| `target_action_type` | самый сильный тип действия по этой паре              |
| `evidence_count`     | сколько раз пара встретилась                         |
| `view_count`         | число просмотров                                     |
| `click_count`        | число кликов                                         |
| `favorite_count`     | число добавлений в избранное                         |
| `to_cart_count`      | число добавлений в корзину                           |

Контракт ground truth описан в [`../../../docs/data_contract.md`](../../../docs/data_contract.md).

## Binary и graded relevance

Ground truth можно строить в двух режимах:

```text
binary
graded
```

В режиме `binary` любая найденная пара считается релевантной с весом `1.0`.

В режиме `graded` сила релевантности зависит от действий:

```yaml
view: 0.1
click: 0.3
favorite: 0.6
to_cart: 1.0
```

Так мы можем считать добавление в корзину более сильным подтверждением, чем просмотр.

## Почему validation semantics вынесены отдельно

В [`validation_semantics.py`](validation_semantics.py) фиксируются правила построения validation ground truth.

Это нужно для честного сравнения экспериментов. Например, параметры графа и дополнительные веса могут меняться в
train-части, но они не должны менять саму цель проверки.

Иначе эксперимент мог бы улучшить метрику не потому, что рекомендации стали лучше, а потому что изменился способ
построения ground truth.

## Метрики

### `compute_offline_metrics`

Функция `compute_offline_metrics` сравнивает рекомендации с ground truth и возвращает объект `OfflineMetrics`.

Пример:

```python
from ozon_similar_products.evaluation import compute_offline_metrics

metrics = compute_offline_metrics(
    recommendations=recommendations,
    ground_truth=ground_truth,
    top_k=20,
    context={
        "item_popularity": item_popularity,
        "popularity_column": "events_count",
    },
    ranking_relevant_action_types=["click", "favorite", "to_cart"],
    min_ranking_relevance=0.3,
)
```

Основные группы метрик:

| Метрика                | Что показывает                                              |
|------------------------|-------------------------------------------------------------|
| `hit_rate_at_k`        | долю товаров, у которых есть хотя бы одно попадание в top-K |
| `recall_at_k`          | какую долю релевантных будущих пар удалось покрыть          |
| `ndcg_at_k`            | насколько хорошо релевантные товары подняты вверх списка    |
| `mrr_at_k`             | насколько рано встречается первое попадание                 |
| `coverage_at_k`        | для какой доли оцениваемых товаров есть рекомендации        |
| `popularity_bias_at_k` | насколько выдача смещена в сторону популярных товаров       |
| `fallback_share_at_k`  | какая доля выдачи пришла из резервного слоя                 |

Дополнительно считаются метрики по отдельным действиям:

```text
view
click
favorite
to_cart
```

И отдельные метрики для резервных рекомендаций:

```text
fallback_hit_rate_at_k
fallback_recall_at_k
fallback_to_cart_hit_rate_at_k
fallback_to_cart_recall_at_k
```

Подробнее о смысле метрик: [`../../../docs/evaluation_metrics.md`](../../../docs/evaluation_metrics.md).

## Strong metrics

Для основных ranking-метрик мы не всегда хотим учитывать простые просмотры.

В настройках можно указать, какие действия считаются сильными для ранжирования:

```yaml
evaluation:
  ranking_relevant_action_types:
    - click
    - favorite
    - to_cart
  min_ranking_relevance: 0.3
```

Так основные метрики меньше зависят от слабых просмотров и лучше отражают качество рекомендаций по более сильным
действиям.

## Метрики резервного слоя

Резервный слой из [`business`](../business/README.md) добавляет рекомендации с `source`, отличным от `behavioral`.

В [`metrics.py`](metrics.py) отдельно считаются доли таких рекомендаций:

```text
fallback_share_at_k
fallback_category_type_share_at_k
fallback_category_share_at_k
fallback_type_share_at_k
fallback_brand_share_at_k
fallback_global_share_at_k
```

Это помогает понять, насколько итоговая выдача зависит от fallback.

Если `fallback_global_share_at_k` слишком высокий, значит в рекомендациях много глобально популярных товаров и мало
персонализированной поведенческой логики.

## Scorecard

### `EvaluationScorecard`

[`scorecard.py`](scorecard.py) хранит компактное описание эксперимента.

```python
from ozon_similar_products.evaluation import build_scorecard

scorecard = build_scorecard(
    experiment_id="run_2024_04_23",
    train_until_date="2024-04-22",
    lookback_days=7,
    top_k=20,
    metrics=metrics,
    notes="baseline run",
)
```

В scorecard попадают:

```text
experiment_id
train_until_date
lookback_days
top_k
metrics
notes
metadata
```

Это удобно для сохранения результатов запуска и сравнения экспериментов между собой.

## Tracking

В [`tracking.py`](tracking.py) находятся функции для сохранения результатов.

```python
from ozon_similar_products.evaluation.tracking import (
    append_experiment_index,
    metrics_to_flat_dict,
    write_json,
)

write_json(
    "outputs/evaluation/run_2024_04_23/scorecard.json",
    {"scorecard": scorecard},
)

append_experiment_index(
    "outputs/evaluation/index.csv",
    {
        "experiment_id": "run_2024_04_23",
        **metrics_to_flat_dict(metrics),
    },
)
```

`write_json` сохраняет JSON в стабильном формате.

`append_experiment_index` добавляет строку в CSV-индекс экспериментов.

## Кэш валидационных артефактов

Построение validation pair counts и ground truth может занимать время.

Чтобы не пересчитывать их при каждом подборе параметров, в [`validation_cache.py`](validation_cache.py) есть локальный
кэш.

Кэш сохраняет:

```text
validation_pair_counts.parquet
ground_truth.parquet
metadata.json
```

Ключ кэша строится по метаданным:

* валидационные даты;
* типы действий;
* настройки построения validation ground truth;
* входные файлы;
* конфиги данных и путей;
* версия схемы кэша;
* текущий git sha, если он доступен.

Если метаданные совпали и файлы кэша есть на диске, проект переиспользует готовые артефакты.

## Где находится в полном запуске

В полном сценарии оценка идёт после построения рекомендаций.

```text
train events
→ pipeline builds recommendations
→ validation events
→ validation pair counts
→ ground truth
→ offline metrics
→ scorecard / files
```

Обычно это запускается через:

```bash
uv run ozon-run-full 2024-04-23 --lookback-days 1 --validation-days 1 --top-k 20 --config-path configs/production.yaml
```

Команды запуска описаны в [`../../../scripts/README.md`](../../../scripts/README.md).

## Границы ответственности

Что делает `evaluation`:

* делит данные на train и validation;
* строит ground truth по будущим действиям;
* считает offline-метрики;
* считает метрики по fallback-источникам;
* считает смещение в сторону популярных товаров;
* формирует scorecard эксперимента;
* сохраняет JSON и CSV-индекс;
* кэширует валидационные артефакты.

Что не делает `evaluation`:

* не читает исходные архивы;
* не строит рекомендации;
* не меняет параметры модели;
* не выбирает лучшие параметры сам по себе;
* не сохраняет итоговую таблицу рекомендаций для serving.

Эти задачи находятся в других слоях:

| Задача                   | Модуль                                                                                  |
|--------------------------|-----------------------------------------------------------------------------------------|
| чтение данных            | [`data`](../data/README.md)                                                             |
| очистка событий и сессии | [`preprocessing`](../preprocessing/README.md)                                           |
| построение рекомендаций  | [`retrieval`](../retrieval/README.md)                                                   |
| резервные рекомендации   | [`business`](../business/README.md)                                                     |
| сохранение рекомендаций  | [`output`](../output/README.md)                                                         |
| подбор параметров        | [`cli/run_tune.py`](../cli/run_tune.py) и [`configs/tuning/`](../../../configs/tuning/) |

## Что менять осторожно

| Что менять                      | Почему осторожно                                 |
|---------------------------------|--------------------------------------------------|
| `relevance_weights`             | меняет смысл ground truth                        |
| `relevance_mode`                | меняет способ считать релевантность              |
| `ranking_relevant_action_types` | меняет набор действий для основных метрик        |
| `min_ranking_relevance`         | меняет, какие пары считаются достаточно сильными |
| validation dates                | меняют период проверки                           |
| `top_k`                         | влияет на все метрики `@k`                       |
| validation semantics            | может сделать сравнение экспериментов нечестным  |
| cache schema version            | инвалидирует старые кэши                         |

Если меняется логика ground truth, старые эксперименты и новые эксперименты нельзя напрямую сравнивать без пометки о
смене семантики.

## Быстрая проверка

Пример ручной проверки:

```python
from ozon_similar_products.evaluation import (
    build_ground_truth_from_daily_pair_counts,
    compute_offline_metrics,
)

ground_truth = build_ground_truth_from_daily_pair_counts(
    daily_pair_counts=validation_pair_counts,
    relevance_mode="graded",
    action_weights={
        "view": 0.1,
        "click": 0.3,
        "favorite": 0.6,
        "to_cart": 1.0,
    },
)

metrics = compute_offline_metrics(
    recommendations=recommendations,
    ground_truth=ground_truth,
    top_k=20,
    context={
        "item_popularity": item_popularity,
        "popularity_column": "events_count",
    },
)

print(metrics)
```

## Связанные документы

| Документ                                                                     | Что смотреть                                                  |
|------------------------------------------------------------------------------|---------------------------------------------------------------|
| [`../retrieval/README.md`](../retrieval/README.md)                           | как строятся рекомендации                                     |
| [`../business/README.md`](../business/README.md)                             | как появляются fallback-рекомендации                          |
| [`../pipeline/README.md`](../pipeline/README.md)                             | где оценка качества находится в полном запуске                |
| [`../../../configs/README.md`](../../../configs/README.md)                   | настройки оценки и подбора параметров                         |
| [`../../../scripts/README.md`](../../../scripts/README.md)                   | команды `ozon-run-full`, `ozon-run-tune`, `compare_tuning.py` |
| [`../../../docs/data_contract.md`](../../../docs/data_contract.md)           | контракты таблиц и колонок                                    |
| [`../../../docs/evaluation_metrics.md`](../../../docs/evaluation_metrics.md) | подробное описание метрик                                     |

## Коротко

Мы используем `evaluation`, чтобы проверить рекомендации на будущих действиях пользователей.

Сначала проект строит рекомендации на train-периоде.

Потом validation-период превращается в ground truth.

После этого мы сравниваем top-K рекомендаций с ground truth и сохраняем метрики эксперимента.
