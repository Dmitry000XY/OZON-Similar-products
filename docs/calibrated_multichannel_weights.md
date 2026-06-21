# Калиброванные multi-channel веса: калибровка весов событий

## 1. Зачем нужен этот документ

Этот документ фиксирует финальную логику работы с весами событий в MVP для виджета «Похожие товары по интересам
пользователей».

Главная проблема: разные `action_type` встречаются с очень разной частотой. Например, просмотров (`view`) может быть в
20 раз больше, чем добавлений в корзину (`to_cart`). Если просто задать веса `view = 1`, `to_cart = 4`, то массовые
просмотры всё равно могут перекрыть редкие, но намного более ценные cart-сигналы.

Поэтому мы используем схему **calibrated multi-channel scoring**:

```text
view_count
click_count
favorite_count
to_cart_count
→ CoVisitationScorer
→ final score
```

События не взвешиваются заранее в `EventCleaner`, `SessionBuilder`, `ItemPairBuilder` или `PairAggregator`. Все веса
применяются только в `CoVisitationScorer`.

---

## 2. Главный принцип: не сжимать сигналы раньше времени

Плохой вариант:

```text
view / click / favorite / to_cart
→ сразу один action_weight
→ pair_weight
→ weight_sum
→ score
```

Почему плохо: после раннего схлопывания мы уже не понимаем, почему пара получила высокий вес. Она могла получить его
из-за 300 просмотров или из-за 3 добавлений в корзину. Для калибровки и анализа качества это слишком малоинформативно.

Правильный вариант:

```text
view / click / favorite / to_cart
→ отдельные channel counts
→ calibrated scorer
→ score
```

Для каждой пары товаров храним отдельные каналы:

```text
item_id
similar_item_id
view_count
click_count
favorite_count
to_cart_count
unique_users
unique_sessions
```

И только после этого считаем итоговый score.

---

## 3. Граница ответственности слоёв

### EventCleaner

EventCleaner только чистит события и сохраняет `action_type`.

Актуальный контракт `events_clean`:

```text
user_id
event_date
timestamp
action_type
item_id
search_query
widget_name
```

EventCleaner НЕ делает:

- `action_weight`;
- business weights;
- inverse-frequency normalization;
- calibrated weights;
- score.

Источник истины для силы события — это `action_type`.

---

### SessionBuilder

SessionBuilder строит пользовательские сессии и сохраняет `action_type`.

Актуальный контракт `sessions`:

```text
user_id
session_id
event_date
timestamp
action_type
item_id
```

SessionBuilder НЕ делает:

- веса событий;
- схлопывание действий одного товара;
- scoring;
- pair building.

---

### ItemPairBuilder

ItemPairBuilder превращает события внутри сессии в directed item pairs.

Внутри одной сессии один товар может встретиться несколько раз. Например:

```text
A: view
A: click
A: to_cart
```

Для такого товара внутри сессии оставляем strongest signal по приоритету:

```text
to_cart > favorite > click > view
```

То есть item `A` в этой сессии будет представлен как `to_cart`.

Дальше строим directed pairs. Для пары `A → B` основным сигналом считаем действие с target item `B`, потому что если мы
рекомендуем `B` для `A`, особенно важно, что пользователь сделал именно с кандидатом `B`.

Пример:

```text
session:
A: view
B: to_cart
C: view
```

Получаем:

```text
A → B: signal_type = to_cart
C → B: signal_type = to_cart
B → A: signal_type = view
B → C: signal_type = view
```

Актуальный контракт `daily_item_pairs`:

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

ItemPairBuilder НЕ делает:

- `pair_weight`;
- business weights;
- inverse-frequency normalization;
- final score.

---

### PairAggregator

PairAggregator агрегирует пары за rolling window и считает факты по каналам.

Актуальный контракт `pair_aggregates`:

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

Проверка качества агрегата:

```text
pair_count = view_count + click_count + favorite_count + to_cart_count
```

PairAggregator НЕ делает:

- `weight_sum`;
- business weights;
- calibrated score;
- top-K.

---

### CoVisitationScorer

CoVisitationScorer — единственное место, где появляются веса.

Он получает channel counts и считает итоговый score.

Актуальный вход:

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

Актуальный выход `pair_scores`:

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

---

## 4. Базовая формула score

Для метода `calibrated_multichannel` используем формулу:

```text
score(A, B) =
    effective_weight_view     * log1p(view_count)
  + effective_weight_click    * log1p(click_count)
  + effective_weight_favorite * log1p(favorite_count)
  + effective_weight_to_cart  * log1p(to_cart_count)
```

Почему используем `log1p`:

- просмотры остаются полезным coverage-сигналом;
- массовые просмотры не растут линейно бесконечно;
- один сильный cart-сигнал может перебить десятки случайных views.

Например:

```text
log1p(1)   ≈ 0.69
log1p(10)  ≈ 2.40
log1p(100) ≈ 4.62
```

То есть 100 просмотров не становятся в 100 раз сильнее одного просмотра.

---

## 5. Business weights

Фрагмент config-а:

```yaml
scoring:
  method: calibrated_multichannel
  business_weights:
    view: 1.0
    click: 3.0
    favorite: 6.0
    to_cart: 8.0
```

`business_weights` — это ручные бизнес-приоритеты каналов.

Смысл:

```text
view     — самый слабый сигнал
click    — пользователь проявил интерес сильнее, чем просто просмотр
favorite — сильный сигнал отложенного интереса
to_cart  — самый сильный сигнал, прокси целевого действия
```

Важно: `business_weights` — ещё не финальные веса. Они дополнительно корректируются с учётом частоты каналов.

---

## 6. Frequency boost

Проблема: если `view` встречается в 20 раз чаще `to_cart`, то даже `to_cart = 8 * view` может быть недостаточно.

Поэтому вводим частотную поправку:

```text
effective_weight[action] =
    business_weight[action] * frequency_boost[action]
```

Где:

```text
frequency_boost[action] =
    (reference_share / action_share) ^ beta
```

Параметры:

- `action_share` — доля конкретного action type на calibration window;
- `reference_share` — доля reference action type, обычно `view`;
- `beta` — сила компенсации редкости события.

---

## 7. Beta

Фрагмент config-а:

```yaml
beta: 0.5
```

`beta` управляет тем, насколько сильно мы компенсируем редкость события.

```text
beta = 0
```

Частотной компенсации нет:

```text
frequency_boost = 1
```

```text
beta = 1
```

Полная компенсация редкости события.

```text
beta = 0.5
```

Мягкая компенсация через квадратный корень.

Для MVP выбираем `beta = 0.5`, потому что это компромисс: редкие сильные сигналы усиливаются, но один случайный cart не
становится бесконечно огромным сигналом.

---

## 8. Reference action type

Фрагмент config-а:

```yaml
reference_action_type: view
```

`reference_action_type` — это канал, относительно которого считаем редкость остальных каналов.

Обычно берём `view`, потому что просмотры — самый частый и базовый канал.

Пример долей на calibration window:

```text
view_share = 0.80
click_share = 0.12
favorite_share = 0.04
to_cart_share = 0.04
```

Для `to_cart`:

```text
reference_share / action_share = 0.80 / 0.04 = 20
```

При `beta = 0.5`:

```text
frequency_boost_to_cart = sqrt(20) ≈ 4.47
```

Если `business_weight_to_cart = 8`, то:

```text
effective_weight_to_cart = 8 * 4.47 ≈ 35.8
```

Один cart-сигнал становится примерно как 36 view-сигналов, а не как 8.

---

## 9. Max frequency boost

Фрагмент config-а:

```yaml
max_frequency_boost:
  view: 1.0
  click: 10.0
  favorite: 15.0
  to_cart: 30.0
```

`max_frequency_boost` ограничивает слишком большие частотные коэффициенты.

Зачем это нужно: если какой-то action type очень редкий, его boost может стать огромным. Тогда один случайный сигнал
может слишком сильно поднять пару.

Например:

```text
view_share = 0.90
to_cart_share = 0.001
```

Тогда:

```text
sqrt(0.90 / 0.001) = sqrt(900) = 30
```

Если `to_cart_share` ещё меньше, boost станет ещё больше. Поэтому ставим верхнюю границу.

Для `view` boost ограничен `1.0`, потому что view — базовый частый сигнал, его не нужно усиливать.

---

## 10. Thresholds

Фрагмент config-а:

```yaml
min_pair_count: 1
min_unique_users: 1
min_unique_sessions: 1
```

Эти параметры фильтруют слабые пары.

### min_pair_count

Минимальное общее число появлений пары.

```text
pair_count < min_pair_count → пару отбрасываем
```

В MVP стоит `1`, чтобы не потерять coverage.

Позже можно поднять до `2` или `3`.

### min_unique_users

Минимальное число уникальных пользователей, которые создали связь.

Пример слабого случая:

```text
A → B
pair_count = 20
unique_users = 1
```

Один пользователь мог много раз создать одну и ту же связь.

Пример сильного случая:

```text
A → B
pair_count = 20
unique_users = 15
```

Связь подтверждена разными пользователями.

### min_unique_sessions

Минимальное число уникальных сессий, где появилась пара.

Это защита от одной длинной или странной сессии.

В MVP thresholds мягкие. После offline evaluation можно сделать правила строже, особенно для view-only пар.

---

## 11. Calibration metadata

Фрагмент config-а:

```yaml
calibration:
  action_shares_used_for_calibration: null
  calibration_start: null
  calibration_end: null
```

Это место для фиксации реальных долей action type, на которых калибровались веса.

Пока стоит `null`, потому что реальные доли нужно посчитать на train/calibration window.

После калибровки должно быть примерно так:

```yaml
calibration:
  action_shares_used_for_calibration:
    view: 0.80
    click: 0.12
    favorite: 0.04
    to_cart: 0.04
  calibration_start: "2026-04-01"
  calibration_end: "2026-04-25"
```

Это нужно для воспроизводимости. Через неделю команда должна понимать:

- какие доли использовались;
- за какой период они считались;
- почему effective weights получились именно такими.

---

## 12. Что делать, если calibration shares пока null

Есть два возможных режима.

### Мягкий MVP-режим

Если `action_shares_used_for_calibration = null`, то:

```text
frequency_boost[action] = 1
```

Тогда scorer использует только `business_weights`.

Плюс: pipeline запускается сразу.

Минус: редкие сильные сигналы пока не получают частотную компенсацию.

В manifest нужно явно сохранить:

```text
calibration_used = false
```

### Строгий production-like режим

Если `method = calibrated_multichannel`, но calibration shares отсутствуют, scorer падает с понятной ошибкой.

Плюс: невозможно случайно запустить финальный pipeline без калибровки.

Минус: менее удобно для MVP.

Для текущего MVP лучше мягкий режим, но перед финальной презентацией стоит посчитать реальные action shares и включить
калибровку.

---

## 13. Полный пример расчёта

Пусть для пары `A → B` агрегатор посчитал:

```text
view_count = 100
click_count = 5
favorite_count = 1
to_cart_count = 1
```

Доли action type на calibration window:

```text
view = 0.80
click = 0.12
favorite = 0.04
to_cart = 0.04
```

Config:

```text
business_weight_view = 1
business_weight_click = 3
business_weight_favorite = 6
business_weight_to_cart = 8
beta = 0.5
reference_action_type = view
```

Считаем boosts:

```text
boost_view = sqrt(0.80 / 0.80) = 1
boost_click = sqrt(0.80 / 0.12) ≈ 2.58
boost_favorite = sqrt(0.80 / 0.04) ≈ 4.47
boost_to_cart = sqrt(0.80 / 0.04) ≈ 4.47
```

Effective weights:

```text
effective_view = 1 * 1 = 1
effective_click = 3 * 2.58 ≈ 7.74
effective_favorite = 6 * 4.47 ≈ 26.82
effective_to_cart = 8 * 4.47 ≈ 35.76
```

Score:

```text
score =
    1     * log1p(100)
  + 7.74  * log1p(5)
  + 26.82 * log1p(1)
  + 35.76 * log1p(1)
```

Приблизительно:

```text
log1p(100) ≈ 4.62
log1p(5)   ≈ 1.79
log1p(1)   ≈ 0.69
```

Тогда:

```text
score ≈
    1 * 4.62
  + 7.74 * 1.79
  + 26.82 * 0.69
  + 35.76 * 0.69

score ≈
    4.62
  + 13.85
  + 18.51
  + 24.67

score ≈ 61.65
```

Видно, что 100 просмотров дают вклад около `4.62`, а один cart даёт вклад около `24.67`. Это желаемое поведение:
просмотры помогают coverage, но не забивают сильные действия.

---

## 14. Как подбирать веса честно

Веса не нужно подбирать на глаз навсегда. Их нужно калибровать через offline evaluation.

Рекомендуемая схема:

```text
train window: дни 1–25
validation/test window: дни 26–30
```

На train window строим рекомендации.

На validation/test window проверяем, попали ли будущие сильные действия пользователя в top-K.

Важно: оптимизироваться нужно не на будущие views, а на сильные действия.

Приоритет target-событий для evaluation:

```text
to_cart   — главный успех
favorite  — сильный успех
click     — слабый успех
view      — очень слабый target или только coverage-сигнал
```

Метрики:

- `CartHitRate@K`;
- `WeightedRecall@K`;
- `MRR@K`;
- `NDCG@K`;
- coverage;
- доля view-only рекомендаций;
- popularity bias;
- manual review.

Сравниваем минимум:

```text
score_method = pair_count
score_method = calibrated_multichannel без frequency boost
score_method = calibrated_multichannel с beta = 0.5
score_method = calibrated_multichannel с beta = 1.0
```

После выбора лучшей схемы сохраняем её в config и manifest.

---

## 15. Как часто пересчитывать веса

Веса не должны автоматически плавать каждый daily run.

Правильная стратегия:

1. Берём исторический calibration period.
2. Считаем action shares.
3. Подбираем `business_weights`, `beta`, thresholds на validation.
4. Сохраняем выбранную версию в config.
5. Daily pipeline использует фиксированную версию config.
6. Раз в неделю/месяц или при заметном drift пересчитываем калибровку.

Так результат становится воспроизводимым: если сегодня и завтра входные данные одинаковые, score не меняется из-за
случайно пересчитанных долей.

---

## 16. Итоговая позиция

Финальная архитектура:

```text
EventCleaner:
  сохраняет action_type, не считает веса

SessionBuilder:
  сохраняет action_type, не считает веса

ItemPairBuilder:
  строит directed pairs и signal_type

PairAggregator:
  считает view_count / click_count / favorite_count / to_cart_count

CoVisitationScorer:
  применяет business weights, frequency boost, beta и считает score

TopKSelector:
  выбирает top-K по готовому score
```

Коротко:

```text
business_weights — насколько событие важно для бизнеса.
beta — насколько компенсируем редкость события.
reference_action_type — относительно какого события считаем редкость.
max_frequency_boost — защита от слишком огромных весов.
min_* — фильтры слабых пар.
calibration — реальные доли action_type и период, по которому они считались.
```

Главная идея: **не взвешиваем события раньше времени, не теряем каналы, применяем веса только в Scorer**.
