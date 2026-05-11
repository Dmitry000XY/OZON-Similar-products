from datetime import date

import polars as pl
import pytest

from ozon_similar_products.retrieval.aggregate_pairs import PairAggregator


def _pairs(rows: list[dict]) -> pl.DataFrame:
    return pl.DataFrame(rows)


def test_aggregate_window_counts_signal_channels_and_uniques() -> None:
    day1 = _pairs(
        [
            {
                "pair_date": date(2026, 5, 1),
                "item_id": 10,
                "similar_item_id": 20,
                "session_id": "s1",
                "user_id": 1,
                "source_action_type": "view",
                "target_action_type": "to_cart",
                "signal_type": "to_cart",
            },
            {
                "pair_date": date(2026, 5, 1),
                "item_id": 10,
                "similar_item_id": 20,
                "session_id": "s2",
                "user_id": 1,
                "source_action_type": "view",
                "target_action_type": "click",
                "signal_type": "click",
            },
            {
                "pair_date": date(2026, 5, 1),
                "item_id": 10,
                "similar_item_id": 20,
                "session_id": "s3",
                "user_id": 2,
                "source_action_type": "view",
                "target_action_type": "view",
                "signal_type": "view",
            },
        ]
    )
    day2 = _pairs(
        [
            {
                "pair_date": date(2026, 5, 2),
                "item_id": 10,
                "similar_item_id": 20,
                "session_id": "s4",
                "user_id": 3,
                "source_action_type": "view",
                "target_action_type": "favorite",
                "signal_type": "favorite",
            },
            {
                "pair_date": date(2026, 5, 2),
                "item_id": 20,
                "similar_item_id": 10,
                "session_id": "s4",
                "user_id": 3,
                "source_action_type": "favorite",
                "target_action_type": "view",
                "signal_type": "view",
            },
        ]
    )

    result = PairAggregator().aggregate_window([day1, day2.lazy()], "2026-05-01", "2026-05-02")

    row = result.filter((pl.col("item_id") == 10) & (pl.col("similar_item_id") == 20)).row(0, named=True)
    assert row["pair_count"] == 4
    assert row["view_count"] == 1
    assert row["click_count"] == 1
    assert row["favorite_count"] == 1
    assert row["to_cart_count"] == 1
    assert row["unique_users"] == 3
    assert row["unique_sessions"] == 4


def test_aggregate_window_filters_dates_inclusively() -> None:
    pairs = _pairs(
        [
            {
                "pair_date": date(2026, 5, 1),
                "item_id": 10,
                "similar_item_id": 20,
                "session_id": "old",
                "user_id": 1,
                "source_action_type": "view",
                "target_action_type": "view",
                "signal_type": "view",
            },
            {
                "pair_date": date(2026, 5, 2),
                "item_id": 10,
                "similar_item_id": 20,
                "session_id": "in",
                "user_id": 1,
                "source_action_type": "view",
                "target_action_type": "to_cart",
                "signal_type": "to_cart",
            },
        ]
    )

    result = PairAggregator().aggregate_window([pairs], "2026-05-02", "2026-05-02")

    assert result.height == 1
    assert result[0, "pair_count"] == 1
    assert result[0, "to_cart_count"] == 1


def test_aggregate_window_returns_empty_for_no_pairs() -> None:
    result = PairAggregator().aggregate_window([], "2026-05-01", "2026-05-02")

    assert result.is_empty()
    assert "view_count" in result.columns
    assert "to_cart_count" in result.columns


def test_aggregate_window_rejects_invalid_window() -> None:
    with pytest.raises(ValueError, match="window_start"):
        PairAggregator().aggregate_window([], "bad", "2026-05-02")

    with pytest.raises(ValueError, match="less than or equal"):
        PairAggregator().aggregate_window([], "2026-05-03", "2026-05-02")
