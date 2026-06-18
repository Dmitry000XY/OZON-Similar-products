from datetime import date, datetime

import polars as pl
import pytest

from ozon_similar_products.data import schemas
from ozon_similar_products.retrieval.build_pairs import DailyPairStats, ItemPairBuilder


def _sessions(rows: list[dict]) -> pl.DataFrame:
    return pl.DataFrame(rows)


def test_build_pairs_uses_target_signal_type() -> None:
    sessions = _sessions(
        [
            {
                "user_id": 1,
                "session_index": 1,
                "session_start_date": date(2026, 5, 1),
                "event_date": date(2026, 5, 1),
                "timestamp": datetime(2026, 5, 1, 10, 0),
                "action_type": "view",
                "item_id": 10,
            },
            {
                "user_id": 1,
                "session_index": 1,
                "session_start_date": date(2026, 5, 1),
                "event_date": date(2026, 5, 1),
                "timestamp": datetime(2026, 5, 1, 10, 1),
                "action_type": "to_cart",
                "item_id": 20,
            },
            {
                "user_id": 1,
                "session_index": 1,
                "session_start_date": date(2026, 5, 1),
                "event_date": date(2026, 5, 1),
                "timestamp": datetime(2026, 5, 1, 10, 2),
                "action_type": "view",
                "item_id": 30,
            },
        ]
    )

    result = ItemPairBuilder().transform_day(sessions)

    assert result.height == 6
    assert result.filter((pl.col("item_id") == 10) & (pl.col("similar_item_id") == 20))[0, "signal_type"] == "to_cart"
    assert result.filter((pl.col("item_id") == 30) & (pl.col("similar_item_id") == 20))[0, "signal_type"] == "to_cart"
    assert result.filter((pl.col("item_id") == 20) & (pl.col("similar_item_id") == 10))[0, "signal_type"] == "view"
    assert result.filter(pl.col("item_id") == pl.col("similar_item_id")).is_empty()
    assert "session_id" not in result.columns
    assert "session_index" in result.columns
    assert result["session_index"].to_list() == [1, 1, 1, 1, 1, 1]


def test_build_daily_pair_stats_matches_raw_pair_semantics() -> None:
    sessions = _sessions(
        [
            {
                "user_id": 1,
                "session_index": 1,
                "session_start_date": date(2026, 5, 1),
                "event_date": date(2026, 5, 1),
                "timestamp": datetime(2026, 5, 1, 10, 0),
                "action_type": "view",
                "item_id": 10,
            },
            {
                "user_id": 1,
                "session_index": 1,
                "session_start_date": date(2026, 5, 1),
                "event_date": date(2026, 5, 1),
                "timestamp": datetime(2026, 5, 1, 10, 1),
                "action_type": "to_cart",
                "item_id": 20,
            },
            {
                "user_id": 1,
                "session_index": 2,
                "session_start_date": date(2026, 5, 1),
                "event_date": date(2026, 5, 1),
                "timestamp": datetime(2026, 5, 1, 11, 0),
                "action_type": "view",
                "item_id": 10,
            },
            {
                "user_id": 1,
                "session_index": 2,
                "session_start_date": date(2026, 5, 1),
                "event_date": date(2026, 5, 1),
                "timestamp": datetime(2026, 5, 1, 11, 1),
                "action_type": "click",
                "item_id": 20,
            },
            {
                "user_id": 2,
                "session_index": 1,
                "session_start_date": date(2026, 5, 1),
                "event_date": date(2026, 5, 1),
                "timestamp": datetime(2026, 5, 1, 12, 0),
                "action_type": "view",
                "item_id": 10,
            },
            {
                "user_id": 2,
                "session_index": 1,
                "session_start_date": date(2026, 5, 1),
                "event_date": date(2026, 5, 1),
                "timestamp": datetime(2026, 5, 1, 12, 1),
                "action_type": "view",
                "item_id": 20,
            },
        ]
    )

    stats = ItemPairBuilder().build_daily_pair_stats(sessions)

    assert isinstance(stats, DailyPairStats)
    assert stats.raw_pair_rows == 6

    assert stats.counts.columns == schemas.DAILY_PAIR_COUNTS_COLUMNS
    assert stats.user_keys.columns == schemas.DAILY_PAIR_USER_KEYS_COLUMNS
    assert stats.session_keys.columns == schemas.DAILY_PAIR_SESSION_KEYS_COLUMNS

    row = stats.counts.filter(
        (pl.col("item_id") == 10)
        & (pl.col("similar_item_id") == 20)
    ).row(0, named=True)

    assert row["pair_count"] == 3
    assert row["view_count"] == 1
    assert row["click_count"] == 1
    assert row["favorite_count"] == 0
    assert row["to_cart_count"] == 1

    user_keys = stats.user_keys.filter(
        (pl.col("item_id") == 10)
        & (pl.col("similar_item_id") == 20)
    )
    assert user_keys.height == 2
    assert set(user_keys["user_id"].to_list()) == {1, 2}

    session_keys = stats.session_keys.filter(
        (pl.col("item_id") == 10)
        & (pl.col("similar_item_id") == 20)
    )
    assert session_keys.height == 3


def test_build_daily_pair_stats_returns_empty_contracts_for_no_pairs() -> None:
    sessions = _sessions(
        [
            {
                "user_id": 1,
                "session_index": 1,
                "session_start_date": date(2026, 5, 1),
                "event_date": date(2026, 5, 1),
                "timestamp": datetime(2026, 5, 1, 10, 0),
                "action_type": "view",
                "item_id": 10,
            }
        ]
    )

    stats = ItemPairBuilder().build_daily_pair_stats(sessions)

    assert stats.raw_pair_rows == 0
    assert stats.counts.is_empty()
    assert stats.user_keys.is_empty()
    assert stats.session_keys.is_empty()
    assert stats.counts.columns == schemas.DAILY_PAIR_COUNTS_COLUMNS
    assert stats.user_keys.columns == schemas.DAILY_PAIR_USER_KEYS_COLUMNS
    assert stats.session_keys.columns == schemas.DAILY_PAIR_SESSION_KEYS_COLUMNS


def test_build_pairs_collapses_repeated_item_to_strongest_signal() -> None:
    sessions = _sessions(
        [
            {
                "user_id": 1,
                "session_index": 1,
                "session_start_date": date(2026, 5, 1),
                "event_date": date(2026, 5, 1),
                "timestamp": datetime(2026, 5, 1, 10, 0),
                "action_type": "view",
                "item_id": 10,
            },
            {
                "user_id": 1,
                "session_index": 1,
                "session_start_date": date(2026, 5, 1),
                "event_date": date(2026, 5, 1),
                "timestamp": datetime(2026, 5, 1, 10, 1),
                "action_type": "to_cart",
                "item_id": 10,
            },
            {
                "user_id": 1,
                "session_index": 1,
                "session_start_date": date(2026, 5, 1),
                "event_date": date(2026, 5, 1),
                "timestamp": datetime(2026, 5, 1, 10, 2),
                "action_type": "click",
                "item_id": 20,
            },
        ]
    )

    result = ItemPairBuilder().transform_day(sessions)

    assert result.height == 2
    assert result.filter((pl.col("item_id") == 20) & (pl.col("similar_item_id") == 10))[0, "signal_type"] == "to_cart"
    assert result.filter((pl.col("item_id") == 10) & (pl.col("similar_item_id") == 20))[0, "signal_type"] == "click"


def test_build_pairs_skips_single_item_and_too_long_sessions() -> None:
    sessions = _sessions(
        [
            {
                "user_id": 1,
                "session_index": 1,
                "session_start_date": date(2026, 5, 1),
                "event_date": date(2026, 5, 1),
                "timestamp": datetime(2026, 5, 1, 10, 0),
                "action_type": "view",
                "item_id": 10,
            },
            {
                "user_id": 2,
                "session_index": 1,
                "session_start_date": date(2026, 5, 1),
                "event_date": date(2026, 5, 1),
                "timestamp": datetime(2026, 5, 1, 10, 0),
                "action_type": "view",
                "item_id": 100,
            },
            {
                "user_id": 2,
                "session_index": 1,
                "session_start_date": date(2026, 5, 1),
                "event_date": date(2026, 5, 1),
                "timestamp": datetime(2026, 5, 1, 10, 1),
                "action_type": "view",
                "item_id": 200,
            },
            {
                "user_id": 2,
                "session_index": 1,
                "session_start_date": date(2026, 5, 1),
                "event_date": date(2026, 5, 1),
                "timestamp": datetime(2026, 5, 1, 10, 2),
                "action_type": "view",
                "item_id": 300,
            },
        ]
    )

    result = ItemPairBuilder(max_items_per_session=2).transform_day(sessions)

    assert result.is_empty()


def test_build_pairs_accepts_lazy_frame_and_ignores_null_items() -> None:
    sessions = _sessions(
        [
            {
                "user_id": 1,
                "session_index": 1,
                "session_start_date": date(2026, 5, 1),
                "event_date": date(2026, 5, 1),
                "timestamp": datetime(2026, 5, 1, 10, 0),
                "action_type": "view",
                "item_id": None,
            },
            {
                "user_id": 1,
                "session_index": 1,
                "session_start_date": date(2026, 5, 1),
                "event_date": date(2026, 5, 1),
                "timestamp": datetime(2026, 5, 1, 10, 1),
                "action_type": "view",
                "item_id": 10,
            },
            {
                "user_id": 1,
                "session_index": 1,
                "session_start_date": date(2026, 5, 1),
                "event_date": date(2026, 5, 1),
                "timestamp": datetime(2026, 5, 1, 10, 2),
                "action_type": "click",
                "item_id": 20,
            },
        ]
    )

    result = ItemPairBuilder().transform_day(sessions.lazy())

    assert result.height == 2
    assert set(result["item_id"].to_list()) == {10, 20}


def test_build_pairs_rejects_invalid_max_items_per_session() -> None:
    with pytest.raises(ValueError, match="max_items_per_session"):
        ItemPairBuilder(max_items_per_session=1)


def test_build_pairs_can_be_created_from_config() -> None:
    builder = ItemPairBuilder.from_config(
        {
            "pipeline": {"max_items_per_session": 7},
            "events": {"item_action_types": ["view", "click", "favorite", "to_cart"]},
            "item_pair_builder": {
                "signal_priority": {
                    "view": 1,
                    "click": 2,
                    "favorite": 3,
                    "to_cart": 4,
                }
            },
        }
    )

    assert builder.max_items_per_session == 7
    assert tuple(builder.item_action_types) == ("view", "click", "favorite", "to_cart")
    assert builder.signal_priority == {"view": 1, "click": 2, "favorite": 3, "to_cart": 4}


def test_build_pairs_from_config_treats_string_action_type_as_single_value() -> None:
    builder = ItemPairBuilder.from_config(
        {
            "events": {"item_action_types": "to_cart"},
        }
    )

    assert tuple(builder.item_action_types) == ("to_cart",)


@pytest.mark.parametrize(
    "action_types",
    [123, True, {"view": 1}, ["view", ""], ["view", 1], ()],
)
def test_build_pairs_from_config_rejects_invalid_action_types(action_types: object) -> None:
    with pytest.raises((TypeError, ValueError), match="item_action_types"):
        ItemPairBuilder.from_config(
            {
                "events": {"item_action_types": action_types},
            }
        )
