"""DataFrame schemas and column contracts for MVP pipeline."""

import polars as pl

from ozon_similar_products.config import load_data_config

EXTERNAL_SKU_COLUMN = "sku"

_DATA_CONFIG = load_data_config()
_USER_ACTIONS = _DATA_CONFIG.get("user_actions", {})
_PRODUCTS = _DATA_CONFIG.get("product_information", {})

ITEM_ID_COLUMN = _PRODUCTS.get("id_column", "item_id")
KNOWN_ACTION_TYPES = _USER_ACTIONS.get("known_action_types", [])
RAW_EVENTS_COLUMNS = _USER_ACTIONS.get("expected_columns", [])

ITEM_SIGNAL_TYPES = [
    "view",
    "click",
    "favorite",
    "to_cart",
]

CLEAN_EVENTS_COLUMNS = [
    "user_id",
    "event_date",
    "timestamp",
    "action_type",
    "item_id",
    "search_query",
    "widget_name",
]

PRODUCT_INFORMATION_COLUMNS = _PRODUCTS.get("expected_columns", [])

SESSIONS_COLUMNS = [
    "user_id",
    "session_id",
    "event_date",
    "timestamp",
    "action_type",
    "item_id",
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

ACTION_TYPE_DISTRIBUTION_COLUMNS = [
    "action_type",
    "events_count",
    "event_share",
    "unique_users",
    "unique_items",
    "calibration_start",
    "calibration_end",
]

DAILY_ITEM_PAIRS_COLUMNS = [
    "pair_date",
    "item_id",
    "similar_item_id",
    "session_id",
    "user_id",
    "source_action_type",
    "target_action_type",
    "signal_type",
]

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

PAIR_SCORES_COLUMNS = [
    "item_id",
    "similar_item_id",
    "score",
    "pair_count",
    "view_count",
    "click_count",
    "favorite_count",
    "to_cart_count",
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

SESSIONS_SCHEMA = {
    "user_id": {"dtypes": (pl.Int64,), "nullable": False},
    "session_id": {"dtypes": (pl.String,), "nullable": False},
    "event_date": {"dtypes": (pl.Date, pl.String), "nullable": False},
    "timestamp": {"dtypes": (pl.Datetime,), "nullable": False},
    "action_type": {"dtypes": (pl.String,), "nullable": False},
    "item_id": {"dtypes": (pl.Int64,), "nullable": False},
}

PAIR_AGGREGATES_SCHEMA = {
    "item_id": {"dtypes": (pl.Int64,), "nullable": False},
    "similar_item_id": {"dtypes": (pl.Int64,), "nullable": False},
    "pair_count": {"dtypes": (pl.Int64,), "nullable": False},
    "view_count": {"dtypes": (pl.UInt32, pl.Int64), "nullable": False},
    "click_count": {"dtypes": (pl.UInt32, pl.Int64), "nullable": False},
    "favorite_count": {"dtypes": (pl.UInt32, pl.Int64), "nullable": False},
    "to_cart_count": {"dtypes": (pl.UInt32, pl.Int64), "nullable": False},
    "unique_users": {"dtypes": (pl.UInt32, pl.Int64), "nullable": False},
    "unique_sessions": {"dtypes": (pl.UInt32, pl.Int64), "nullable": False},
    "window_start": {"dtypes": (pl.String, pl.Date), "nullable": False},
    "window_end": {"dtypes": (pl.String, pl.Date), "nullable": False},
}

RECOMMENDATIONS_SCHEMA = {
    "item_id": {"dtypes": (pl.Int64,), "nullable": False},
    "similar_item_id": {"dtypes": (pl.Int64,), "nullable": False},
    "score": {"dtypes": (pl.Float64, pl.Float32), "nullable": False},
    "rank": {"dtypes": (pl.Int64,), "nullable": False},
    "source": {"dtypes": (pl.String,), "nullable": False},
}
