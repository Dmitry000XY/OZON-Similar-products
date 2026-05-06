"""Tests for item popularity features."""

from datetime import datetime

import polars as pl

from ozon_similar_products.features.item_popularity import weighted_item_popularity


def test_weighted_item_popularity_uses_direct_item_actions_only() -> None:
    """Search rows and null item ids should not affect direct item popularity."""
    events = pl.DataFrame(
        {
            "user_id": [1, 1, 2, 3, 3],
            "action_type": ["view", "click", "search", "to_cart", "view"],
            "item_id": [10, 10, 10, None, 20],
            "timestamp": [
                datetime(2024, 3, 1, 10, 0),
                datetime(2024, 3, 1, 10, 1),
                datetime(2024, 3, 1, 10, 2),
                datetime(2024, 3, 1, 10, 3),
                datetime(2024, 3, 1, 10, 4),
            ],
        }
    )
    weights = {"search": 0.2, "view": 1.0, "click": 2.0, "favorite": 2.5, "to_cart": 4.0}

    popularity = weighted_item_popularity(events, weights).sort("item_id")

    assert popularity["item_id"].to_list() == [10, 20]
    assert popularity["events"].to_list() == [2, 1]
    assert popularity["weighted_events"].to_list() == [3.0, 1.0]
    assert popularity["unique_users"].to_list() == [1, 1]
