# Архитектура проекта

Проект **OZON Similar Products** строит блок «Похожие товары» на основе пользовательского поведения.

Текущая архитектура — это пакетный offline-конвейер. Он берёт события пользователей за выбранное временное окно, очищает
их, строит сессии, находит пары товаров, считает оценку похожести, выбирает лучшие кандидаты и сохраняет готовый
результат.

## Общий путь данных

```text
сырые события пользователей
→ очистка событий
→ построение сессий
→ популярность товаров
→ пары товаров внутри сессий
→ агрегация пар за период
→ оценка похожести
→ выбор top-K кандидатов
→ резервные рекомендации
→ сохранение результата
→ чтение готовых рекомендаций
```

В терминах классов это выглядит так:

```text
raw user actions
→ EventCleaner
→ SessionBuilder
→ ItemPopularityBuilder
→ ItemPairBuilder
→ PairAggregator
→ CoVisitationScorer
→ TopKSelector
→ FallbackLayer
→ RecommendationWriter
→ SimilarItemsLookup
```

Главный принцип: каждый слой отвечает за свою часть работы и не забирает ответственность соседнего слоя.

## Зачем нужны отдельные слои

Проект специально не строит рекомендации одним большим скриптом.

Разделение на слои нужно, чтобы можно было отдельно проверить:

* как читаются данные;
* какие события остаются после очистки;
* как строятся пользовательские сессии;
* какие пары товаров появляются внутри сессий;
* как пары агрегируются за период;
* как считается итоговая оценка похожести;
* как добавляются резервные рекомендации;
* какие файлы сохраняются после запуска.

Если качество рекомендаций стало хуже, такое разделение помогает понять, где именно появилась проблема.

## Структура пакета

Основной код лежит в [`../src/ozon_similar_products/`](../src/ozon_similar_products/).

```text
src/ozon_similar_products/
  data/           # чтение данных, схемы, валидация
  preprocessing/  # очистка событий и построение сессий
  features/       # популярность товаров и вспомогательные статистики
  retrieval/      # пары товаров, агрегация, scoring, top-K
  business/       # резервные рекомендации и бизнес-правила
  evaluation/     # offline-метрики, scorecard и tracking
  output/         # сохранение результата и manifest
  serving/        # чтение готовых рекомендаций
  diagnostics/    # диагностика данных и повторяемые проверки
  pipeline/       # управление полным запуском
  cli/            # команды пакета
```

Документация по конкретным слоям лежит рядом с кодом:

* [`data/README.md`](../src/ozon_similar_products/data/README.md);
* [`preprocessing/README.md`](../src/ozon_similar_products/preprocessing/README.md);
* [`features/README.md`](../src/ozon_similar_products/features/README.md);
* [`retrieval/README.md`](../src/ozon_similar_products/retrieval/README.md);
* [`business/README.md`](../src/ozon_similar_products/business/README.md);
* [`evaluation/README.md`](../src/ozon_similar_products/evaluation/README.md);
* [`pipeline/README.md`](../src/ozon_similar_products/pipeline/README.md);
* [`output/README.md`](../src/ozon_similar_products/output/README.md);
* [`serving/README.md`](../src/ozon_similar_products/serving/README.md);
* [`diagnostics/README.md`](../src/ozon_similar_products/diagnostics/README.md).

## Слой данных

Модуль [`data`](../src/ozon_similar_products/data/README.md) отвечает за чтение подготовленных parquet-данных, схемы и
базовую валидацию.

Он не очищает события и не строит рекомендации.

Его задача — дать остальным слоям предсказуемые таблицы:

```text
product_information
user_actions
```

Основные обязанности:

* найти подготовленные parquet-файлы;
* прочитать события и товары;
* поддержать eager- и lazy-чтение через Polars;
* проверить наличие ожидаемых колонок;
* хранить общие схемы и контракты колонок.

## Предобработка

Модуль [`preprocessing`](../src/ozon_similar_products/preprocessing/README.md) отвечает за очистку событий и построение
пользовательских сессий.

```text
raw events
→ EventCleaner
→ clean events
→ SessionBuilder
→ sessions
```

`EventCleaner` оставляет только товарные действия с валидным `item_id`, нормализует время события и готовит таблицу для
дальнейшей обработки.

`SessionBuilder` группирует действия пользователя по времени. Если разрыв между действиями больше заданного таймаута,
начинается новая сессия.

На этом этапе не считается похожесть и не применяются финальные веса действий.

## Статистики

Модуль [`features`](../src/ozon_similar_products/features/README.md) считает статистики по очищенным событиям.

Главный объект здесь — `ItemPopularityBuilder`.

Он строит:

* популярность товаров;
* число уникальных пользователей по товарам;
* счётчики по типам действий;
* распределение действий для калибровки scoring.

Эти статистики используются дальше в [`retrieval`](../src/ozon_similar_products/retrieval/README.md), [
`business`](../src/ozon_similar_products/business/README.md), [
`evaluation`](../src/ozon_similar_products/evaluation/README.md) и диагностике.

## Построение похожих товаров

Модуль [`retrieval`](../src/ozon_similar_products/retrieval/README.md) отвечает за поведенческое ядро рекомендаций.

```text
sessions
→ ItemPairBuilder
→ PairAggregator
→ CoVisitationScorer
→ TopKSelector
```

### `ItemPairBuilder`

Строит направленные пары товаров внутри пользовательских сессий.

Если товары часто встречаются в одном пользовательском контексте, между ними появляется связь.

### `PairAggregator`

Объединяет дневные пары за выбранный период и считает статистики:

```text
pair_count
view_count
click_count
favorite_count
to_cart_count
unique_users
unique_sessions
```

### `CoVisitationScorer`

Считает итоговую оценку похожести.

До этого этапа проект сохраняет факты о поведении, но не превращает их в один финальный score.

### `TopKSelector`

Выбирает лучшие `top_k` похожих товаров для каждого `item_id`.

## Бизнес-правила и резервные рекомендации

Модуль [`business`](../src/ozon_similar_products/business/README.md) применяется после поведенческого top-K.

Главный слой здесь — `FallbackLayer`.

Он нужен для товаров, которым не хватило поведенческих данных:

* новый товар;
* редкий товар;
* слишком мало пар;
* после фильтров осталось меньше `top_k` рекомендаций.

Важно: `FallbackLayer` не должен встраиваться в `preprocessing` или `retrieval`.

Он работает как отдельный post-top-k слой:

```text
behavioral recommendations
→ fallback
→ final recommendations
```

Так проще отличить поведенческие рекомендации от резервных: источник сохраняется в поле `source`.

## Оценка качества

Модуль [`evaluation`](../src/ozon_similar_products/evaluation/README.md) проверяет рекомендации на будущих действиях
пользователей.

Общий принцип:

```text
train period → строим рекомендации
validation period → строим ground truth
recommendations + ground truth → offline metrics
```

Оценка качества не участвует в построении рекомендаций для production-выхода. Она нужна для экспериментов, сравнения
параметров и контроля качества.

Подробнее о метриках: [`evaluation_metrics.md`](evaluation_metrics.md).

## Сохранение результата

Модуль [`output`](../src/ozon_similar_products/output/README.md) сохраняет готовые рекомендации.

Основные файлы:

```text
detailed.parquet
enriched.parquet
lookup.parquet
manifest.json
```

`detailed.parquet` нужен для анализа и оценки.

`enriched.parquet` нужен для ручной проверки с названиями товаров.

`lookup.parquet` нужен для быстрого получения похожих товаров.

`manifest.json` связывает результат с параметрами запуска.

Контракты выходных таблиц описаны в [`data_contract.md`](data_contract.md).

## Чтение готовых рекомендаций

Модуль [`serving`](../src/ozon_similar_products/serving/README.md) читает уже сохранённый compact-результат и возвращает
похожие товары для конкретного `item_id`.

Главный класс:

```text
SimilarItemsLookup
```

Он не пересчитывает рекомендации. Он только открывает `lookup.parquet` или `manifest.json`, строит словарь в памяти и
отдаёт список похожих товаров.

Текущий serving-слой — это Python API, а не отдельный HTTP-сервис.

## Диагностика

Модуль [`diagnostics`](../src/ozon_similar_products/diagnostics/README.md) содержит функции для проверки данных и
промежуточных таблиц.

Он помогает посмотреть:

* схему таблицы;
* пропуски;
* распределение действий;
* размеры parquet-разделов;
* временные разрывы между событиями;
* примерную сессионность.

Диагностика не заменяет рабочие слои и не должна становиться скрытой частью production-конвейера.

Если проверка становится обязательной частью запуска, её лучше перенести в соответствующий модуль и покрыть тестами.

## Полный запуск

Модуль [`pipeline`](../src/ozon_similar_products/pipeline/README.md) связывает все слои в один сценарий.

Он отвечает за:

* загрузку конфигов;
* расчёт временного окна;
* вызов этапов в правильном порядке;
* запись промежуточных артефактов;
* incremental-режим;
* сохранение итогового результата;
* публикацию `outputs/latest/`.

Главная функция:

```text
run_pipeline(...)
```

Пользовательский вход обычно идёт через команду:

```bash
uv run ozon-run-pipeline 2024-04-23 --lookback-days 7 --top-k 20 --config-path configs/baseline.yaml
```

Полный сценарий с оценкой качества:

```bash
uv run ozon-run-full 2024-04-23 --lookback-days 1 --validation-days 1 --top-k 20 --config-path configs/production.yaml
```

## Единый слой конфигурации

Основной слой конфигурации находится здесь:

[`../src/ozon_similar_products/config.py`](../src/ozon_similar_products/config.py)

Через него доступны функции и объекты:

```text
PROJECT_ROOT
resolve_config_path
load_yaml_config
load_paths_config
load_data_config
load_configs
resolve_project_path
get_path_from_config
```

Конфиги хранятся в [`../configs/`](../configs/):

```text
configs/
  paths.yaml
  data.yaml
  baseline.yaml
  production.yaml
  evaluation.yaml
  tuning/
```

`data/config.py` сохранён как совместимая обёртка для старых импортов. Новую логику загрузки YAML туда добавлять не
нужно.

## Совместимые обёртки

В проекте есть несколько мест, которые оставлены для совместимости.

Например:

* [`../src/ozon_similar_products/data/config.py`](../src/ozon_similar_products/data/config.py);
* [`../src/ozon_similar_products/output/lookup.py`](../src/ozon_similar_products/output/lookup.py).

Их задача — не развивать новую логику, а поддерживать старые импорты в ноутбуках, тестах или пользовательском коде.

Канонический lookup для чтения готовых рекомендаций находится в [
`../src/ozon_similar_products/serving/lookup.py`](../src/ozon_similar_products/serving/lookup.py).

## Связанные документы

* [`docs/README.md`](README.md) — карта документации;
* [`data_contract.md`](data_contract.md) — контракты таблиц и границы ответственности;
* [`incremental_update.md`](incremental_update.md) — incremental-режим;
* [`evaluation_metrics.md`](evaluation_metrics.md) — offline-метрики качества;
* [`../scripts/README.md`](../scripts/README.md) — пользовательские команды запуска;
* [`../configs/README.md`](../configs/README.md) — настройки проекта.
