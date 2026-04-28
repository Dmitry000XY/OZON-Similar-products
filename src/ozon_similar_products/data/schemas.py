"""Column contracts for raw project data."""

ITEM_ID_COLUMN = "item_id"
EXTERNAL_SKU_COLUMN = "sku"

PRODUCT_INFORMATION_COLUMNS = [
    ITEM_ID_COLUMN,
    "name",
    "brand",
    "type",
    "category_id",
    "category_name",
]

USER_ACTION_COLUMNS = [
    "user_id",
    "date",
    "timestamp",
    "action_type",
    "widget_name",
    "search_query",
    ITEM_ID_COLUMN,
]

KNOWN_ACTION_TYPES = [
    "search",
    "view",
    "click",
    "to_cart",
    "favorite",
]
