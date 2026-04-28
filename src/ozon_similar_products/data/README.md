Пример использования в ноутбуке:
from ozon_similar_products.data import load_configs, load_events, load_products

config = load_configs()

events_sample = load_events(
    config,
    use_sample=True,
    sample_days=1,
)

products = load_products(config)

events_sample.head()*/

Для чтения только одного типа событий:

clicks = load_events(
    config,
    use_sample=True,
    sample_days=1,
    action_types="click",
)

Для конкретных дат:

events = load_events(
    config,
    dates=["2024-03-01", "2024-03-02"],
)

Для больших вычислений:

from ozon_similar_products.data import load_configs, scan_events

config = load_configs()

events_lazy = scan_events(
    config,
    start_date="2024-03-01",
    end_date="2024-03-07",
    action_types=["view", "click", "to_cart"],
)

result = (
    events_lazy
    .group_by("item_id", "action_type")
    .len()
    .collect()
)