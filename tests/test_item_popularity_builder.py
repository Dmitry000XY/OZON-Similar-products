"""Tests for production item popularity builder."""

from datetime import date, datetime

import polars as pl
import pytest

from ozon_similar_products.data.validation import (
    validate_action_type_distribution,
    validate_item_popularity,
)
from ozon_similar_products.features.item_popularity import ItemPopularityBuilder


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
            },
            {
                "user_id": 1,
                "event_date": date(2024, 3, 1),
                "timestamp": datetime(2024, 3, 1, 10, 1),
                "action_type": "click",
                "item_id": 10,
                "search_query": None,
                "widget_name": "catalog",
            },
            {
                "user_id": 2,
                "event_date": date(2024, 3, 1),
                "timestamp": datetime(2024, 3, 1, 10, 2),
                "action_type": "favorite",
                "item_id": 10,
                "search_query": None,
                "widget_name": "product_card",
            },
            {
                "user_id": 3,
                "event_date": date(2024, 3, 1),
                "timestamp": datetime(2024, 3, 1, 10, 3),
                "action_type": "to_cart",
                "item_id": 20,
                "search_query": None,
                "widget_name": "product_card",
            },
        ]
    )

    popularity = ItemPopularityBuilder().build_item_popularity(events).sort("item_id")

    validate_item_popularity(popularity)
    assert popularity.columns == [
        "item_id",
        "events_count",
        "unique_users",
        "views_count",
        "clicks_count",
        "favorites_count",
        "to_cart_count",
    ]
    assert popularity["item_id"].to_list() == [10, 20]
    assert popularity["events_count"].to_list() == [3, 1]
    assert popularity["unique_users"].to_list() == [2, 1]
    assert popularity["views_count"].to_list() == [1, 0]
    assert popularity["clicks_count"].to_list() == [1, 0]
    assert popularity["favorites_count"].to_list() == [1, 0]
    assert popularity["to_cart_count"].to_list() == [0, 1]


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
            },
            {
                "user_id": 1,
                "event_date": date(2024, 3, 1),
                "timestamp": datetime(2024, 3, 1, 10, 1),
                "action_type": "search",
                "item_id": 10,
                "search_query": "phone",
                "widget_name": "search_bar",
            },
            {
                "user_id": 2,
                "event_date": date(2024, 3, 1),
                "timestamp": datetime(2024, 3, 1, 10, 2),
                "action_type": "view",
                "item_id": None,
                "search_query": None,
                "widget_name": "catalog",
            },
            {
                "user_id": 2,
                "event_date": date(2024, 3, 1),
                "timestamp": datetime(2024, 3, 1, 10, 3),
                "action_type": "view",
                "item_id": 10,
                "search_query": None,
                "widget_name": "catalog",
            },
        ]
    )

    popularity = ItemPopularityBuilder().build_item_popularity(events)

    assert popularity["item_id"].to_list() == [10]
    assert popularity["events_count"].to_list() == [1]
    assert popularity["views_count"].to_list() == [1]


def test_item_popularity_builder_builds_action_type_distribution() -> None:
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
            },
            {
                "user_id": 2,
                "event_date": date(2024, 3, 1),
                "timestamp": datetime(2024, 3, 1, 10, 1),
                "action_type": "view",
                "item_id": 20,
                "search_query": None,
                "widget_name": "catalog",
            },
            {
                "user_id": 3,
                "event_date": date(2024, 3, 1),
                "timestamp": datetime(2024, 3, 1, 10, 2),
                "action_type": "to_cart",
                "item_id": 20,
                "search_query": None,
                "widget_name": "product_card",
            },
        ]
    )

    distribution = ItemPopularityBuilder().build_action_type_calibration_stats(
        events,
        calibration_start="2024-03-01",
        calibration_end="2024-03-01",
    ).sort("action_type")

    validate_action_type_distribution(distribution)
    assert distribution["action_type"].to_list() == ["to_cart", "view"]
    assert distribution["events_count"].to_list() == [1, 2]
    assert distribution["unique_items"].to_list() == [1, 2]
    assert distribution["event_share"].to_list() == pytest.approx([1 / 3, 2 / 3])


def test_item_popularity_builder_builds_popularity_by_widget_name() -> None:
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
            },
            {
                "user_id": 2,
                "event_date": date(2024, 3, 1),
                "timestamp": datetime(2024, 3, 1, 10, 1),
                "action_type": "click",
                "item_id": 10,
                "search_query": None,
                "widget_name": "catalog",
            },
            {
                "user_id": 3,
                "event_date": date(2024, 3, 1),
                "timestamp": datetime(2024, 3, 1, 10, 2),
                "action_type": "favorite",
                "item_id": 10,
                "search_query": None,
                "widget_name": "product_card",
            },
        ]
    )

    popularity_by_widget_name = ItemPopularityBuilder().build_item_popularity_by_widget_name(events).sort(
        ["item_id", "widget_name"]
    )

    assert popularity_by_widget_name["item_id"].to_list() == [10, 10]
    assert popularity_by_widget_name["widget_name"].to_list() == ["catalog", "product_card"]
    assert popularity_by_widget_name["events_count"].to_list() == [2, 1]
    assert popularity_by_widget_name["unique_users"].to_list() == [2, 1]
    assert popularity_by_widget_name["views_count"].to_list() == [1, 0]
    assert popularity_by_widget_name["clicks_count"].to_list() == [1, 0]
    assert popularity_by_widget_name["favorites_count"].to_list() == [0, 1]


def test_item_popularity_builder_builds_popularity_by_action_type() -> None:
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
            },
            {
                "user_id": 2,
                "event_date": date(2024, 3, 1),
                "timestamp": datetime(2024, 3, 1, 10, 1),
                "action_type": "view",
                "item_id": 10,
                "search_query": None,
                "widget_name": "catalog",
            },
            {
                "user_id": 2,
                "event_date": date(2024, 3, 1),
                "timestamp": datetime(2024, 3, 1, 10, 2),
                "action_type": "click",
                "item_id": 10,
                "search_query": None,
                "widget_name": "catalog",
            },
            {
                "user_id": 3,
                "event_date": date(2024, 3, 1),
                "timestamp": datetime(2024, 3, 1, 10, 3),
                "action_type": "search",
                "item_id": 10,
                "search_query": "phone",
                "widget_name": "search_bar",
            },
        ]
    )

    popularity_by_action_type = (
        ItemPopularityBuilder()
        .build_item_popularity_by_action_type(events)
        .sort(["item_id", "action_type"])
    )

    assert popularity_by_action_type.columns == [
        "item_id",
        "action_type",
        "events_count",
        "unique_users",
    ]
    assert popularity_by_action_type["item_id"].to_list() == [10, 10]
    assert popularity_by_action_type["action_type"].to_list() == ["click", "view"]
    assert popularity_by_action_type["events_count"].to_list() == [1, 2]
    assert popularity_by_action_type["unique_users"].to_list() == [1, 2]


def test_item_popularity_builder_builds_popularity_by_date() -> None:
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
            },
            {
                "user_id": 2,
                "event_date": date(2024, 3, 1),
                "timestamp": datetime(2024, 3, 1, 10, 1),
                "action_type": "click",
                "item_id": 10,
                "search_query": None,
                "widget_name": "catalog",
            },
            {
                "user_id": 1,
                "event_date": date(2024, 3, 2),
                "timestamp": datetime(2024, 3, 2, 10, 0),
                "action_type": "to_cart",
                "item_id": 10,
                "search_query": None,
                "widget_name": "product_card",
            },
            {
                "user_id": 3,
                "event_date": date(2024, 3, 2),
                "timestamp": datetime(2024, 3, 2, 10, 1),
                "action_type": "view",
                "item_id": 20,
                "search_query": None,
                "widget_name": "catalog",
            },
        ]
    )

    popularity_by_date = ItemPopularityBuilder().build_item_popularity_by_date(events).sort(
        ["event_date", "item_id"]
    )

    assert popularity_by_date.columns == [
        "event_date",
        "item_id",
        "events_count",
        "unique_users",
        "views_count",
        "clicks_count",
        "favorites_count",
        "to_cart_count",
    ]
    assert popularity_by_date["event_date"].to_list() == [
        date(2024, 3, 1),
        date(2024, 3, 2),
        date(2024, 3, 2),
    ]
    assert popularity_by_date["item_id"].to_list() == [10, 10, 20]
    assert popularity_by_date["events_count"].to_list() == [2, 1, 1]
    assert popularity_by_date["unique_users"].to_list() == [2, 1, 1]
    assert popularity_by_date["views_count"].to_list() == [1, 0, 1]
    assert popularity_by_date["clicks_count"].to_list() == [1, 0, 0]
    assert popularity_by_date["favorites_count"].to_list() == [0, 0, 0]
    assert popularity_by_date["to_cart_count"].to_list() == [0, 1, 0]


def test_item_popularity_builder_accepts_lazy_frame() -> None:
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
            }
        ]
    )

    popularity = ItemPopularityBuilder().build_item_popularity(events.lazy())

    validate_item_popularity(popularity)
    assert popularity["item_id"].to_list() == [10]
    assert popularity["events_count"].to_list() == [1]
    assert popularity["unique_users"].to_list() == [1]
    assert popularity["views_count"].to_list() == [1]
