"""Schema contract tests."""

from ozon_similar_products.data import schemas


def test_schema_contains_key_columns() -> None:
    """Core columns should be present in schema constants."""
    assert schemas.ITEM_ID_COLUMN == "item_id"
    assert schemas.EXTERNAL_SKU_COLUMN == "sku"
    assert "item_id" in schemas.PRODUCT_INFORMATION_COLUMNS
    assert "item_id" in schemas.USER_ACTION_COLUMNS
    assert "user_id" in schemas.USER_ACTION_COLUMNS
    assert "action_type" in schemas.USER_ACTION_COLUMNS
