"""Tests for production item popularity builder."""

from datetime import date, datetime

import polars as pl
import pytest

from ozon_similar_products.data.validation import validate_item_popularity
from ozon_similar_products.features.item_popularity import ItemPopularityBuilder

ACTION_WEIGHTS = {
    "view": 1.0,
    "click": 2.0,
    "favorite": 2.5,
    "to_cart": 4.0,
}


def _clean_events(rows: list[dict]) -> pl.DataFrame:
    """Build a clean-events DataFrame for item popularity tests."""
    return pl.DataFrame(
        rows,
        schema={
            "user_id": pl.Int64,
            "event_date": pl.Date,
            "timestamp": pl.Datetime,
            "action_type": pl.String,
            "item_id": pl.Int64,
            "search_query": pl.String,
            "widget_name": pl.String,
            "action_weight": pl.Float64,
        },
    )


def test_item_popularity_builder_counts_required_metrics() -> None:
    events = _clean_events(
        [
            {
                "user_id": 1,
                "event_date": date(2024, 3, 1),
                "timestamp": datetime(2024, 3, 1, 10, 0),
                "action_type": "view",
                "item_id": 10,
                "search_query": None,
                "widget_name": "catalog",
                "action_weight": 1.0,
            },
            {
                "user_id": 1,
                "event_date": date(2024, 3, 1),
                "timestamp": datetime(2024, 3, 1, 10, 1),
                "action_type": "click",
                "item_id": 10,
                "search_query": None,
                "widget_name": "catalog",
                "action_weight": 2.0,
            },
            {
                "user_id": 2,
                "event_date": date(2024, 3, 1),
                "timestamp": datetime(2024, 3, 1, 10, 2),
                "action_type": "favorite",
                "item_id": 10,
                "search_query": None,
                "widget_name": "product_card",
                "action_weight": 2.5,
            },
            {
                "user_id": 3,
                "event_date": date(2024, 3, 1),
                "timestamp": datetime(2024, 3, 1, 10, 3),
                "action_type": "to_cart",
                "item_id": 20,
                "search_query": None,
                "widget_name": "product_card",
                "action_weight": 4.0,
            },
        ]
    )

    builder = ItemPopularityBuilder(action_weights=ACTION_WEIGHTS)
    popularity = builder.transform_day(events).sort("item_id")

    validate_item_popularity(popularity)

    assert popularity["item_id"].to_list() == [10, 20]
    assert popularity["events_count"].to_list() == [3, 1]
    assert popularity["unique_users"].to_list() == [2, 1]
    assert popularity["views_count"].to_list() == [1, 0]
    assert popularity["clicks_count"].to_list() == [1, 0]
    assert popularity["favorites_count"].to_list() == [1, 0]
    assert popularity["to_cart_count"].to_list() == [0, 1]
    assert popularity["weighted_events"].to_list() == [5.5, 4.0]


def test_item_popularity_builder_ignores_search_and_null_item_id() -> None:
    events = _clean_events(
        [
            {
                "user_id": 1,
                "event_date": date(2024, 3, 1),
                "timestamp": datetime(2024, 3, 1, 10, 0),
                "action_type": "search",
                "item_id": None,
                "search_query": "phone",
                "widget_name": "search_bar",
                "action_weight": 0.0,
            },
            {
                "user_id": 1,
                "event_date": date(2024, 3, 1),
                "timestamp": datetime(2024, 3, 1, 10, 1),
                "action_type": "search",
                "item_id": 10,
                "search_query": "phone",
                "widget_name": "search_bar",
                "action_weight": 0.0,
            },
            {
                "user_id": 2,
                "event_date": date(2024, 3, 1),
                "timestamp": datetime(2024, 3, 1, 10, 2),
                "action_type": "view",
                "item_id": None,
                "search_query": None,
                "widget_name": "catalog",
                "action_weight": 1.0,
            },
            {
                "user_id": 2,
                "event_date": date(2024, 3, 1),
                "timestamp": datetime(2024, 3, 1, 10, 3),
                "action_type": "view",
                "item_id": 10,
                "search_query": None,
                "widget_name": "catalog",
                "action_weight": 1.0,
            },
        ]
    )

    builder = ItemPopularityBuilder(action_weights=ACTION_WEIGHTS)
    popularity = builder.transform_day(events)

    assert popularity["item_id"].to_list() == [10]
    assert popularity["events_count"].to_list() == [1]
    assert popularity["views_count"].to_list() == [1]
    assert popularity["weighted_events"].to_list() == [1.0]


def test_item_popularity_builder_counts_unique_users_not_events() -> None:
    events = _clean_events(
        [
            {
                "user_id": 1,
                "event_date": date(2024, 3, 1),
                "timestamp": datetime(2024, 3, 1, 10, minute),
                "action_type": "view",
                "item_id": 10,
                "search_query": None,
                "widget_name": "catalog",
                "action_weight": 1.0,
            }
            for minute in range(5)
        ]
        + [
            {
                "user_id": 2,
                "event_date": date(2024, 3, 1),
                "timestamp": datetime(2024, 3, 1, 10, 10),
                "action_type": "click",
                "item_id": 10,
                "search_query": None,
                "widget_name": "catalog",
                "action_weight": 2.0,
            }
        ]
    )

    builder = ItemPopularityBuilder(action_weights=ACTION_WEIGHTS)
    popularity = builder.transform_day(events)

    assert popularity["events_count"].to_list() == [6]
    assert popularity["unique_users"].to_list() == [2]
    assert popularity["weighted_events"].to_list() == [7.0]


def test_item_popularity_builder_returns_valid_empty_result() -> None:
    events = _clean_events(
        [
            {
                "user_id": 1,
                "event_date": date(2024, 3, 1),
                "timestamp": datetime(2024, 3, 1, 10, 0),
                "action_type": "search",
                "item_id": None,
                "search_query": "phone",
                "widget_name": "search_bar",
                "action_weight": 0.0,
            }
        ]
    )

    builder = ItemPopularityBuilder(action_weights=ACTION_WEIGHTS)
    popularity = builder.transform_day(events)

    validate_item_popularity(popularity)

    assert popularity.is_empty()
    assert popularity.columns == [
        "item_id",
        "events_count",
        "unique_users",
        "views_count",
        "clicks_count",
        "favorites_count",
        "to_cart_count",
        "weighted_events",
    ]


def test_item_popularity_builder_validates_action_weights() -> None:
    events = _clean_events(
        [
            {
                "user_id": 1,
                "event_date": date(2024, 3, 1),
                "timestamp": datetime(2024, 3, 1, 10, 0),
                "action_type": "view",
                "item_id": 10,
                "search_query": None,
                "widget_name": "catalog",
                "action_weight": 1.0,
            }
        ]
    )

    builder = ItemPopularityBuilder(
        action_weights={"view": 1.0},
        item_action_types=("view", "click"),
    )

    with pytest.raises(ValueError, match="Missing action weights"):
        builder.transform_day(events)


def test_item_popularity_builder_build_accepts_lazy_frame() -> None:
    events = _clean_events(
        [
            {
                "user_id": 1,
                "event_date": date(2024, 3, 1),
                "timestamp": datetime(2024, 3, 1, 10, 0),
                "action_type": "view",
                "item_id": 10,
                "search_query": None,
                "widget_name": "catalog",
                "action_weight": 1.0,
            }
        ]
    )

    builder = ItemPopularityBuilder(action_weights=ACTION_WEIGHTS)
    popularity = builder.build(events.lazy())

    validate_item_popularity(popularity)

    assert popularity["item_id"].to_list() == [10]
    assert popularity["events_count"].to_list() == [1]
    assert popularity["weighted_events"].to_list() == [1.0]


def test_item_popularity_builder_aggregate_window_is_not_implemented() -> None:
    builder = ItemPopularityBuilder(action_weights=ACTION_WEIGHTS)

    with pytest.raises(NotImplementedError, match="unique_users cannot be summed"):
        builder.aggregate_window([])
