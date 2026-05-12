"""Tests for SessionBuilder."""

from datetime import datetime

import polars as pl
import pytest

from ozon_similar_products.data.validation import validate_sessions
from ozon_similar_products.preprocessing.build_sessions import SessionBuilder


def _clean_events(rows: list[dict]) -> pl.DataFrame:
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


def _event(
    user_id: int | None,
    timestamp: datetime | None,
    item_id: int | None,
    action_type: str = "view",
) -> dict:
    return {
        "user_id": user_id,
        "event_date": timestamp.date() if timestamp is not None else None,
        "timestamp": timestamp,
        "action_type": action_type,
        "item_id": item_id,
        "search_query": None,
        "widget_name": "test_widget",
    }


def test_session_builder_splits_user_timeline_by_timeout() -> None:
    events = _clean_events(
        [
            _event(1, datetime(2024, 3, 1, 0, 10), 11, "click"),
            _event(1, datetime(2024, 3, 1, 0, 0), 10, "view"),
            _event(1, datetime(2024, 3, 1, 0, 45), 12, "to_cart"),
            _event(2, datetime(2024, 3, 1, 0, 5), 20, "favorite"),
        ]
    )

    sessions = SessionBuilder(timeout_minutes=30).transform_day(events)

    validate_sessions(sessions)
    user_one = sessions.filter(pl.col("user_id") == 1)
    assert user_one["item_id"].to_list() == [10, 11, 12]
    assert user_one["session_id"].n_unique() == 2
    assert user_one["session_id"].to_list()[0] == user_one["session_id"].to_list()[1]
    assert user_one["session_id"].to_list()[2] != user_one["session_id"].to_list()[0]


def test_session_builder_preserves_action_type_without_action_weight() -> None:
    events = _clean_events(
        [
            _event(1, datetime(2024, 3, 1, 0, 0), 10, "view"),
            _event(1, datetime(2024, 3, 1, 0, 5), 11, "to_cart"),
        ]
    )

    sessions = SessionBuilder().transform_day(events)

    assert sessions.columns == [
        "user_id",
        "session_id",
        "event_date",
        "timestamp",
        "action_type",
        "item_id",
    ]
    assert "action_weight" not in sessions.columns
    assert sessions["action_type"].to_list() == ["view", "to_cart"]


def test_session_builder_drops_rows_without_item_or_time_context() -> None:
    events = _clean_events(
        [
            _event(1, datetime(2024, 3, 1, 0, 0), 10),
            _event(1, datetime(2024, 3, 1, 0, 1), None),
            _event(None, datetime(2024, 3, 1, 0, 2), 12),
            _event(1, None, 13),
        ]
    )

    sessions = SessionBuilder().transform_day(events)

    assert sessions["item_id"].to_list() == [10]


def test_session_builder_accepts_lazy_frame() -> None:
    events = _clean_events(
        [
            _event(1, datetime(2024, 3, 1, 0, 0), 10),
            _event(1, datetime(2024, 3, 1, 0, 5), 11),
        ]
    )

    sessions = SessionBuilder().transform_day(events.lazy())

    assert sessions.height == 2
    assert sessions["session_id"].n_unique() == 1


def test_session_builder_handles_empty_input() -> None:
    sessions = SessionBuilder().transform_day(_clean_events([]))

    validate_sessions(sessions)
    assert sessions.is_empty()
    assert sessions.columns == [
        "user_id",
        "session_id",
        "event_date",
        "timestamp",
        "action_type",
        "item_id",
    ]


def test_session_builder_transform_window_concatenates_daily_sessions() -> None:
    day_one = _clean_events([_event(1, datetime(2024, 3, 1, 0, 0), 10)])
    day_two = _clean_events([_event(1, datetime(2024, 3, 2, 0, 0), 20)])

    sessions = SessionBuilder().transform_window([day_one, day_two])

    validate_sessions(sessions)
    assert sessions.height == 2
    assert sessions["event_date"].cast(pl.String).to_list() == [
        "2024-03-01",
        "2024-03-02",
    ]


def test_session_builder_can_be_created_from_config() -> None:
    builder = SessionBuilder.from_config({"pipeline": {"session_timeout_minutes": 15}})
    assert builder.timeout_minutes == 15


def test_session_builder_rejects_invalid_timeout() -> None:
    with pytest.raises(ValueError, match="timeout_minutes"):
        SessionBuilder(timeout_minutes=0)


def test_session_builder_continues_session_across_day_boundary() -> None:
    """Session should continue across midnight if within timeout."""
    day_one = _clean_events(
        [
            _event(1, datetime(2024, 3, 1, 23, 50), 10),
            _event(1, datetime(2024, 3, 1, 23, 55), 11),
        ]
    )
    day_two = _clean_events(
        [
            _event(1, datetime(2024, 3, 2, 0, 5), 12),  # 10 minutes after last event
        ]
    )

    sessions = SessionBuilder(timeout_minutes=30).transform_window([day_one, day_two])

    validate_sessions(sessions)
    # All events should be in the same session
    assert sessions.height == 3
    assert sessions["session_id"].n_unique() == 1
    assert sessions["item_id"].to_list() == [10, 11, 12]


def test_session_builder_splits_session_at_day_boundary_if_timeout_exceeded() -> None:
    """Session should split at midnight if timeout is exceeded."""
    day_one = _clean_events(
        [
            _event(1, datetime(2024, 3, 1, 23, 50), 10),
        ]
    )
    day_two = _clean_events(
        [
            _event(1, datetime(2024, 3, 2, 0, 25), 11),  # 35 minutes later (> 30 min timeout)
        ]
    )

    sessions = SessionBuilder(timeout_minutes=30).transform_window([day_one, day_two])

    validate_sessions(sessions)
    # Events should be in different sessions
    assert sessions.height == 2
    assert sessions["session_id"].n_unique() == 2
