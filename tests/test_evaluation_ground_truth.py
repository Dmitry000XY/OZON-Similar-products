"""Tests for evaluation ground-truth builder."""

from __future__ import annotations

from datetime import date

import polars as pl

from ozon_similar_products.evaluation.ground_truth import build_ground_truth_from_daily_pair_counts


def test_build_ground_truth_from_daily_pair_counts_defaults_to_binary_mode() -> None:
    pair_counts = pl.DataFrame(
        {
            "pair_date": [date(2024, 4, 24), date(2024, 4, 24)],
            "item_id": [100, 100],
            "similar_item_id": [200, 300],
            "pair_count": [1, 1],
            "view_count": [0, 0],
            "click_count": [1, 0],
            "favorite_count": [0, 0],
            "to_cart_count": [0, 1],
        }
    )

    ground_truth = build_ground_truth_from_daily_pair_counts(pair_counts)

    to_cart_row = ground_truth.filter(pl.col("relevant_item_id") == 300)

    assert to_cart_row.height == 1
    assert to_cart_row["target_action_type"].to_list() == ["to_cart"]
    assert to_cart_row["relevance"].to_list() == [1.0]
    assert to_cart_row["view_count"].to_list() == [0]
    assert to_cart_row["click_count"].to_list() == [0]
    assert to_cart_row["favorite_count"].to_list() == [0]
    assert to_cart_row["to_cart_count"].to_list() == [1]


def test_build_ground_truth_from_daily_pair_counts_supports_graded_mode() -> None:
    pair_counts = pl.DataFrame(
        {
            "pair_date": [date(2024, 4, 24)],
            "item_id": [100],
            "similar_item_id": [200],
            "pair_count": [2],
            "view_count": [3],
            "click_count": [1],
            "favorite_count": [0],
            "to_cart_count": [1],
        }
    )

    ground_truth = build_ground_truth_from_daily_pair_counts(
        pair_counts,
        relevance_mode="graded",
    )

    assert ground_truth["relevance"].to_list() == [1.6]
