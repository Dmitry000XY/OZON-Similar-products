# Сохранение результатов

В этом модуле мы сохраняем готовые рекомендации после построения.

К моменту, когда данные попадают в `output`, рекомендации уже посчитаны: пары товаров построены, `score` рассчитан,
top-K выбран, резервные рекомендации добавлены. Задача этого слоя — не менять логику рекомендаций, а аккуратно сохранить
результат в форматах, которые нужны для анализа, проверки и дальнейшего использования.

## Что делает модуль

```text
recommendations
+ product_information
+ manifest metadata
→ detailed.parquet
→ enriched.parquet
→ lookup.parquet
→ manifest.json
```

Главный класс модуля:

```python
RecommendationWriter
```

Он отвечает за сохранение рекомендаций и манифеста запуска.

## Основные файлы

| Файл          | Что в нём находится                                                      |
|---------------|--------------------------------------------------------------------------|
| `writers.py`  | сохранение рекомендаций в подробном, обогащённом и compact-формате       |
| `manifest.py` | чтение манифеста, поиск путей к артефактам и перенос относительных путей |

## Какие файлы сохраняем

После обычного запуска конвейера в папке run появляются файлы:

```text
outputs/
  runs/
    <run_id>/
      recommendations/
        detailed.parquet
        enriched.parquet
        lookup.parquet
      manifest.json
```

Эти три parquet-файла решают разные задачи.

| Файл               | Для чего нужен                                                                         |
|--------------------|----------------------------------------------------------------------------------------|
| `detailed.parquet` | основной подробный результат с `item_id`, `similar_item_id`, `score`, `rank`, `source` |
| `enriched.parquet` | человекочитаемая версия с названиями исходного и похожего товара                       |
| `lookup.parquet`   | compact-формат для быстрого получения списка похожих товаров                           |
| `manifest.json`    | описание запуска, путей, параметров и количества строк                                 |

## `detailed.parquet`

`detailed.parquet` — это основной результат построения рекомендаций.

Он сохраняется методом:

```python
writer.save_detailed(recommendations, output_path)
```

В нём остаётся табличная структура:

```text
item_id
similar_item_id
score
rank
source
```

А также могут сохраняться диагностические колонки, если они были добавлены раньше в конвейере.

Этот файл удобен для:

* проверки качества ранжирования;
* анализа `score`;
* сравнения источников рекомендаций;
* отладки fallback;
* ручной диагностики конкретных товаров.

Пример:

```text
item_id | similar_item_id | score | rank | source
100     | 205             | 8.42  | 1    | behavioral
100     | 317             | 6.10  | 2    | behavioral
100     | 918             | 0.00  | 3    | fallback_category_popular
```

## `enriched.parquet`

`enriched.parquet` — это версия результата с названиями товаров.

Он сохраняется методом:

```python
writer.save_enriched(
    recommendations,
    products,
    output_path,
)
```

Для этого writer берёт из `product_information` колонки:

```text
item_id
name
```

И добавляет названия для исходного товара и похожего товара.

На выходе получается таблица:

```text
item_id
item_name
similar_item_id
similar_item_name
rank
score
source
```

Этот файл нужен не столько для автоматического использования, сколько для чтения человеком.

Через него удобно быстро ответить на вопросы:

* какие товары рекомендуются для конкретного товара;
* выглядят ли рекомендации разумно;
* не попали ли в выдачу очевидно нерелевантные товары;
* насколько часто появляются fallback-рекомендации.

## `lookup.parquet`

`lookup.parquet` — compact-формат для быстрого получения похожих товаров.

Он сохраняется методом:

```python
writer.save_widget_format(recommendations, output_path)
```

Внутри writer сначала сортирует рекомендации по:

```text
item_id
rank
similar_item_id
```

А потом собирает для каждого `item_id` список похожих товаров.

Формат результата:

```text
item_id
similar_items_sku_list
```

Пример:

```text
item_id | similar_items_sku_list
100     | [205, 317, 918, 441]
101     | [778, 120, 334, 902]
```

Именно этот формат дальше удобнее всего использовать в serving-слое, потому что для одного товара уже есть готовый
список похожих товаров.

## `manifest.json`

`manifest.json` описывает запуск и сохранённые файлы.

Он сохраняется методом:

```python
writer.save_manifest(manifest, output_path)
```

В манифесте обычно лежат:

```text
run_id
generated_at
train_until_date
lookback_days
window_start
window_end
score_method
top_k
calibration_used
fallback_enabled
paths
rows
```

Главная задача манифеста — связать результат с параметрами запуска.

По нему можно понять:

* когда был построен результат;
* на каком временном окне;
* каким методом считался score;
* какой `top_k` использовался;
* был ли включён fallback;
* где лежат parquet-файлы;
* сколько строк получилось на этапах конвейера.

## `outputs/latest`

Кроме конкретного run, проект может публиковать последнюю версию результата:

```text
outputs/
  latest/
    recommendations/
      detailed.parquet
      enriched.parquet
      lookup.parquet
    manifest.json
```

Это нужно, чтобы serving-слой и ручной просмотр не зависели от знания конкретного `run_id`.

Например, вместо пути:

```text
outputs/runs/run_2024-04-23_lb7/recommendations/lookup.parquet
```

можно читать:

```text
outputs/latest/recommendations/lookup.parquet
```

## Как работает latest-манифест

Когда манифест конкретного run копируется в `outputs/latest`, относительные пути внутри него должны остаться
корректными.

Для этого в `manifest.py` есть helper `rebase_manifest_paths`.

Он переносит известные пути к recommendation-артефактам так, чтобы они были валидны относительно новой папки.

Это важно для `SimilarItemsLookup`: он может открыть `outputs/latest/manifest.json`, найти путь к compact-файлу и
прочитать `lookup.parquet`.

## Поиск compact-файла в манифесте

В проекте исторически использовались разные названия поля для compact-выхода.

Поэтому `manifest.py` поддерживает несколько ключей:

```text
widget_recommendations_path
compact_recommendations_path
similar_items_path
widget_path
lookup_recommendations_path
lookup_path
recommendations_path
```

Функция `find_compact_recommendations_path` ищет путь по этим ключам и в верхнем уровне манифеста, и внутри блока
`paths`.

Это делает чтение результата устойчивее к переименованиям.

## Пример использования writer

```python
from ozon_similar_products.output.writers import RecommendationWriter

writer = RecommendationWriter()

writer.save_detailed(
    recommendations,
    "outputs/runs/run_2024-04-23_lb7/recommendations/detailed.parquet",
)

writer.save_enriched(
    recommendations,
    products,
    "outputs/runs/run_2024-04-23_lb7/recommendations/enriched.parquet",
)

writer.save_widget_format(
    recommendations,
    "outputs/runs/run_2024-04-23_lb7/recommendations/lookup.parquet",
)

writer.save_manifest(
    manifest,
    "outputs/runs/run_2024-04-23_lb7/manifest.json",
)
```

Если вместо файла передать директорию, writer сам добавит стандартное имя файла:

```python
writer.save_detailed(recommendations, "outputs/example/recommendations/")
```

В этом случае файл будет сохранён как:

```text
outputs/example/recommendations/detailed.parquet
```

## Проверки перед сохранением

Перед записью writer проверяет контракты таблиц.

Для `detailed.parquet` проверяется контракт рекомендаций.

Для `enriched.parquet` дополнительно проверяется, что в `product_information` есть:

```text
item_id
name
```

Для `lookup.parquet` проверяется compact-контракт:

```text
item_id
similar_items_sku_list
```

Это помогает поймать ошибку до того, как некорректный файл попадёт в `outputs/latest`.

## Где находится в конвейере

`output` используется в конце полного запуска.

```text
retrieval
→ business
→ final recommendations
→ output
→ serving
```

Сначала `retrieval` и `business` формируют итоговую таблицу рекомендаций.

Потом `output` сохраняет её в нескольких форматах.

После этого `serving` может читать `lookup.parquet` и отдавать похожие товары для конкретного `item_id`.

## Границы ответственности

Что делает `output`:

* сохраняет подробную таблицу рекомендаций;
* добавляет названия товаров для человекочитаемой версии;
* собирает compact-таблицу для lookup;
* сохраняет `manifest.json`;
* помогает читать и переносить пути внутри манифеста;
* готовит результат к использованию в serving-слое.

Что не делает `output`:

* не читает сырые события;
* не строит сессии;
* не считает пары товаров;
* не рассчитывает `score`;
* не выбирает top-K;
* не добавляет fallback;
* не отвечает на запросы пользователя по `item_id`.

Эти задачи находятся в других слоях:

| Задача                      | Модуль      |
|-----------------------------|-------------|
| построение рекомендаций     | `retrieval` |
| резервные рекомендации      | `business`  |
| полный запуск               | `pipeline`  |
| чтение готовых рекомендаций | `serving`   |

## Что менять осторожно

| Что менять                                                            | Почему осторожно                                    |
|-----------------------------------------------------------------------|-----------------------------------------------------|
| имена файлов `detailed.parquet`, `enriched.parquet`, `lookup.parquet` | на них опираются pipeline, latest и serving         |
| название колонки `similar_items_sku_list`                             | это публичный compact-контракт                      |
| структуру `manifest.json`                                             | по ней downstream-код находит артефакты             |
| список ключей compact-path в манифесте                                | влияет на обратную совместимость                    |
| формат `enriched.parquet`                                             | его используют для ручной проверки результата       |
| логику `update_latest_manifest`                                       | можно сломать относительные пути в `outputs/latest` |

Если меняется `lookup.parquet` или ключи путей в манифесте, нужно проверить `serving`.

Если меняется `detailed.parquet`, нужно проверить `evaluation`, потому что метрики читают рекомендации в подробном
формате.

## Быстрая проверка

После запуска конвейера можно проверить, что файлы появились:

```text
outputs/latest/recommendations/detailed.parquet
outputs/latest/recommendations/enriched.parquet
outputs/latest/recommendations/lookup.parquet
outputs/latest/manifest.json
```

Посмотреть последние рекомендации:

```bash
uv run python scripts/preview_latest_recommendations.py
```

Или прочитать compact-файл вручную:

```python
import polars as pl

lookup = pl.read_parquet("outputs/latest/recommendations/lookup.parquet")
print(lookup.head())
```

## Связанные документы

| Документ                         | Что смотреть                                           |
|----------------------------------|--------------------------------------------------------|
| `../pipeline/README.md`          | где output используется в полном запуске               |
| `../retrieval/README.md`         | как строится поведенческая выдача                      |
| `../business/README.md`          | как добавляются fallback-рекомендации                  |
| `../serving/README.md`           | как читать готовый `lookup.parquet`                    |
| `../evaluation/README.md`        | как проверять подробные рекомендации                   |
| `../../../configs/README.md`     | где задаются `outputs.root_dir` и `outputs.latest_dir` |
| `../../../docs/data_contract.md` | контракты итоговых таблиц                              |

## Коротко

Мы используем `output`, чтобы сохранить готовые рекомендации в нескольких форматах.

`detailed.parquet` нужен для анализа и оценки.

`enriched.parquet` нужен для ручной проверки с названиями товаров.

`lookup.parquet` нужен для быстрого получения похожих товаров.

`manifest.json` связывает файлы результата с параметрами запуска.
