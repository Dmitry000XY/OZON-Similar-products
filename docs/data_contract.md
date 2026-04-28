# Data Contract

Этот документ фиксирует текущие raw-колонки и минимальный контракт, от которого будут отталкиваться загрузчики, EDA и будущий pipeline.

Идентификатор товара в локальных данных хранится в колонке `item_id`. Во внешней постановке кейса он называется `sku`.

## Product Information

Ожидаемые колонки:

- `item_id`
- `name`
- `brand`
- `type`
- `category_id`
- `category_name`

## User Actions

Ожидаемые колонки:

- `user_id`
- `date`
- `timestamp`
- `action_type`
- `widget_name`
- `search_query`
- `item_id`

## Action Types

Текущие типы действий:

- `search`
- `view`
- `click`
- `to_cart`
- `favorite`

В текущем sample нет действия `order`. Его можно добавить в конфиги и pipeline позже, если оно появится в полной выгрузке.

TODO: подробно описать типы колонок, nullable-поля, ожидаемые форматы дат и правила обработки событий без товара.
