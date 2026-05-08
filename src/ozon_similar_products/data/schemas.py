"""DataFrame schemas and column contracts for MVP pipeline."""

from ozon_similar_products.config import load_data_config

EXTERNAL_SKU_COLUMN = "sku"

_DATA_CONFIG = load_data_config()
_USER_ACTIONS = _DATA_CONFIG.get("user_actions", {})
_PRODUCTS = _DATA_CONFIG.get("product_information", {})

ITEM_ID_COLUMN = _PRODUCTS.get("id_column", "item_id")
KNOWN_ACTION_TYPES = _USER_ACTIONS.get("known_action_types", [])
RAW_EVENTS_COLUMNS = _USER_ACTIONS.get("expected_columns", [])

CLEAN_EVENTS_COLUMNS = [
    "user_id",
    "event_date",
    "timestamp",
    "action_type",
    "item_id",
    "search_query",
    "widget_name",
    "action_weight",
]

PRODUCT_INFORMATION_COLUMNS = _PRODUCTS.get("expected_columns", [])


SESSIONS_COLUMNS = [
    "user_id",
    "session_id",
    "event_date",
    "timestamp",
    "action_type",
    "item_id",
    "action_weight",
]

ITEM_POPULARITY_COLUMNS = [
    "item_id",
    "events_count",
    "unique_users",
    "views_count",
    "clicks_count",
    "favorites_count",
    "to_cart_count",
]

DAILY_ITEM_PAIRS_COLUMNS = [
    "pair_date",
    "item_id",
    "similar_item_id",
    "session_id",
    "user_id",
    "pair_weight",
]

PAIR_AGGREGATES_COLUMNS = [
    "item_id",
    "similar_item_id",
    "pair_count",
    "weight_sum",
    "unique_users",
    "unique_sessions",
    "window_start",
    "window_end",
]

PAIR_SCORES_COLUMNS = [
    "item_id",
    "similar_item_id",
    "score",
    "pair_count",
    "weight_sum",
    "unique_users",
    "unique_sessions",
]

RECOMMENDATIONS_COLUMNS = [
    "item_id",
    "similar_item_id",
    "score",
    "rank",
    "source",
]

WIDGET_OUTPUT_COLUMNS = [
    "item_id",
    "similar_items_sku_list",
]
