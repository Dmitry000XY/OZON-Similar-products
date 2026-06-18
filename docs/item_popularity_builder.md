# ItemPopularityBuilder: популярность товаров и статистика для калибровки

`ItemPopularityBuilder` — production-модуль для расчёта фактической популярности товаров и статистик для будущей калибровки `CoVisitationScorer`.

Модуль относится к шагу 8 MVP pipeline: **«Посчитать популярность товаров»**.

Важно: популярность товара — это не похожесть товара. Популярность отвечает на вопрос:

```text
какие товары часто встречаются в поведении пользователей?
```

Похожесть отвечает на другой вопрос:

```text
какие товары часто оказываются рядом в одном пользовательском контексте?
```

Поэтому `ItemPopularityBuilder` не строит пары, не считает co-visitation score и не выбирает top-K. Он создаёт отдельный вспомогательный артефакт, который нужен для диагностики, fallback и калибровки весов в scoring-слое.

---

## 1. Где находится код

Файл:

```text
src/ozon_similar_products/features/item_popularity.py
```

Основной класс:

```python
ItemPopularityBuilder
```

Публичные методы называются по артефакту, который они строят:

```python
build_item_popularity(events_clean)
build_action_type_calibration_stats(events_clean, calibration_start, calibration_end)
build_item_popularity_by_date(events_clean)
build_item_popularity_by_action_type(events_clean)
build_item_popularity_by_widget_name(events_clean)
```

Такой нейминг лучше, чем абстрактные `build`, `transform_day` или `aggregate_window`, потому что сразу видно, какой результат вернёт метод.

---

## 2. Место в pipeline

Упрощённый поток:

```text
raw user_actions
→ EventCleaner
→ clean events
→ ItemPopularityBuilder
→ item popularity
→ calibration diagnostics
```

Параллельно clean events идут в session/pair pipeline:

```text
clean events
→ SessionBuilder
→ ItemPairBuilder
→ PairAggregator
→ CoVisitationScorer
```

`ItemPopularityBuilder` не является частью построения pair graph напрямую. Он даёт дополнительные факты:

- какие товары популярны;
- какие action_type доминируют в данных;
- какие доли `view`, `click`, `favorite`, `to_cart` использовать при калибровке scorer-а;
- какие товары можно использовать как fallback внутри категории/типа/бренда в будущих улучшениях.

---

## 3. Входной контракт

Все методы принимают `events_clean` в формате Polars `DataFrame` или `LazyFrame`.

Актуальный clean-events contract:

```text
user_id
event_date
timestamp
action_type
item_id
search_query
widget_name
```

Перед расчётом вход валидируется через:

```python
validate_clean_events(events_clean)
```

### Почему здесь нет `action_weight`

В новой multi-channel архитектуре `action_weight` не входит в обязательный контракт.

Мы договорились, что события **не взвешиваются до scorer-а**. До `CoVisitationScorer` мы сохраняем только факты: тип действия, товар, пользователя, дату, сессию и т.д.

Если бы `ItemPopularityBuilder` считал `weighted_events`, нам пришлось бы заранее решить, сколько весит `view`, `click`, `favorite`, `to_cart`. Это плохое место для такого решения, потому что:

- веса относятся к ранжированию пар, а не к popularity table;
- есть риск домножить один и тот же сигнал дважды;
- мы потеряем чистую статистику каналов;
- позже будет сложнее менять калибровку без пересчёта промежуточных данных.

Поэтому источник истины здесь — `action_type`, а не `action_weight`.

---

## 4. Какие события участвуют в расчёте

В item popularity учитываются только прямые товарные действия:

```text
view
click
favorite
to_cart
```

Не учитываются:

- строки без `item_id`;
- `search` без конкретного товара;
- любые `action_type`, которые не входят в `item_action_types`;
- служебные или неизвестные действия.

Почему search не учитывается как popularity товара: search показывает intent пользователя, но сам по себе не является взаимодействием с конкретным товаром. Search-сигналы можно использовать позже в отдельном co-search слое, но не нужно смешивать их с товарной популярностью MVP.

---

## 5. Основной метод: `build_item_popularity`

Использование:

```python
from ozon_similar_products.features.item_popularity import ItemPopularityBuilder

builder = ItemPopularityBuilder()
item_popularity = builder.build_item_popularity(events_clean)
```

Если `events_clean` содержит один день, результат будет популярностью за день.

Если `events_clean` содержит rolling window, результат будет популярностью за всё окно.

### Выходной контракт

```text
item_id
events_count
unique_users
views_count
clicks_count
favorites_count
to_cart_count
```

### Значение колонок

| Колонка | Значение |
|---|---|
| `item_id` | Идентификатор товара |
| `events_count` | Общее количество товарных действий по товару |
| `unique_users` | Количество уникальных пользователей, взаимодействовавших с товаром |
| `views_count` | Количество `view`-событий |
| `clicks_count` | Количество `click`-событий |
| `favorites_count` | Количество `favorite`-событий |
| `to_cart_count` | Количество `to_cart`-событий |

После расчёта выход валидируется через:

```python
validate_item_popularity(item_popularity)
```

---

## 6. Почему в результате нет `weighted_events`

Раньше можно было считать:

```text
weighted_events = sum(action_weight)
```

Но теперь это поле убрано из обязательного контракта.

Причина: `weighted_events` смешивает каналы раньше времени. Например, если у товара:

```text
1000 views
5 to_cart
```

одна сумма не показывает, откуда взялась популярность: из большого числа слабых просмотров или из малого числа сильных действий.

Для анализа нам полезнее видеть отдельные каналы:

```text
views_count
clicks_count
favorites_count
to_cart_count
```

А финальная интерпретация силы каналов происходит только в `CoVisitationScorer`.

---

## 7. Калибровочная статистика: `build_action_type_calibration_stats`

Метод:

```python
calibration_stats = builder.build_action_type_calibration_stats(
    events_clean,
    calibration_start="2026-04-01",
    calibration_end="2026-04-30",
)
```

Он строит отдельную таблицу долей action_type на calibration window.

### Зачем это нужно

В данных просмотров может быть намного больше, чем добавлений в корзину. Например:

```text
view:    80%
click:   12%
favorite: 4%
to_cart: 4%
```

Если поставить простые веса:

```text
view = 1
to_cart = 8
```

то массовые просмотры всё равно могут забить редкие, но более важные cart-сигналы.

Поэтому scorer использует мягкую частотную поправку:

```text
frequency_boost[action] = (reference_share / action_share) ^ beta
```

`ItemPopularityBuilder` не применяет эту формулу. Он только считает факты, которые scorer потом использует.

### Выходной контракт

```text
action_type
events_count
event_share
unique_users
unique_items
calibration_start
calibration_end
```

### Значение колонок

| Колонка | Значение |
|---|---|
| `action_type` | Тип действия: `view`, `click`, `favorite`, `to_cart` |
| `events_count` | Количество событий этого типа на calibration window |
| `event_share` | Доля событий этого типа среди всех товарных событий |
| `unique_users` | Сколько уникальных пользователей сделали это действие |
| `unique_items` | Сколько уникальных товаров получили это действие |
| `calibration_start` | Начало периода калибровки |
| `calibration_end` | Конец периода калибровки |

### Где используется результат

Результат можно сохранить в config/manifest как:

```yaml
calibration:
  action_shares_used_for_calibration:
    view: 0.80
    click: 0.12
    favorite: 0.04
    to_cart: 0.04
  calibration_start: "2026-04-01"
  calibration_end: "2026-04-30"
```

После этого `CoVisitationScorer` сможет воспроизводимо считать effective weights.

---

## 8. Диагностика по дням: `build_item_popularity_by_date`

Метод:

```python
item_popularity_by_date = builder.build_item_popularity_by_date(events_clean)
```

Группировка:

```text
event_date
item_id
```

Ожидаемые поля:

```text
event_date
item_id
events_count
unique_users
views_count
clicks_count
favorites_count
to_cart_count
```

Эта таблица нужна для анализа динамики:

- нет ли резких всплесков популярности товара;
- не случилась ли акция или технический выброс;
- не меняется ли структура action_type по дням;
- какие товары становятся популярными только на короткое время.

Важно: это диагностическая таблица, а не основной contract для downstream.

---

## 9. Диагностика по action_type: `build_item_popularity_by_action_type`

Метод:

```python
item_popularity_by_action_type = builder.build_item_popularity_by_action_type(events_clean)
```

Группировка:

```text
item_id
action_type
```

Ожидаемые поля:

```text
item_id
action_type
events_count
unique_users
```

Эта таблица нужна, чтобы понять, за счёт каких действий товар стал популярным.

Например, два товара могут иметь одинаковый `events_count`, но разную природу популярности:

```text
item A: 100 views, 0 carts
item B: 60 views, 10 carts
```

Для рекомендаций и manual review это разные ситуации.

---

## 10. Диагностика по widget_name: `build_item_popularity_by_widget_name`

Метод:

```python
item_popularity_by_widget_name = builder.build_item_popularity_by_widget_name(events_clean)
```

Группировка:

```text
item_id
widget_name
```

Ожидаемые поля:

```text
item_id
widget_name
events_count
unique_users
views_count
clicks_count
favorites_count
to_cart_count
```

Эта таблица помогает понять, откуда приходят действия:

- из поиска;
- из каталога;
- из карточки товара;
- из рекомендаций;
- из других виджетов.

Это важно, потому что просмотры из рекомендательной выдачи могут быть менее осознанным сигналом, чем добавление в корзину из карточки товара. В MVP `widget_name` не входит в score, но его нужно сохранить для EDA и будущих улучшений.

---

## 11. Почему нет `aggregate_window`

В `ItemPopularityBuilder` намеренно нет метода `aggregate_window(daily_popularity)`.

Причина: точный `unique_users` за окно нельзя восстановить из уже агрегированных дневных таблиц.

Пример:

```text
day_1:
item_id = 10, unique_users = 1  # user_id = 123

day_2:
item_id = 10, unique_users = 1  # тот же user_id = 123
```

Если сложить дневные агрегаты, получится:

```text
unique_users = 2
```

Правильный ответ за окно:

```text
unique_users = 1
```

Поэтому popularity за rolling window нужно строить из `events_clean` за всё окно:

```python
item_popularity_window = builder.build_item_popularity(events_clean_window)
```

А не из заранее агрегированных daily popularity tables.

---

## 12. Что модуль не делает

`ItemPopularityBuilder` не отвечает за:

- очистку raw events;
- построение сессий;
- построение item pairs;
- агрегацию pair graph;
- применение business weights;
- inverse-frequency normalization;
- расчёт co-visitation score;
- top-K selection;
- запись parquet-файлов;
- lookup рекомендаций.

Модуль только считает факты по товарам и action_type.

---

## 13. Что осталось на будущие улучшения

В текущей реализации не добавлены:

```text
category_popularity
brand_popularity
type_popularity
```

Для этих таблиц нужен дополнительный вход `product_information`, потому что в `events_clean` нет полей:

```text
brand
type
category_id
category_name
```

Эти артефакты пригодятся для fallback:

- популярные товары той же категории;
- популярные товары того же типа;
- популярные товары того же бренда;
- fallback для холодных/редких товаров.

Но их лучше добавлять отдельным шагом, чтобы не смешивать базовый popularity builder с metadata fallback.

---

## 14. Тесты

Тесты находятся в файле:

```text
tests/test_item_popularity_builder.py
```

Они должны проверять, что builder:

- считает основной `item_popularity` contract;
- не требует `action_weight`;
- не создаёт `weighted_events`;
- игнорирует `search` и строки без `item_id`;
- считает `unique_users`, а не просто количество событий;
- строит `action_type_calibration_stats`;
- считает `event_share`;
- строит диагностику by date, by action type, by widget name;
- принимает `DataFrame` и `LazyFrame`;
- не содержит фиктивного `aggregate_window` с `NotImplementedError`.

Запуск тестов:

Если терминал открыт в папке `docs/`, перейдите в корень проекта
относительным путём:

```bash
cd ..
```

```bash
uv run pytest tests/test_item_popularity_builder.py
```

Полный запуск тестов проекта:

```bash
uv run pytest
```

---

## 15. Короткий итог

`ItemPopularityBuilder` в новой архитектуре — это не scorer и не weighted popularity. Это модуль, который считает чистые факты:

```text
сколько было событий,
сколько пользователей,
сколько view/click/favorite/to_cart,
какие доли action_type на calibration window.
```

Все веса и калибровка появляются позже, только в `CoVisitationScorer`.
