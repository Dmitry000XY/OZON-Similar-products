# ItemPairBuilder: построение дневных multi-channel пар товаров

Этот документ описывает текущую роль файла:

```text
src/ozon_similar_products/retrieval/build_pairs.py
```

и класса:

```python
ItemPairBuilder
```

`ItemPairBuilder` относится к шагу 9 MVP pipeline: **«Построить дневные пары товаров»**.

Главное решение текущей архитектуры: **ItemPairBuilder не применяет веса событий**. Он не считает `action_weight`, `pair_weight` или `score`. Его задача — сохранить факты о том, какие товары оказались рядом в одной пользовательской сессии, и какой `action_type` стал сигналом для пары.

---

## 1. Место в pipeline

Поток данных вокруг этого слоя:

```text
clean events
→ SessionBuilder
→ sessions
→ ItemPairBuilder
→ daily item pairs
→ PairAggregator
→ pair aggregates
→ CoVisitationScorer
```

До `ItemPairBuilder` у нас уже есть пользовательские сессии. Сессия — это короткий контекст поведения пользователя. Внутри него товары считаются связанными: если пользователь в одной сессии взаимодействовал с товарами A, B и C, то эти товары становятся кандидатами для co-visitation графа.

После `ItemPairBuilder` появляются directed pairs:

```text
A → B
A → C
B → A
B → C
C → A
C → B
```

Пары направленные, потому что downstream выбирает список похожих товаров для каждого конкретного `item_id`.

---

## 2. Почему в ItemPairBuilder нет весов

Раньше можно было бы сделать так:

```text
action_type → action_weight → pair_weight
```

Но мы отказались от этого в основном MVP-контракте. Причина: раннее взвешивание слишком быстро сжимает разные типы поведения в одно число. После этого невозможно понять, почему пара стала сильной:

```text
из-за многих просмотров?
из-за кликов?
из-за избранного?
из-за добавлений в корзину?
```

В текущей архитектуре правильный путь такой:

```text
ItemPairBuilder: сохраняет signal_type
PairAggregator: считает view_count / click_count / favorite_count / to_cart_count
CoVisitationScorer: применяет веса и считает score
```

Так мы избегаем double weighting и можем менять веса scorer-а без пересборки пар.

---

## 3. Входной контракт

На вход `ItemPairBuilder.transform_day(...)` получает таблицу `sessions`:

```text
user_id
session_id
event_date
timestamp
action_type
item_id
```

Смысл полей:

- `user_id` — пользователь, создавший сессию;
- `session_id` — идентификатор короткого пользовательского контекста;
- `event_date` — дата события, используется как `pair_date`;
- `timestamp` — время события, нужно в upstream и может быть полезно для отладки;
- `action_type` — тип действия: `view`, `click`, `favorite`, `to_cart`;
- `item_id` — товар.

`action_weight` в этом контракте нет. Если оно есть в старых экспериментах, `ItemPairBuilder` не должен от него зависеть.

---

## 4. Выходной контракт

На выходе получается `daily_item_pairs`:

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

- `pair_date` — дата пары, обычно берётся из `event_date`;
- `item_id` — исходный товар;
- `similar_item_id` — кандидат, который будет похожим товаром;
- `session_id` — сессия, в которой возникла связь;
- `user_id` — пользователь, создавший связь;
- `source_action_type` — strongest action для исходного товара внутри сессии;
- `target_action_type` — strongest action для товара-кандидата внутри сессии;
- `signal_type` — канал сигнала пары. Для пары `A → B` он равен `target_action_type`, потому что нам важно, что пользователь сделал именно с кандидатом `B`.

---

## 5. Как выбирается strongest signal внутри сессии

Один и тот же товар может встретиться в одной сессии несколько раз:

```text
A: view
A: click
A: to_cart
```

Для построения пар нам не нужно создавать три версии одного товара. Мы схлопываем товар в один item-level signal. Берём самое сильное действие по приоритету:

```text
view < click < favorite < to_cart
```

В примере выше item A внутри session будет представлен как:

```text
A: to_cart
```

Это схлопывание происходит только внутри одной сессии и только для одного товара. Оно не является scoring-ом: мы не присваиваем числовой вес, а только выбираем strongest categorical signal.

---

## 6. Почему `signal_type = target_action_type`

Для пары:

```text
A → B
```

мы хотим понять, насколько хорош товар `B` как кандидат для товара `A`.

Поэтому важнее действие с target item `B`, а не только действие с source item `A`.

Пример:

```text
session:
A: view
B: to_cart
C: view
```

Пары:

```text
A → B: signal_type = to_cart
C → B: signal_type = to_cart
B → A: signal_type = view
B → C: signal_type = view
```

Это означает: товары A и C ведут к кандидату B, который пользователь добавил в корзину. Для рекомендаций `A → B` и `C → B` это сильный сигнал.

---

## 7. Конфигурация приоритетов

Приоритеты signal type вынесены из кода в config. В `configs/baseline.yaml` есть блок:

```yaml
item_pair_builder:
  signal_priority:
    view: 1
    click: 2
    favorite: 3
    to_cart: 4
```

Также есть список допустимых товарных действий:

```yaml
events:
  item_action_types:
    - view
    - click
    - favorite
    - to_cart
```

В коде для этого есть фабрика:

```python
ItemPairBuilder.from_config(config)
```

Она берёт:

- `pipeline.max_items_per_session`;
- `events.item_action_types`;
- `item_pair_builder.signal_priority`.

Так мы не хардкодим бизнес-приоритеты прямо в `build_pairs.py`. Если команда решит поменять порядок сигналов или добавить новый action type, это должно начинаться с config и контрактов, а не с ручного редактирования бизнес-логики в нескольких местах.

---

## 8. Ограничение длинных сессий

Количество directed pairs внутри сессии растёт квадратично:

```text
L товаров → L * (L - 1) пар
```

Если в сессии 100 товаров, получится 9900 directed pairs. Часто такие сессии шумные: пользователь мог просто долго скроллить, сессия могла быть неправильно собрана, либо в данных есть техническая активность.

Поэтому `ItemPairBuilder` использует:

```text
max_items_per_session
```

В config:

```yaml
pipeline:
  max_items_per_session: 50
```

В MVP слишком длинные сессии проще и безопаснее пропускать целиком. Это снижает шум и защищает pipeline от взрыва количества пар.

---

## 9. Что делает `transform_day`

Метод:

```python
ItemPairBuilder.transform_day(sessions)
```

делает следующие шаги:

1. Валидирует вход как `sessions`.
2. Удаляет строки без `item_id`.
3. Оставляет только `item_action_types` из config.
4. Для каждого `(user_id, session_id, event_date, item_id)` выбирает strongest `action_type`.
5. Считает число уникальных товаров в сессии.
6. Оставляет только сессии с числом товаров от 2 до `max_items_per_session`.
7. Делает self-join session items внутри одной сессии.
8. Удаляет self-pairs.
9. Записывает `source_action_type`, `target_action_type`, `signal_type`.
10. Возвращает таблицу `daily_item_pairs`.

---

## 10. Что модуль не делает

`ItemPairBuilder` не должен:

- читать raw events;
- строить session_id;
- считать item popularity;
- применять `business_weights`;
- применять inverse-frequency normalization;
- считать `score`;
- выбирать top-K;
- сохранять финальные рекомендации;
- создавать `pair_weight`.

Его зона ответственности заканчивается на daily pair events.

---

## 11. Как тестировать

Минимальные тесты должны проверять:

1. Сессию из трёх товаров.
2. Directed pairs в обе стороны.
3. Отсутствие self-pairs.
4. Схлопывание повторного товара в strongest signal.
5. `signal_type = target_action_type`.
6. Игнорирование строк без `item_id`.
7. Игнорирование action_type вне `item_action_types`.
8. Пропуск однотоварных сессий.
9. Пропуск слишком длинных сессий.
10. Работу с `LazyFrame`.
11. Создание builder-а через `from_config`.

Запуск:

```bash
uv run pytest tests/test_build_pairs.py
```

---

## 12. Короткий итог

`ItemPairBuilder` отвечает за переход:

```text
sessions → daily_item_pairs
```

Он сохраняет структуру поведения, но не взвешивает её. Главная ценность нового контракта — мы не теряем канал сигнала:

```text
view / click / favorite / to_cart
```

И поэтому можем позже в `CoVisitationScorer` честно калибровать веса, не пересобирая пары и не перечитывая raw events.
