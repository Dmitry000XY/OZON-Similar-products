"""DataFrame schemas and column contracts for MVP pipeline."""

ITEM_ID_COLUMN = "item_id"
EXTERNAL_SKU_COLUMN = "sku"


KNOWN_ACTION_TYPES = [
    "search",
    "view",
    "click",
    "to_cart",
    "favorite",
]

RAW_EVENTS_COLUMNS = [
    "user_id",
    "date",
    "timestamp",
    "action_type",
    "widget_name",
    "search_query",
    "item_id",
]

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

PRODUCT_INFORMATION_COLUMNS = [
    ITEM_ID_COLUMN,
    "name",
    "brand",
    "type",
    "category_id",
    "category_name",
]


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
