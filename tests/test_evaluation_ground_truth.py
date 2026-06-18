"""Tests for evaluation ground-truth builder."""

from __future__ import annotations

from datetime import date, datetime

import polars as pl

from ozon_similar_products.evaluation.ground_truth import build_ground_truth_from_sessions


def test_build_ground_truth_from_sessions_prefers_to_cart_relevance() -> None:
    sessions = pl.DataFrame(
        {
            "user_id": [1, 1, 1],
            "session_index": [1, 1, 1],
            "session_start_date": [date(2024, 4, 30)] * 3,
            "event_date": [date(2024, 4, 30)] * 3,
            "timestamp": [
                datetime(2024, 4, 30, 10, 0),
                datetime(2024, 4, 30, 10, 1),
                datetime(2024, 4, 30, 10, 2),
            ],
            "action_type": ["view", "click", "to_cart"],
            "item_id": [100, 200, 300],
        }
    )

    ground_truth = build_ground_truth_from_sessions(sessions)

    row = ground_truth.filter((pl.col("item_id") == 100) & (pl.col("relevant_item_id") == 300))

    assert row.height == 1
    assert row["target_action_type"].to_list() == ["to_cart"]
    assert row["relevance"].to_list() == [1.0]
