# PairAggregator и `aggregate_pairs.py`: агрегация multi-channel пар

Файл:

```text
src/ozon_similar_products/retrieval/aggregate_pairs.py
```

Модуль реализует `PairAggregator` — слой, который агрегирует дневные пары товаров за rolling window.

Главное правило: **PairAggregator не применяет веса и не считает score**.


Он только считает фактические статистики по парам и каналам поведения:

```text
view_count
click_count
favorite_count
to_cart_count
unique_users
unique_sessions
```

Это принципиально важно для новой multi-channel архитектуры: до `CoVisitationScorer` мы не хотим превращать разные типы событий в одно число.

---

## 1. Место в pipeline

Поток данных:

```text
sessions
→ ItemPairBuilder
→ daily item pairs
→ PairAggregator
→ pair aggregates
→ CoVisitationScorer
→ pair scores
→ TopKSelector
```

`ItemPairBuilder` создаёт directed pairs внутри сессий и сохраняет `signal_type`.

`PairAggregator` объединяет эти pair events за окно дат и считает отдельные статистики по каналам.

`CoVisitationScorer` потом решает, как взвесить эти каналы и превратить их в итоговый `score`.

---

## 2. Зачем нужен отдельный агрегатор

Дневные пары товаров слишком шумные. В один день может быть:

- акция;
- сезонный всплеск;
- технический шум;
- необычная активность одного пользователя;
- мало данных по редким товарам;
- случайная длинная сессия.

Поэтому рекомендации строятся не по одному дню, а по rolling window, например за последние 30 дней.

`PairAggregator` превращает много дневных pair events в устойчивую статистику связи между двумя товарами.

Пример:

```text
A → B встретилась 1 раз в одной сессии
```

Это слабый сигнал.

```text
A → B встретилась 80 раз у 50 пользователей в 70 сессиях
```

Это уже сильная поведенческая связь.

---

## 3. Входной контракт

`PairAggregator.aggregate_window(...)` принимает список таблиц `daily_item_pairs`.

Каждая таблица должна иметь контракт:

```text
pair_date
item_id
similar_item_id
session_id
user_id
source_action_type
target_action_type
signal_type
```

Смысл полей:

| Поле | Значение |
|---|---|
| `pair_date` | Дата pair event |
| `item_id` | Исходный товар |
| `similar_item_id` | Товар-кандидат |
| `session_id` | Сессия, где возникла связь |
| `user_id` | Пользователь, создавший связь |
| `source_action_type` | Strongest action исходного товара внутри сессии |
| `target_action_type` | Strongest action кандидата внутри сессии |
| `signal_type` | Канал пары; сейчас равен `target_action_type` |

Пример:

```text
session:
A: view
B: to_cart
C: view

pairs:
A → B: signal_type = to_cart
C → B: signal_type = to_cart
B → A: signal_type = view
B → C: signal_type = view
```

Почему `signal_type` берётся от target item: если мы строим рекомендацию `A → B`, то для качества кандидата особенно важно, что пользователь сделал с `B`. Если `B` добавили в корзину, это сильный сигнал для рекомендации `B` рядом с `A`.

---

## 4. Выходной контракт

`PairAggregator` возвращает `pair_aggregates`:

```text
item_id
similar_item_id
pair_count
view_count
click_count
favorite_count
to_cart_count
unique_users
unique_sessions
window_start
window_end
```

Смысл полей:

| Поле | Значение |
|---|---|
| `item_id` | Исходный товар |
| `similar_item_id` | Товар-кандидат |
| `pair_count` | Сколько раз пара встретилась в rolling window |
| `view_count` | Сколько pair events пришли из `signal_type = view` |
| `click_count` | Сколько pair events пришли из `signal_type = click` |
| `favorite_count` | Сколько pair events пришли из `signal_type = favorite` |
| `to_cart_count` | Сколько pair events пришли из `signal_type = to_cart` |
| `unique_users` | Сколько уникальных пользователей создали пару |
| `unique_sessions` | Сколько уникальных `(user_id, session_id)` создали пару |
| `window_start` | Начало rolling window |
| `window_end` | Конец rolling window |

Главный инвариант:

```text
pair_count = view_count + click_count + favorite_count + to_cart_count
```

---

## 5. Почему нет `weight_sum`

В старой weighted-схеме можно было сделать так:

```text
pair_weight
→ weight_sum
→ score
```

Но для текущей задачи это слишком раннее сжатие данных.

После `weight_sum` мы уже не знаем, почему пара стала сильной:

```text
100 views?
10 clicks?
2 favorites?
1 to_cart?
```

А именно это важно для калибровки. Добавление в корзину должно быть сильнее просмотра, но просмотров может быть в десятки раз больше. Поэтому нам нельзя просто смешать всё до scorer-а.

Правильная схема:

```text
PairAggregator:
  считает view_count, click_count, favorite_count, to_cart_count

CoVisitationScorer:
  применяет business_weights, frequency normalization, beta и считает score
```

Итог: `weight_sum` не входит в обязательный контракт `pair_aggregates`.

---

## 6. Что делает `aggregate_window`

Метод:

```python
from ozon_similar_products.retrieval.aggregate_pairs import PairAggregator

pair_aggregates = PairAggregator().aggregate_window(
    daily_pairs=[pairs_day_1, pairs_day_2, pairs_day_3],
    window_start="2026-04-01",
    window_end="2026-04-30",
)
```

Логика метода:

1. Проверяет, что `window_start` и `window_end` — ISO-даты формата `YYYY-MM-DD`.
2. Проверяет, что `window_start <= window_end`.
3. Если список `daily_pairs` пустой, возвращает пустую таблицу с колонками из `schemas.PAIR_AGGREGATES_COLUMNS`.
4. Валидирует каждую таблицу daily pairs через `validate_daily_item_pairs`.
5. Объединяет входные таблицы через `pl.concat`.
6. Приводит `pair_date` к типу `Date`.
7. Фильтрует строки по inclusive window:

```text
window_start <= pair_date <= window_end
```

8. Группирует по:

```text
item_id
similar_item_id
```

9. Считает:

```text
pair_count
view_count
click_count
favorite_count
to_cart_count
unique_users
unique_sessions
```

10. Добавляет `window_start` и `window_end`.
11. Выбирает колонки в порядке `schemas.PAIR_AGGREGATES_COLUMNS`.
12. Сортирует результат по `item_id`, `similar_item_id`.
13. Валидирует выход через `validate_pair_aggregates`.

---

## 7. Почему пустой результат строится по колонкам из `schemas`, а не через локальный `_AGGREGATE_SCHEMA`

Не нужно держать в `aggregate_pairs.py` отдельный словарь вида:

```python
_AGGREGATE_SCHEMA = {
    "item_id": pl.Int64,
    "similar_item_id": pl.Int64,
    ...
}
```

Это дублирует контракт, который уже описан в `src/ozon_similar_products/data/schemas.py`:

```python
PAIR_AGGREGATES_COLUMNS = [
    "item_id",
    "similar_item_id",
    "pair_count",
    "view_count",
    "click_count",
    "favorite_count",
    "to_cart_count",
    "unique_users",
    "unique_sessions",
    "window_start",
    "window_end",
]
```

Если мы будем хранить локальные typed schemas в каждом модуле, появится риск рассинхронизации:

```text
schemas.py изменили,
aggregate_pairs.py забыли обновить,
тесты стали неочевидно падать или, хуже, pipeline начал отдавать разные контракты.
```

Поэтому пустой результат создаётся через общий helper:

```python
from ozon_similar_products.data.frames import empty_contract_frame
from ozon_similar_products.data import schemas

empty = empty_contract_frame(schemas.PAIR_AGGREGATES_COLUMNS)
```

Так модуль знает только канонический список колонок из `schemas.py`. Если позже потребуется строгая dtype-валидация, её лучше добавить централизованно, а не размазывать по файлам.

---

## 8. Что PairAggregator не должен делать

PairAggregator не должен:

- применять `business_weights`;
- применять inverse-frequency normalization;
- использовать `beta`;
- считать `score`;
- выбирать top-K;
- создавать `pair_weight`;
- создавать `weight_sum`;
- знать формулу `calibrated_multichannel`;
- читать raw events;
- пересобирать сессии.

Его ответственность — только агрегировать факты из already-built daily pairs.

---

## 9. Пример

Входные daily pairs:

```text
pair_date  | item_id | similar_item_id | user_id | session_id | signal_type
2026-04-01 | 1       | 2               | 10      | s1         | view
2026-04-01 | 1       | 2               | 11      | s2         | click
2026-04-02 | 1       | 2               | 12      | s3         | to_cart
2026-04-02 | 1       | 3               | 10      | s1         | favorite
```

Вызов:

```python
PairAggregator().aggregate_window(
    daily_pairs=[pairs_day_1, pairs_day_2],
    window_start="2026-04-01",
    window_end="2026-04-02",
)
```

Выход:

```text
item_id | similar_item_id | pair_count | view_count | click_count | favorite_count | to_cart_count | unique_users | unique_sessions
1       | 2               | 3          | 1          | 1           | 0              | 1             | 3            | 3
1       | 3               | 1          | 0          | 0           | 1              | 0             | 1            | 1
```

---

## 10. Почему `unique_sessions` считается как `(user_id, session_id)`

`session_id` может быть уникальным глобально, но это не всегда гарантировано.

Безопаснее считать уникальную сессию как пару:

```text
(user_id, session_id)
```

Так мы защищаемся от ситуации, когда у разных пользователей случайно совпали строки `session_id`.

---

## 11. Поведение на пустом входе

Если:

```python
daily_pairs = []
```

агрегатор возвращает пустой DataFrame с колонками:

```text
item_id
similar_item_id
pair_count
view_count
click_count
favorite_count
to_cart_count
unique_users
unique_sessions
window_start
window_end
```

Это нужно, чтобы downstream не ломался на малых выборках или на датах без данных.

---

## 12. Ошибки и проверки

Агрегатор должен падать с понятной ошибкой, если:

- `window_start` не ISO-дата;
- `window_end` не ISO-дата;
- `window_start > window_end`;
- входные daily pairs не соответствуют контракту;
- выходные pair aggregates не соответствуют контракту.

---

## 13. Тесты

Тесты находятся в файле:

```text
tests/test_aggregate_pairs.py
```

Минимальный набор проверок:

1. Агрегация одной пары за несколько дней.
2. Фильтрация строк вне rolling window.
3. Подсчёт `view_count`, `click_count`, `favorite_count`, `to_cart_count`.
4. Проверка инварианта:

```text
pair_count = view_count + click_count + favorite_count + to_cart_count
```

5. Подсчёт `unique_users`.
6. Подсчёт `unique_sessions` через `(user_id, session_id)`.
7. Пустой вход `daily_pairs=[]`.
8. Невалидный формат даты.
9. Невалидный диапазон дат.
10. LazyFrame input.

Запуск:

Если терминал открыт в папке `docs/`, перейдите в корень проекта
относительным путём:

```bash
cd ..
```

```bash
uv run pytest tests/test_aggregate_pairs.py
```

---

## 14. Короткий итог

`PairAggregator` — это не scorer. Он не знает, сколько должен весить `to_cart`, и не должен знать.

Он создаёт чистый агрегат:

```text
сколько раз пара встретилась,
через какие signal_type она встретилась,
сколько пользователей и сессий подтвердили связь,
за какое окно это посчитано.
```

Все веса появляются только на следующем шаге — в `CoVisitationScorer`.
