"""Tests for TopKSelector."""

import polars as pl
import pytest

from ozon_similar_products.data.validation import validate_recommendations
from ozon_similar_products.retrieval.topk import TopKSelector


def _pair_scores(rows: list[dict]) -> pl.DataFrame:
    """Build a pair-scores DataFrame for TopKSelector tests."""
    return pl.DataFrame(
        rows,
        schema={
            "item_id": pl.Int64,
            "similar_item_id": pl.Int64,
            "score": pl.Float64,
            "pair_count": pl.Int64,
            "weight_sum": pl.Float64,
            "unique_users": pl.Int64,
            "unique_sessions": pl.Int64,
        },
    )


def _empty_pair_scores() -> pl.DataFrame:
    """Build an empty pair-scores DataFrame with the expected schema."""
    return _pair_scores([])


def test_topk_selector_selects_top_k_per_item_and_adds_rank_source() -> None:
    pair_scores = _pair_scores(
        [
            {
                "item_id": 1,
                "similar_item_id": 2,
                "score": 10.0,
                "pair_count": 10,
                "weight_sum": 10.0,
                "unique_users": 5,
                "unique_sessions": 7,
            },
            {
                "item_id": 1,
                "similar_item_id": 3,
                "score": 8.0,
                "pair_count": 8,
                "weight_sum": 8.0,
                "unique_users": 4,
                "unique_sessions": 5,
            },
            {
                "item_id": 1,
                "similar_item_id": 4,
                "score": 5.0,
                "pair_count": 5,
                "weight_sum": 5.0,
                "unique_users": 3,
                "unique_sessions": 4,
            },
            {
                "item_id": 2,
                "similar_item_id": 1,
                "score": 3.0,
                "pair_count": 3,
                "weight_sum": 3.0,
                "unique_users": 2,
                "unique_sessions": 2,
            },
            {
                "item_id": 2,
                "similar_item_id": 3,
                "score": 6.0,
                "pair_count": 6,
                "weight_sum": 6.0,
                "unique_users": 3,
                "unique_sessions": 3,
            },
        ]
    )

    recommendations = TopKSelector(top_k=2).select(pair_scores)

    validate_recommendations(recommendations)
    assert recommendations.select(
        ["item_id", "similar_item_id", "score", "rank", "source"]
    ).to_dicts() == [
        {
            "item_id": 1,
            "similar_item_id": 2,
            "score": 10.0,
            "rank": 1,
            "source": "behavioral",
        },
        {
            "item_id": 1,
            "similar_item_id": 3,
            "score": 8.0,
            "rank": 2,
            "source": "behavioral",
        },
        {
            "item_id": 2,
            "similar_item_id": 3,
            "score": 6.0,
            "rank": 1,
            "source": "behavioral",
        },
        {
            "item_id": 2,
            "similar_item_id": 1,
            "score": 3.0,
            "rank": 2,
            "source": "behavioral",
        },
    ]


def test_topk_selector_removes_self_recommendations() -> None:
    pair_scores = _pair_scores(
        [
            {
                "item_id": 1,
                "similar_item_id": 1,
                "score": 100.0,
                "pair_count": 100,
                "weight_sum": 100.0,
                "unique_users": 50,
                "unique_sessions": 50,
            },
            {
                "item_id": 1,
                "similar_item_id": 2,
                "score": 10.0,
                "pair_count": 10,
                "weight_sum": 10.0,
                "unique_users": 5,
                "unique_sessions": 7,
            },
        ]
    )

    recommendations = TopKSelector(top_k=5).select(pair_scores)

    assert recommendations["similar_item_id"].to_list() == [2]
    assert recommendations["rank"].to_list() == [1]


def test_topk_selector_uses_stable_tie_break_by_similar_item_id() -> None:
    pair_scores = _pair_scores(
        [
            {
                "item_id": 1,
                "similar_item_id": 4,
                "score": 8.0,
                "pair_count": 8,
                "weight_sum": 8.0,
                "unique_users": 4,
                "unique_sessions": 4,
            },
            {
                "item_id": 1,
                "similar_item_id": 3,
                "score": 8.0,
                "pair_count": 8,
                "weight_sum": 8.0,
                "unique_users": 4,
                "unique_sessions": 4,
            },
            {
                "item_id": 1,
                "similar_item_id": 2,
                "score": 10.0,
                "pair_count": 10,
                "weight_sum": 10.0,
                "unique_users": 5,
                "unique_sessions": 5,
            },
        ]
    )

    recommendations = TopKSelector(top_k=3).select(pair_scores)

    assert recommendations["similar_item_id"].to_list() == [2, 3, 4]
    assert recommendations["rank"].to_list() == [1, 2, 3]


def test_topk_selector_deduplicates_pairs_and_keeps_best_score() -> None:
    pair_scores = _pair_scores(
        [
            {
                "item_id": 1,
                "similar_item_id": 2,
                "score": 5.0,
                "pair_count": 5,
                "weight_sum": 5.0,
                "unique_users": 2,
                "unique_sessions": 2,
            },
            {
                "item_id": 1,
                "similar_item_id": 2,
                "score": 9.0,
                "pair_count": 9,
                "weight_sum": 9.0,
                "unique_users": 4,
                "unique_sessions": 4,
            },
            {
                "item_id": 1,
                "similar_item_id": 3,
                "score": 8.0,
                "pair_count": 8,
                "weight_sum": 8.0,
                "unique_users": 3,
                "unique_sessions": 3,
            },
        ]
    )

    recommendations = TopKSelector(top_k=5).select(pair_scores)

    assert recommendations.select(["similar_item_id", "score", "rank"]).to_dicts() == [
        {"similar_item_id": 2, "score": 9.0, "rank": 1},
        {"similar_item_id": 3, "score": 8.0, "rank": 2},
    ]


def test_topk_selector_applies_quality_thresholds() -> None:
    pair_scores = _pair_scores(
        [
            {
                "item_id": 1,
                "similar_item_id": 2,
                "score": 10.0,
                "pair_count": 1,
                "weight_sum": 10.0,
                "unique_users": 5,
                "unique_sessions": 5,
            },
            {
                "item_id": 1,
                "similar_item_id": 3,
                "score": 9.0,
                "pair_count": 3,
                "weight_sum": 9.0,
                "unique_users": 1,
                "unique_sessions": 5,
            },
            {
                "item_id": 1,
                "similar_item_id": 4,
                "score": 8.0,
                "pair_count": 3,
                "weight_sum": 8.0,
                "unique_users": 2,
                "unique_sessions": 1,
            },
            {
                "item_id": 1,
                "similar_item_id": 5,
                "score": 7.0,
                "pair_count": 3,
                "weight_sum": 7.0,
                "unique_users": 2,
                "unique_sessions": 2,
            },
        ]
    )

    recommendations = TopKSelector(
        top_k=5,
        min_pair_count=2,
        min_unique_users=2,
        min_unique_sessions=2,
    ).select(pair_scores)

    assert recommendations["similar_item_id"].to_list() == [5]
    assert recommendations["rank"].to_list() == [1]


def test_topk_selector_preserves_diagnostic_columns() -> None:
    pair_scores = _pair_scores(
        [
            {
                "item_id": 1,
                "similar_item_id": 2,
                "score": 10.0,
                "pair_count": 10,
                "weight_sum": 15.0,
                "unique_users": 5,
                "unique_sessions": 7,
            }
        ]
    )

    recommendations = TopKSelector(top_k=1).select(pair_scores)

    assert recommendations.columns == [
        "item_id",
        "similar_item_id",
        "score",
        "rank",
        "source",
        "pair_count",
        "weight_sum",
        "unique_users",
        "unique_sessions",
    ]
    assert recommendations.select(
        ["pair_count", "weight_sum", "unique_users", "unique_sessions"]
    ).to_dicts() == [
        {
            "pair_count": 10,
            "weight_sum": 15.0,
            "unique_users": 5,
            "unique_sessions": 7,
        }
    ]


def test_topk_selector_handles_empty_input() -> None:
    recommendations = TopKSelector(top_k=5).select(_empty_pair_scores())

    validate_recommendations(recommendations)
    assert recommendations.is_empty()
    assert recommendations.columns == [
        "item_id",
        "similar_item_id",
        "score",
        "rank",
        "source",
        "pair_count",
        "weight_sum",
        "unique_users",
        "unique_sessions",
    ]


def test_topk_selector_accepts_lazy_frame() -> None:
    pair_scores = _pair_scores(
        [
            {
                "item_id": 1,
                "similar_item_id": 2,
                "score": 10.0,
                "pair_count": 10,
                "weight_sum": 10.0,
                "unique_users": 5,
                "unique_sessions": 5,
            }
        ]
    )

    recommendations = TopKSelector(top_k=1).select(pair_scores.lazy())

    assert recommendations["similar_item_id"].to_list() == [2]
    assert recommendations["rank"].to_list() == [1]


def test_topk_selector_rejects_invalid_configuration() -> None:
    with pytest.raises(ValueError, match="top_k must be a positive integer"):
        TopKSelector(top_k=0)

    with pytest.raises(ValueError, match="min_pair_count"):
        TopKSelector(min_pair_count=-1)

    with pytest.raises(ValueError, match="source"):
        TopKSelector(source="")
