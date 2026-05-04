"""Data config synchronization tests."""

from ozon_similar_products.config import load_data_config
from ozon_similar_products.data import schemas


def test_data_config_matches_schemas() -> None:
    """Data config column lists should align with schemas."""
    data_config = load_data_config()
    product_config = data_config["product_information"]
    user_actions_config = data_config["user_actions"]

    assert product_config["expected_columns"] == schemas.PRODUCT_INFORMATION_COLUMNS
    assert product_config["id_column"] == schemas.ITEM_ID_COLUMN
    assert user_actions_config["expected_columns"] == schemas.RAW_EVENTS_COLUMNS
    assert user_actions_config["known_action_types"] == schemas.KNOWN_ACTION_TYPES
