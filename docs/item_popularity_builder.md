# ItemPopularityBuilder

`ItemPopularityBuilder` — production-модуль для расчёта популярности товаров на основе очищенных пользовательских
событий `events_clean`.

Модуль относится к шагу 8 MVP pipeline: **«Посчитать популярность товаров»**. Популярность товара не является похожестью
товара и не заменяет co-visitation scoring. Она нужна как отдельный вспомогательный слой для диагностики данных, будущей
нормализации score против popularity bias и fallback-логики для редких товаров.

## Где находится код

```text
src/ozon_similar_products/features/item_popularity.py
```

Основной класс:

```python
ItemPopularityBuilder
```

## Входные данные

На вход методы builder-а принимают `events_clean` в формате Polars `DataFrame` или `LazyFrame`.

Ожидаемый clean-events contract:

```text
user_id
event_date
timestamp
action_type
item_id
search_query
widget_name
action_weight
```

Перед расчётом вход валидируется через:

```python
validate_clean_events(events_clean)
```

## Какие события участвуют в расчёте

В item popularity учитываются только прямые товарные действия:

```text
view
click
favorite
to_cart
```

По умолчанию они заданы в модуле как:

```python
DEFAULT_ITEM_ACTION_TYPES = ("view", "click", "favorite", "to_cart")
```

Не учитываются:

- строки без `item_id`;
- `search`;
- любые `action_type`, которые не входят в `item_action_types`.

Это важно, потому что search-событие может отражать пользовательский intent, но не должно увеличивать популярность
конкретного товара в MVP item popularity.

## Action weights

`ItemPopularityBuilder` не назначает веса действий самостоятельно. Он ожидает, что колонка `action_weight` уже добавлена
на этапе подготовки `events_clean`.

`weighted_events` считается как сумма `action_weight`:

```python
pl.col("action_weight").sum().alias("weighted_events")
```

Пример создания builder-а:

```python
from ozon_similar_products.features.item_popularity import ItemPopularityBuilder

builder = ItemPopularityBuilder()
```

Текущая реализация считает `weighted_events` как сумму уже подготовленной колонки `action_weight` из `events_clean`.

Словарь `action_weights` используется для проверки, что для каждого `item_action_type` задан вес. Если для какого-то
действия веса нет, builder падает с `ValueError`.

## Основной результат: item_popularity

Для построения основной таблицы используется:

```python
item_popularity = builder.build(events_clean)
```

или эквивалентный метод:

```python
item_popularity = builder.transform_day(events_clean)
```

`transform_day()` сохраняет интерфейс дневной трансформации.  
`build()` — более общий alias, который можно использовать и для одного дня, и для заранее подготовленного окна clean
events.

Основной выходной contract:

```text
item_id
events_count
unique_users
views_count
clicks_count
favorites_count
to_cart_count
weighted_events
```

После расчёта выход валидируется через:

```python
validate_item_popularity(item_popularity)
```

## Значение колонок

| Колонка           | Значение                                                           |
|-------------------|--------------------------------------------------------------------|
| `item_id`         | Идентификатор товара                                               |
| `events_count`    | Общее количество товарных действий по товару                       |
| `unique_users`    | Количество уникальных пользователей, взаимодействовавших с товаром |
| `views_count`     | Количество `view`-событий                                          |
| `clicks_count`    | Количество `click`-событий                                         |
| `favorites_count` | Количество `favorite`-событий                                      |
| `to_cart_count`   | Количество `to_cart`-событий                                       |
| `weighted_events` | Сумма `action_weight` по товарным событиям                         |

## Пример использования

```python
from ozon_similar_products.features.item_popularity import ItemPopularityBuilder

ACTION_WEIGHTS = {
    "view": 1.0,
    "click": 2.0,
    "favorite": 2.5,
    "to_cart": 4.0,
}

builder = ItemPopularityBuilder(action_weights=ACTION_WEIGHTS)

item_popularity = builder.build(events_clean)
```

Если `events_clean` содержит события за один день, результат будет popularity за день.  
Если `events_clean` содержит события за rolling window, результат будет popularity за всё это окно.

## Диагностические таблицы

Кроме основной таблицы `item_popularity`, builder умеет строить дополнительные диагностические разрезы.

### Popularity by date

```python
item_popularity_by_date = builder.build_by_date(events_clean)
```

Группировка:

```text
event_date
item_id
```

Выходные колонки:

```text
event_date
item_id
events_count
unique_users
weighted_events
views_count
clicks_count
favorites_count
to_cart_count
```

Эта таблица помогает смотреть динамику популярности товаров по дням.

### Popularity by action type

```python
item_popularity_by_action_type = builder.build_by_action_type(events_clean)
```

Группировка:

```text
item_id
action_type
```

Выходные колонки:

```text
item_id
action_type
events_count
unique_users
weighted_events
```

Эта таблица помогает понять, за счёт каких действий товар стал популярным: просмотров, кликов, добавлений в избранное
или добавлений в корзину.

Action-count колонки вроде `views_count` и `clicks_count` здесь намеренно не добавляются, потому что каждая строка уже
соответствует одному `action_type`.

### Popularity by widget name

```python
item_popularity_by_widget_name = builder.build_by_widget_name(events_clean)
```

Группировка:

```text
item_id
widget_name
```

Выходные колонки:

```text
item_id
widget_name
events_count
unique_users
weighted_events
views_count
clicks_count
favorites_count
to_cart_count
```

Эта таблица нужна для диагностики вклада разных интерфейсных контекстов. Например, она помогает понять, какие
`widget_name` дают больше всего событий и нет ли виджетов, которые создают шумный или перекошенный сигнал.

## Почему aggregate_window не реализован

В классе есть метод:

```python
aggregate_window(daily_popularity)
```

Он намеренно оставлен неимплементированным.

Причина: точный `unique_users` за окно нельзя восстановить из уже агрегированных дневных таблиц popularity.

Пример:

```text
day_1:
item_id=10, unique_users=1  # user_id=123

day_2:
item_id=10, unique_users=1  # тот же user_id=123
```

Если просто сложить дневные агрегаты, получится:

```text
unique_users = 2
```

Но правильный ответ за окно:

```text
unique_users = 1
```

Поэтому для rolling window нужно передавать clean events за всё окно напрямую:

```python
item_popularity_window = builder.build(events_clean_window)
```

а не агрегировать уже посчитанные дневные popularity-таблицы.

## Что модуль не делает

`ItemPopularityBuilder` не отвечает за:

- построение похожих товаров;
- построение item pairs;
- pair scoring;
- top-K selection;
- сохранение parquet-файлов;
- обновление `latest`;
- lookup рекомендаций.

Модуль только считает popularity-таблицы и возвращает `pl.DataFrame`.

## Что осталось вне текущей реализации

В текущей реализации не добавлены:

```text
category_popularity
brand_popularity
```

Для этих таблиц нужен дополнительный вход `product_information`, потому что в `events_clean` нет товарных полей:

```text
brand
type
category_id
category_name
```

## Тесты

Тесты находятся в файле:

```text
tests/test_item_popularity_builder.py
```

Они проверяют, что builder:

- считает основной `item_popularity` contract;
- игнорирует `search` и строки без `item_id`;
- считает `unique_users`, а не просто количество событий;
- возвращает валидный пустой результат;
- валидирует наличие action weights;
- принимает `LazyFrame`;
- осознанно не реализует `aggregate_window`;
- строит диагностики `by_date`, `by_action_type`, `by_widget_name`.

Запуск тестов:

```bash
uv run python -m pytest tests/test_item_popularity_builder.py
```

Полный запуск тестов проекта:

```bash
uv run python -m pytest
```
