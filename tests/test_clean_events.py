"""Tests for clean events preprocessing."""

from datetime import date, datetime

import polars as pl

from ozon_similar_products.data import schemas
from ozon_similar_products.data.validation import validate_clean_events
from ozon_similar_products.preprocessing.clean_events import EventCleaner

ITEM_ACTION_TYPES = ["view", "click", "favorite", "to_cart"]


def _cleaner() -> EventCleaner:
    return EventCleaner(
        item_action_types=ITEM_ACTION_TYPES,
    )


def _event(**overrides: object) -> dict[str, object]:
    row: dict[str, object] = {
        "user_id": 1,
        "date": date(2024, 3, 1),
        "timestamp": datetime(2024, 3, 1, 10, 0),
        "action_type": "view",
        "widget_name": "search_catalog_listing",
        "search_query": None,
        "item_id": 10,
    }
    row.update(overrides)
    return row


def _raw_events(rows: list[dict[str, object]]) -> pl.DataFrame:
    return pl.DataFrame(
        rows,
        schema={
            "user_id": pl.Int64,
            "date": pl.Date,
            "timestamp": pl.Datetime,
            "action_type": pl.String,
            "widget_name": pl.String,
            "search_query": pl.String,
            "item_id": pl.Int64,
        },
        strict=False,
    )


def test_transform_day_keeps_only_direct_item_events() -> None:
    duplicate_view = _event()
    events = _raw_events(
        [
            duplicate_view,
            duplicate_view,
            _event(
                timestamp=datetime(2024, 3, 1, 10, 1),
                action_type="click",
                widget_name=None,
                search_query="milk",
                item_id=11,
            ),
            _event(
                timestamp=datetime(2024, 3, 1, 10, 2),
                action_type="favorite",
                item_id=12,
            ),
            _event(
                timestamp=datetime(2024, 3, 1, 10, 3),
                action_type="to_cart",
                item_id=13,
            ),
            _event(
                timestamp=datetime(2024, 3, 1, 10, 4),
                action_type="search",
                search_query="milk",
                item_id=None,
            ),
            _event(
                timestamp=datetime(2024, 3, 1, 10, 5),
                action_type="view",
                item_id=None,
            ),
        ]
    )

    cleaned = _cleaner().transform_day(events)

    validate_clean_events(cleaned)
    assert cleaned.columns == schemas.CLEAN_EVENTS_COLUMNS
    assert cleaned["action_type"].to_list() == ["view", "click", "favorite", "to_cart"]
    assert cleaned["item_id"].to_list() == [10, 11, 12, 13]
    assert cleaned["widget_name"].to_list()[1] == "unknown"
    assert cleaned["search_query"].to_list()[1] == "milk"


def test_transform_day_drops_critical_nulls() -> None:
    events = _raw_events(
        [
            _event(user_id=None, item_id=10),
            _event(timestamp=None, item_id=11),
            _event(action_type=None, item_id=12),
            _event(item_id=13),
        ]
    )

    cleaned = _cleaner().transform_day(events)

    assert cleaned.height == 1
    assert cleaned["item_id"].to_list() == [13]


def test_transform_day_parses_string_timestamps() -> None:
    events = pl.DataFrame(
        [
            {
                "user_id": 1,
                "date": "2024-03-01",
                "timestamp": "2024-03-01 10:00:00",
                "action_type": "view",
                "widget_name": "catalog",
                "search_query": None,
                "item_id": 10,
            }
        ],
        schema={
            "user_id": pl.Int64,
            "date": pl.String,
            "timestamp": pl.String,
            "action_type": pl.String,
            "widget_name": pl.String,
            "search_query": pl.String,
            "item_id": pl.Int64,
        },
    )

    cleaned = _cleaner().transform_day(events)

    assert cleaned["timestamp"].to_list() == [datetime(2024, 3, 1, 10, 0)]
    assert cleaned["event_date"].to_list() == [date(2024, 3, 1)]


def test_transform_day_accepts_lazy_frame() -> None:
    events = _raw_events([_event(item_id=10)])

    cleaned = _cleaner().transform_day(events.lazy())

    assert cleaned["item_id"].to_list() == [10]


def test_transform_day_preserves_filtered_input_order() -> None:
    events = _raw_events(
        [
            _event(
                user_id=2,
                timestamp=datetime(2024, 3, 1, 10, 5),
                action_type="click",
                item_id=20,
            ),
            _event(
                user_id=1,
                timestamp=datetime(2024, 3, 1, 10, 0),
                action_type="view",
                item_id=10,
            ),
        ]
    )

    cleaned = _cleaner().transform_day(events)

    assert cleaned["user_id"].to_list() == [2, 1]
    assert cleaned["item_id"].to_list() == [20, 10]


def test_transform_window_concatenates_cleaned_days() -> None:
    first_day = _raw_events([_event(item_id=10)])
    second_day = _raw_events(
        [
            _event(
                date=date(2024, 3, 2),
                timestamp=datetime(2024, 3, 2, 10, 0),
                action_type="click",
                item_id=20,
            )
        ]
    )

    cleaned = _cleaner().transform_window([first_day, second_day])

    validate_clean_events(cleaned)
    assert cleaned.height == 2
    assert cleaned["item_id"].to_list() == [10, 20]


def test_transform_window_empty_input_returns_clean_event_columns() -> None:
    cleaned = _cleaner().transform_window([])

    validate_clean_events(cleaned)
    assert cleaned.is_empty()
    assert cleaned.columns == schemas.CLEAN_EVENTS_COLUMNS
