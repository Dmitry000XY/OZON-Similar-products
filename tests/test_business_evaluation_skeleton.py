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
            "item_id": [1, 2, 3, 4, 5, 6, 7],
            "events_count": [100, 90, 80, 70, 60, 50, 40],
            "unique_users": [100, 90, 80, 70, 60, 50, 40],
            "views_count": [50, 45, 40, 35, 30, 25, 20],
            "clicks_count": [30, 27, 24, 21, 18, 15, 12],
            "favorites_count": [10, 9, 8, 7, 6, 5, 4],
            "to_cart_count": [10, 9, 8, 7, 6, 5, 4],
        }
    )


def _product_information_frame() -> pl.DataFrame:
    return pl.DataFrame(
        {
            "item_id": [1, 2, 3, 4, 5, 6],
            "name": ["item_1", "item_2", "item_3", "item_4", "item_5", "item_6"],
            "brand": ["brand_a", "brand_b", "brand_c", "brand_d", "brand_e", "brand_a"],
            "type": ["milk", "milk", "milk", "cheese", "milk", "bread"],
            "category_id": [10, 10, 10, 10, 20, 30],
            "category_name": ["dairy", "dairy", "dairy", "dairy", "food", "bakery"],
        }
    )


def test_fallback_layer_returns_baseline_when_disabled() -> None:
    recommendations = _recommendations_frame()
    layer = FallbackLayer(config=FallbackConfig(enabled=False, top_k=2))

    actual = layer.apply(
        recommendations,
        item_popularity=_item_popularity_frame(),
        product_information=_product_information_frame(),
    )

    assert actual.equals(recommendations)


def test_fallback_merge_fills_top_k_by_metadata_cascade_without_duplicates() -> None:
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
        product_information=_product_information_frame(),
        config=FallbackConfig(
            enabled=True,
            top_k=5,
            include_cold_start_items=False,
            candidate_pool_size=10,
        ),
    )

    item_1 = merged.filter(pl.col("item_id") == 1).sort("rank")

    assert item_1["similar_item_id"].to_list() == [2, 3, 4, 5, 6]
    assert item_1["rank"].to_list() == [1, 2, 3, 4, 5]
    assert item_1["score"].to_list() == [0.9, 0.0, 0.0, 0.0, 0.0]
    assert item_1["source"].to_list() == [
        "behavioral",
        "fallback_category_type_popular",
        "fallback_category_popular",
        "fallback_type_popular",
        "fallback_global_popular",
    ]


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
        product_information=_product_information_frame(),
        config=FallbackConfig(
            enabled=True,
            top_k=2,
            include_cold_start_items=True,
            candidate_pool_size=10,
        ),
    )

    # item_id=5 has no behavioral rows, but should receive fallback candidates.
    item_5 = merged.filter(pl.col("item_id") == 5).sort("rank")
    assert item_5.height == 2
    assert item_5["source"].to_list() == [
        "fallback_type_popular",
        "fallback_type_popular",
    ]


def test_fallback_uses_global_only_when_source_metadata_is_missing() -> None:
    recommendations = pl.DataFrame(
        {
            "item_id": [99],
            "similar_item_id": [1],
            "score": [0.9],
            "rank": [1],
            "source": ["behavioral"],
        }
    )
    popularity = pl.concat(
        [
            _item_popularity_frame(),
            pl.DataFrame(
                {
                    "item_id": [99],
                    "events_count": [95],
                    "unique_users": [95],
                    "views_count": [48],
                    "clicks_count": [29],
                    "favorites_count": [9],
                    "to_cart_count": [9],
                }
            ),
        ],
        how="vertical",
    )

    merged = merge_fallback_candidates(
        recommendations=recommendations,
        item_popularity=popularity,
        product_information=_product_information_frame(),
        config=FallbackConfig(
            enabled=True,
            top_k=3,
            include_cold_start_items=False,
            candidate_pool_size=10,
        ),
    )

    item_99 = merged.filter(pl.col("item_id") == 99).sort("rank")

    assert item_99["similar_item_id"].to_list() == [1, 2, 3]
    assert item_99["source"].to_list() == [
        "behavioral",
        "fallback_global_popular",
        "fallback_global_popular",
    ]


def test_fallback_brand_level_is_optional() -> None:
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
        product_information=_product_information_frame(),
        config=FallbackConfig(
            enabled=True,
            top_k=6,
            enable_brand=True,
            include_cold_start_items=False,
            candidate_pool_size=10,
        ),
    )

    item_1 = merged.filter(pl.col("item_id") == 1).sort("rank")

    assert item_1["similar_item_id"].to_list() == [2, 3, 4, 5, 6, 7]
    assert item_1["source"].to_list() == [
        "behavioral",
        "fallback_category_type_popular",
        "fallback_category_popular",
        "fallback_type_popular",
        "fallback_brand_popular",
        "fallback_global_popular",
    ]


def test_fallback_merger_rejects_invalid_config() -> None:
    with pytest.raises(ValueError, match="fallback.top_k"):
        FallbackConfig(enabled=True, top_k=0)

    with pytest.raises(ValueError, match="fallback.source_label"):
        FallbackConfig(enabled=True, source_label="")

    with pytest.raises(ValueError, match="fallback.candidate_pool_size"):
        FallbackConfig(enabled=True, candidate_pool_size=0)

    assert FallbackMerger(config=FallbackConfig(enabled=True, top_k=10)) is not None


def test_fallback_config_reports_parameter_name_for_invalid_string_ints() -> None:
    with pytest.raises(ValueError, match="fallback.top_k"):
        FallbackConfig.from_config(
            {"fallback": {"top_k": "abc"}},
            top_k=20,
        )

    with pytest.raises(ValueError, match="fallback.candidate_pool_size"):
        FallbackConfig.from_config(
            {"fallback": {"candidate_pool_size": "not-a-number"}},
            top_k=20,
        )


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
