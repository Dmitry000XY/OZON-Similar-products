"""Tests for session feasibility helpers."""

from datetime import datetime

import polars as pl

from ozon_similar_products.preprocessing.eda_session_checks import (
    add_session_markers,
    time_diff_summary,
)


def test_add_session_markers_sorts_and_splits_by_timeout() -> None:
    """Session markers should be based on sorted user timelines."""
    events = pl.DataFrame(
        {
            "user_id": [1, 1, 1, 2],
            "timestamp": [
                datetime(2024, 3, 1, 0, 10),
                datetime(2024, 3, 1, 0, 0),
                datetime(2024, 3, 1, 0, 45),
                datetime(2024, 3, 1, 0, 5),
            ],
            "item_id": [11, 10, 12, 20],
        }
    )

    marked = add_session_markers(events, timeout_minutes=30).collect()
    user_one = marked.filter(pl.col("user_id") == 1)

    assert user_one["item_id"].to_list() == [10, 11, 12]
    assert user_one["time_diff_seconds"].to_list() == [None, 600, 2100]
    assert user_one["is_new_session"].to_list() == [1, 0, 1]
    assert user_one["session_index"].to_list() == [1, 1, 2]


def test_time_diff_summary_counts_sessions() -> None:
    """Summary should count first events and gaps over timeout as sessions."""
    events = pl.DataFrame(
        {
            "user_id": [1, 1, 1, 2],
            "timestamp": [
                datetime(2024, 3, 1, 0, 0),
                datetime(2024, 3, 1, 0, 10),
                datetime(2024, 3, 1, 0, 45),
                datetime(2024, 3, 1, 0, 5),
            ],
        }
    )

    summary = time_diff_summary(events, timeout_minutes=30, quantiles=(0.5,))

    assert summary["events"].item() == 4
    assert summary["time_diffs"].item() == 2
    assert summary["gaps_over_timeout"].item() == 1
    assert summary["sessions"].item() == 3
