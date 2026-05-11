# Контракты данных для calibrated multi-channel MVP

Этот документ фиксирует актуальные контракты данных для MVP-пайплайна виджета «Похожие товары по интересам пользователей».

Главное решение: **события не взвешиваются до scoring-слоя**.

До `CoVisitationScorer` мы сохраняем факты:

```text
raw events
→ clean events
→ sessions
→ daily item pairs
→ pair aggregates
```

А финальные веса, частотная калибровка и итоговый `score` появляются только здесь:

```text
pair aggregates
→ CoVisitationScorer
→ pair scores
```

Это нужно, чтобы не домножить один и тот же сигнал дважды и не смешать слишком рано разные типы поведения: `view`, `click`, `favorite`, `to_cart`.

---

## 1. Почему в основном контракте нет `action_weight`, `pair_weight`, `weight_sum`, `weighted_events`

В старой версии baseline были поля:

```text
action_weight
pair_weight
weight_sum
weighted_events
```

Теперь они **не входят в обязательный контракт**.

Причина: эти поля заставляют нас слишком рано превратить разные каналы поведения в одно число. После этого уже трудно понять, почему пара получила высокий вес:

```text
300 просмотров?
20 кликов?
3 добавления в избранное?
1 добавление в корзину?
```

Для калибровки нам важно хранить каналы отдельно:

```text
view_count
click_count
favorite_count
to_cart_count
```

Только scorer решает, сколько должен весить каждый канал.

---

## 2. Raw events

Исходные пользовательские события.

Ожидаемый контракт:

```text
user_id
date
timestamp
action_type
widget_name
search_query
item_id
```

Смысл полей:

- `user_id` — идентификатор пользователя;
- `date` — дата партиции или исходная дата события;
- `timestamp` — время события;
- `action_type` — тип действия пользователя;
- `widget_name` — интерфейсный контекст действия;
- `search_query` — поисковый запрос, если событие связано с поиском;
- `item_id` — идентификатор товара, если действие связано с товаром.

Raw events не используются напрямую для pair building. Сначала они должны пройти `EventCleaner`.

---

## 3. Clean events

Создаёт: `EventCleaner`.

Актуальный обязательный контракт:

```text
user_id
event_date
timestamp
action_type
item_id
search_query
widget_name
```

Что делает `EventCleaner`:

- проверяет наличие обязательных колонок в raw events;
- приводит `timestamp` к нормальному datetime-типу;
- создаёт `event_date`;
- удаляет явные дубли;
- оставляет товарные действия с непустым `item_id`;
- сохраняет `action_type`, `widget_name`, `search_query`.

Что `EventCleaner` **не делает**:

- не создаёт `action_weight`;
- не применяет business weights;
- не применяет inverse-frequency normalization;
- не считает score;
- не строит сессии;
- не строит пары.

Источник истины для силы события на этом этапе — это только `action_type`.

---

## 4. Sessions

Создаёт: `SessionBuilder`.

Актуальный обязательный контракт:

```text
user_id
session_id
event_date
timestamp
action_type
item_id
```

Что делает `SessionBuilder`:

- получает `events_clean`;
- сортирует события по `user_id` и `timestamp`;
- считает временные разрывы между событиями;
- создаёт `session_id`;
- сохраняет `action_type` для downstream-логики;
- отдаёт события внутри пользовательских сессий.

Что `SessionBuilder` **не делает**:

- не создаёт `action_weight`;
- не схлопывает повторные действия одного товара;
- не выбирает strongest action для товара;
- не строит пары;
- не считает score.

Сессия — это контекст. Веса не являются частью контракта сессий.

---

## 5. Item popularity

Создаёт: `ItemPopularityBuilder`.

Актуальный обязательный контракт:

```text
item_id
events_count
unique_users
views_count
clicks_count
favorites_count
to_cart_count
```

Смысл полей:

- `events_count` — общее число товарных событий по товару;
- `unique_users` — число уникальных пользователей;
- `views_count` — число `view`;
- `clicks_count` — число `click`;
- `favorites_count` — число `favorite`;
- `to_cart_count` — число `to_cart`.

Что не входит в обязательный контракт:

```text
weighted_events
```

Почему: в новой архитектуре item popularity — это факты, а не взвешенный score.

---

## 6. Action type distribution for calibration

Создаёт: `ItemPopularityBuilder` или отдельный calibration helper.

Контракт:

```text
action_type
events_count
event_share
unique_users
unique_items
calibration_start
calibration_end
```

Этот артефакт нужен для `CoVisitationScorer`, чтобы посчитать частотную поправку:

```text
frequency_boost[action] = (reference_share / action_share) ^ beta
```

Важно: этот артефакт не меняет события. Он только сохраняет статистику для будущей калибровки.

---

## 7. Daily item pairs

Создаёт: `ItemPairBuilder`.

Актуальный обязательный контракт:

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

- `pair_date` — дата пары;
- `item_id` — исходный товар;
- `similar_item_id` — товар-кандидат;
- `session_id` — сессия, где возникла связь;
- `user_id` — пользователь, создавший связь;
- `source_action_type` — strongest action исходного товара внутри сессии;
- `target_action_type` — strongest action товара-кандидата внутри сессии;
- `signal_type` — канал сигнала пары.

Правило для `signal_type`:

```text
signal_type = target_action_type
```

Пример:

```text
session:
A: view
B: to_cart
C: view

pairs:
A -> B: signal_type = to_cart
C -> B: signal_type = to_cart
B -> A: signal_type = view
B -> C: signal_type = view
```

Что не входит в обязательный контракт:

```text
pair_weight
```

Почему: пары должны хранить факты и канал сигнала. Вес канала появляется только в scorer.

---

## 8. Pair aggregates

Создаёт: `PairAggregator`.

Актуальный обязательный контракт:

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

- `pair_count` — сколько раз пара встретилась в rolling window;
- `view_count` — сколько pair events пришли из `signal_type = view`;
- `click_count` — сколько pair events пришли из `signal_type = click`;
- `favorite_count` — сколько pair events пришли из `signal_type = favorite`;
- `to_cart_count` — сколько pair events пришли из `signal_type = to_cart`;
- `unique_users` — сколько уникальных пользователей создали пару;
- `unique_sessions` — сколько уникальных `(user_id, session_id)` создали пару;
- `window_start` — начало окна;
- `window_end` — конец окна.

Проверка качества:

```text
pair_count = view_count + click_count + favorite_count + to_cart_count
```

Что не входит в обязательный контракт:

```text
weight_sum
```

Почему: агрегатор не применяет веса. Он только считает факты.

---

## 9. Pair scores

Создаёт: `CoVisitationScorer`.

Актуальный обязательный контракт:

```text
item_id
similar_item_id
score
pair_count
view_count
click_count
favorite_count
to_cart_count
unique_users
unique_sessions
```

Scorer впервые применяет:

- `business_weights`;
- `action_shares_used_for_calibration`;
- `beta`;
- `max_frequency_boost`;
- thresholds;
- итоговую формулу `score`.

Основной strong-MVP метод:

```text
calibrated_multichannel
```

Пример формулы:

```text
score =
    w_view     * log1p(view_count)
  + w_click    * log1p(click_count)
  + w_favorite * log1p(favorite_count)
  + w_to_cart  * log1p(to_cart_count)
```

Где `w_*` — effective weights после частотной калибровки.

---

## 10. Recommendations

Создаёт: `TopKSelector`.

Минимальный контракт:

```text
item_id
similar_item_id
score
rank
source
```

Смысл полей:

- `item_id` — исходный товар;
- `similar_item_id` — рекомендованный похожий товар;
- `score` — итоговый score от scorer-а;
- `rank` — позиция кандидата внутри списка рекомендаций;
- `source` — источник рекомендации, например `behavioral`.

Для ручной проверки можно сохранять расширенную таблицу:

```text
item_id
similar_item_id
score
rank
source
pair_count
view_count
click_count
favorite_count
to_cart_count
unique_users
unique_sessions
```

Но compact output для lookup должен оставаться простым.

---

## 11. Final lookup output

Создаёт: `RecommendationWriter`.

Контракт для виджета/lookup:

```text
item_id
similar_items_sku_list
```

Где `similar_items_sku_list` — список `similar_item_id`, отсортированный по `rank`.

Lookup не пересчитывает pipeline. Он только читает готовый опубликованный результат.

---

## 12. Итоговая граница ответственности

```text
EventCleaner:
  cleaning + action_type preserved

SessionBuilder:
  session_id + action_type preserved

ItemPopularityBuilder:
  item counts + action_type distribution for calibration

ItemPairBuilder:
  strongest item signal + directed pairs + signal_type

PairAggregator:
  channel counts only

CoVisitationScorer:
  calibrated weights + final score

TopKSelector:
  top-K by ready score

RecommendationWriter:
  detailed output + compact lookup + manifest

SimilarItemsLookup:
  read ready recommendations
```

Ключевое правило:

```text
До CoVisitationScorer нет весов.
В CoVisitationScorer появляется один финальный score.
```

Именно это защищает проект от double weighting и делает рекомендации объяснимыми.

---

## 13. Где живёт порядок силы action_type для ItemPairBuilder

`ItemPairBuilder` должен знать, какое действие сильнее внутри одной сессии, чтобы схлопнуть повторный товар в один item-level signal. Это не вес и не score, а только порядок категорий:

```text
view < click < favorite < to_cart
```

Порядок задаётся в config:

```yaml
item_pair_builder:
  signal_priority:
    view: 1
    click: 2
    favorite: 3
    to_cart: 4
```

Список допустимых товарных действий задаётся отдельно:

```yaml
events:
  item_action_types:
    - view
    - click
    - favorite
    - to_cart
```

Важно: эти числа не являются business weights. Они используются только для выбора strongest signal внутри одной сессии. Финальные веса остаются в `scoring.business_weights` и применяются только в `CoVisitationScorer`.
