# Получение готовых рекомендаций

В этом модуле читаются уже построенные рекомендации и возвращается список похожих товаров для конкретного `item_id`.

К этому моменту конвейер уже всё посчитал: собрал события, построил пары товаров, рассчитал `score`, выбрал top-K,
добавил резервные рекомендации и сохранил результат. Модуль `serving` не пересчитывает рекомендации. Он только открывает
готовый compact-файл и отдаёт похожие товары.

## Что делает модуль

```text
outputs/latest/manifest.json
или
outputs/latest/recommendations/lookup.parquet
→ SimilarItemsLookup
→ get_similar_items(item_id)
→ список похожих товаров
```

Главный класс модуля:

```python
SimilarItemsLookup
```

Он загружает compact-рекомендации и строит быстрый lookup:

```text
item_id → similar_items_sku_list
```

## Основные файлы

| Файл                         | Что в нём находится                                     |
|------------------------------|---------------------------------------------------------|
| [`__init__.py`](__init__.py) | публичный импорт `SimilarItemsLookup`                   |
| [`lookup.py`](lookup.py)     | чтение compact-рекомендаций и получение похожих товаров |

## Какой файл читает serving

Serving-слой работает с compact-форматом рекомендаций.

Обычно это файл:

```text
outputs/latest/recommendations/lookup.parquet
```

Формат таблицы:

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

Этот формат создаётся в модуле [`output`](../output/README.md) из подробной таблицы рекомендаций.

## Почему используется `lookup.parquet`

В подробном формате каждая рекомендация хранится отдельной строкой:

```text
item_id | similar_item_id | score | rank | source
```

Для анализа это удобно, но для быстрого получения похожих товаров не очень.

Serving-слою нужен уже собранный список:

```text
item_id | similar_items_sku_list
```

Так для одного товара можно сразу вернуть массив похожих товаров без группировки и сортировки на каждый запрос.

## Использование через manifest

Самый удобный вариант — читать рекомендации через `manifest.json`.

```python
from ozon_similar_products.serving import SimilarItemsLookup

lookup = SimilarItemsLookup("outputs/latest/manifest.json")

similar_items = lookup.get_similar_items(100, top_k=10)
print(similar_items)
```

В этом случае `SimilarItemsLookup` сам найдёт путь к compact-файлу внутри манифеста.

Такой способ лучше, чем вручную указывать parquet-файл, потому что путь к рекомендациям берётся из метаданных последнего
запуска.

## Использование через parquet-файл

Можно передать путь к `lookup.parquet` напрямую:

```python
from ozon_similar_products.serving import SimilarItemsLookup

lookup = SimilarItemsLookup("outputs/latest/recommendations/lookup.parquet")

similar_items = lookup.get_similar_items(100, top_k=10)
print(similar_items)
```

Можно также передать директорию, где лежит `lookup.parquet`:

```python
lookup = SimilarItemsLookup("outputs/latest/recommendations")
```

В этом случае будет использован файл:

```text
outputs/latest/recommendations/lookup.parquet
```

## Что возвращает `get_similar_items`

Метод:

```python
get_similar_items(item_id, top_k=10)
```

возвращает список `item_id` похожих товаров.

Пример:

```python
lookup.get_similar_items(100, top_k=3)
```

Результат:

```python
[205, 317, 918]
```

Если для товара нет рекомендаций, метод возвращает пустой список:

```python
[]
```

Если `top_k <= 0`, метод выбрасывает ошибку, потому что размер выдачи должен быть положительным числом.

## Что происходит при загрузке

При создании `SimilarItemsLookup` модуль:

1. Определяет, что ему передали: manifest, parquet-файл или директорию.
2. Если передан manifest, читает из него путь к compact-рекомендациям.
3. Загружает parquet-файл через Polars.
4. Проверяет, что таблица соответствует compact-контракту.
5. Строит словарь `item_id → similar_items_sku_list`.
6. Убирает `None` из списков похожих товаров.

После этого запросы к `get_similar_items` идут уже по словарю в памяти.

## Какие ключи manifest поддерживаются

В манифесте путь к compact-рекомендациям может называться по-разному.

Serving использует helper из [`output.manifest`](../output/manifest.py), который ищет путь по нескольким ключам:

```text
widget_recommendations_path
compact_recommendations_path
similar_items_path
widget_path
lookup_recommendations_path
lookup_path
recommendations_path
```

Это нужно для обратной совместимости: если название поля в манифесте менялось, serving всё равно сможет найти нужный
файл.

## Где находится в проекте

Serving-слой стоит после [`output`](../output/README.md).

```text
pipeline
→ output
→ serving
```

[`pipeline`](../pipeline/README.md) строит рекомендации.

[`output`](../output/README.md) сохраняет их в `lookup.parquet`.

`serving` читает `lookup.parquet` и отдаёт похожие товары для конкретного товара.

## Чем serving сейчас не является

В текущей версии `serving` — это не отдельный HTTP-сервис.

Здесь нет API-сервера, роутов, авторизации, кэша в Redis или онлайн-обновления модели.

Сейчас это лёгкий Python-слой для локального чтения готового результата. Его можно использовать:

* в скриптах;
* в ноутбуках;
* в проверках;
* как основу для будущего сервиса.

Если проекту понадобится реальный online-serving, его можно будет строить поверх `SimilarItemsLookup` или заменить этот
слой более производительным хранилищем.

## Границы ответственности

Что делает `serving`:

* читает `manifest.json` или `lookup.parquet`;
* находит compact-файл рекомендаций;
* проверяет формат `item_id | similar_items_sku_list`;
* строит lookup-словарь;
* возвращает top-K похожих товаров для `item_id`.

Что не делает `serving`:

* не читает сырые события;
* не строит сессии;
* не считает пары товаров;
* не рассчитывает `score`;
* не выбирает top-K из подробной таблицы;
* не добавляет fallback;
* не сохраняет результат;
* не поднимает web-сервис.

Эти задачи находятся в других слоях:

| Задача                  | Модуль                                  |
|-------------------------|-----------------------------------------|
| построение рекомендаций | [`retrieval`](../retrieval/README.md)   |
| резервные рекомендации  | [`business`](../business/README.md)     |
| полный запуск           | [`pipeline`](../pipeline/README.md)     |
| сохранение результата   | [`output`](../output/README.md)         |
| проверка качества       | [`evaluation`](../evaluation/README.md) |

## Что менять осторожно

| Что менять                          | Почему осторожно                                              |
|-------------------------------------|---------------------------------------------------------------|
| название `lookup.parquet`           | его ожидают `output`, `pipeline` и `serving`                  |
| колонку `similar_items_sku_list`    | это compact-контракт результата                               |
| поиск пути в `manifest.json`        | можно сломать чтение `outputs/latest/manifest.json`           |
| поведение при неизвестном `item_id` | сейчас это безопасный пустой список                           |
| загрузку файла в память             | для очень большого каталога может потребоваться другой подход |

Если меняется compact-формат, нужно обновить [`output`](../output/README.md), `serving`, тесты и документацию по
контрактам.

## Быстрая проверка

После запуска конвейера:

```bash
uv run ozon-run-pipeline 2024-04-23 --lookback-days 7 --top-k 20 --config-path configs/baseline.yaml
```

можно проверить serving так:

```python
from ozon_similar_products.serving import SimilarItemsLookup

lookup = SimilarItemsLookup("outputs/latest/manifest.json")

print(lookup.get_similar_items(100, top_k=10))
```

Или посмотреть последние рекомендации через готовую команду:

```bash
uv run ozon-preview-recommendations
```

## Связанные документы

| Документ                                                           | Что смотреть                             |
|--------------------------------------------------------------------|------------------------------------------|
| [`../output/README.md`](../output/README.md)                       | как создаётся `lookup.parquet`           |
| [`../pipeline/README.md`](../pipeline/README.md)                   | как публикуется `outputs/latest/`        |
| [`../retrieval/README.md`](../retrieval/README.md)                 | как строятся поведенческие рекомендации  |
| [`../business/README.md`](../business/README.md)                   | как добавляются fallback-рекомендации    |
| [`../../../docs/data_contract.md`](../../../docs/data_contract.md) | контракт compact-таблицы                 |
| [`../../../scripts/README.md`](../../../scripts/README.md)         | команда просмотра последних рекомендаций |

## Коротко

Мы используем `serving`, чтобы быстро получить похожие товары из уже сохранённого результата.

Он читает `manifest.json` или `lookup.parquet`, строит словарь в памяти и возвращает список похожих товаров по
`item_id`.

Вся тяжёлая работа — построение рекомендаций, scoring и fallback — происходит раньше, в `pipeline`, `retrieval` и
`business`.
