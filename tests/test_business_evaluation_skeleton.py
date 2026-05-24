"""Tests for business/evaluation skeleton modules."""

from datetime import date

import polars as pl
import pytest

from ozon_similar_products.business import (
    FallbackConfig,
    FallbackLayer,
    FallbackMerger,
    merge_fallback_candidates,
)
from ozon_similar_products.evaluation import (
    OfflineMetrics,
    TemporalSplitConfig,
    build_scorecard,
    compute_offline_metrics,
    split_train_validation,
)


def _recommendations_frame() -> pl.DataFrame:
    return pl.DataFrame(
        {
            "item_id": [1, 1],
            "similar_item_id": [10, 11],
            "score": [0.9, 0.8],
            "rank": [1, 2],
            "source": ["behavioral", "behavioral"],
        }
    )


def _item_popularity_frame() -> pl.DataFrame:
    return pl.DataFrame(
        {
            "item_id": [1, 2, 3, 4, 5],
            "events_count": [100, 90, 80, 70, 60],
            "unique_users": [100, 90, 80, 70, 60],
            "views_count": [50, 45, 40, 35, 30],
            "clicks_count": [30, 27, 24, 21, 18],
            "favorites_count": [10, 9, 8, 7, 6],
            "to_cart_count": [10, 9, 8, 7, 6],
        }
    )


def test_fallback_layer_returns_baseline_when_disabled() -> None:
    recommendations = _recommendations_frame()
    layer = FallbackLayer(config=FallbackConfig(enabled=False, top_k=2))

    actual = layer.apply(recommendations, item_popularity=_item_popularity_frame())

    assert actual.equals(recommendations)


def test_fallback_merge_fills_top_k_without_duplicates() -> None:
    recommendations = pl.DataFrame(
        {
            "item_id": [1, 2],
            "similar_item_id": [2, 1],
            "score": [0.9, 0.8],
            "rank": [1, 1],
            "source": ["behavioral", "behavioral"],
        }
    )

    merged = merge_fallback_candidates(
        recommendations=recommendations,
        item_popularity=_item_popularity_frame(),
        config=FallbackConfig(
            enabled=True,
            top_k=3,
            source_label="fallback",
            include_cold_start_items=False,
            candidate_pool_size=5,
        ),
    )

    item_1 = merged.filter(pl.col("item_id") == 1).sort("rank")
    item_2 = merged.filter(pl.col("item_id") == 2).sort("rank")

    assert item_1["similar_item_id"].to_list() == [2, 3, 4]
    assert item_1["source"].to_list() == ["behavioral", "fallback", "fallback"]
    assert item_2["similar_item_id"].to_list() == [1, 3, 4]
    assert item_2["source"].to_list() == ["behavioral", "fallback", "fallback"]


def test_fallback_merge_can_include_cold_start_items() -> None:
    recommendations = pl.DataFrame(
        {
            "item_id": [1],
            "similar_item_id": [2],
            "score": [0.9],
            "rank": [1],
            "source": ["behavioral"],
        }
    )

    merged = merge_fallback_candidates(
        recommendations=recommendations,
        item_popularity=_item_popularity_frame(),
        config=FallbackConfig(
            enabled=True,
            top_k=2,
            include_cold_start_items=True,
            candidate_pool_size=5,
        ),
    )

    # item_id=5 has no behavioral rows, but should receive fallback candidates.
    item_5 = merged.filter(pl.col("item_id") == 5).sort("rank")
    assert item_5.height == 2
    assert item_5["source"].to_list() == ["fallback", "fallback"]


def test_fallback_merger_rejects_invalid_config() -> None:
    with pytest.raises(ValueError, match="fallback.top_k"):
        FallbackConfig(enabled=True, top_k=0)

    with pytest.raises(ValueError, match="fallback.source_label"):
        FallbackConfig(enabled=True, source_label="")

    with pytest.raises(ValueError, match="fallback.candidate_pool_size"):
        FallbackConfig(enabled=True, candidate_pool_size=0)

    assert FallbackMerger(config=FallbackConfig(enabled=True, top_k=10)) is not None


def test_scorecard_builder_returns_immutable_payload() -> None:
    metrics = OfflineMetrics(hit_rate_at_k=0.42, ndcg_at_k=0.31)

    scorecard = build_scorecard(
        experiment_id="exp-001",
        train_until_date="2024-04-30",
        lookback_days=30,
        top_k=20,
        metrics=metrics,
        notes="skeleton scorecard",
    )

    assert scorecard.experiment_id == "exp-001"
    assert scorecard.metrics.hit_rate_at_k == 0.42
    assert scorecard.metrics.ndcg_at_k == 0.31


def test_split_and_metrics_are_explicit_skeletons() -> None:
    split_config = TemporalSplitConfig(
        train_until_date=date(2024, 4, 30),
        validation_start_date=date(2024, 5, 1),
        validation_end_date=date(2024, 5, 7),
    )

    with pytest.raises(NotImplementedError, match="Planned for PR4"):
        split_train_validation(_recommendations_frame(), split_config)

    with pytest.raises(NotImplementedError, match="Planned for PR4"):
        compute_offline_metrics(
            recommendations=_recommendations_frame(),
            ground_truth=_recommendations_frame(),
            top_k=20,
        )
