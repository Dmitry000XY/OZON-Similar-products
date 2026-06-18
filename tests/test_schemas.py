"""Schema contract tests."""

from ozon_similar_products.data import schemas


def test_schema_contains_key_columns() -> None:
    """Core columns should be present in schema constants."""
    assert schemas.ITEM_ID_COLUMN == "item_id"
    assert schemas.EXTERNAL_SKU_COLUMN == "sku"
    assert schemas.ITEM_ID_COLUMN in schemas.PRODUCT_INFORMATION_COLUMNS
    assert schemas.ITEM_ID_COLUMN in schemas.RAW_EVENTS_COLUMNS
    assert "user_id" in schemas.RAW_EVENTS_COLUMNS
    assert "action_type" in schemas.RAW_EVENTS_COLUMNS


def test_daily_pair_stats_schema_columns_are_defined() -> None:
    """Daily pair stats contracts should be explicit and stable."""
    assert schemas.DAILY_PAIR_COUNTS_COLUMNS == [
        "pair_date",
        "item_id",
        "similar_item_id",
        "pair_count",
        "view_count",
        "click_count",
        "favorite_count",
        "to_cart_count",
    ]

    assert schemas.DAILY_PAIR_USER_KEYS_COLUMNS == [
        "pair_date",
        "item_id",
        "similar_item_id",
        "user_id",
    ]

    assert schemas.DAILY_PAIR_SESSION_KEYS_COLUMNS == [
        "pair_date",
        "item_id",
        "similar_item_id",
        "user_id",
        "session_index",
    ]
