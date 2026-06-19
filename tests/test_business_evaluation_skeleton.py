"""Tests for business/evaluation skeleton modules."""

from datetime import date

import polars as pl
import pytest

from ozon_similar_products.business import (
    FallbackConfig,
    FallbackIndexBuilder,
    FallbackLayer,
    FallbackMerger,
    merge_fallback_candidates,
)
from ozon_similar_products.business.fallback import FrameLike
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


def test_fallback_candidate_without_metadata_appears_only_in_global_level() -> None:
    recommendations = pl.DataFrame(
        {
            "item_id": [6],
            "similar_item_id": [1],
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
            include_cold_start_items=False,
            candidate_pool_size=10,
        ),
    )

    item_6 = merged.filter(pl.col("item_id") == 6).sort("rank")

    assert item_6["similar_item_id"].to_list() == [1, 2, 3, 4, 5, 7]
    assert item_6["source"].to_list() == [
        "behavioral",
        "fallback_global_popular",
        "fallback_global_popular",
        "fallback_global_popular",
        "fallback_global_popular",
        "fallback_global_popular",
    ]


def test_fallback_include_catalog_only_sources_uses_precomputed_indexes() -> None:
    product_information = pl.concat(
        [
            _product_information_frame(),
            pl.DataFrame(
                {
                    "item_id": [8],
                    "name": ["item_8"],
                    "brand": ["brand_z"],
                    "type": ["milk"],
                    "category_id": [10],
                    "category_name": ["dairy"],
                }
            ),
        ],
        how="vertical",
    )

    merged = merge_fallback_candidates(
        recommendations=pl.DataFrame(
            {
                "item_id": [],
                "similar_item_id": [],
                "score": [],
                "rank": [],
                "source": [],
            }
        ),
        item_popularity=_item_popularity_frame(),
        product_information=product_information,
        config=FallbackConfig(
            enabled=True,
            top_k=3,
            include_cold_start_items=False,
            include_catalog_only_sources=True,
            candidate_pool_size=10,
        ),
    )

    item_8 = merged.filter(pl.col("item_id") == 8).sort("rank")

    assert item_8["similar_item_id"].to_list() == [1, 2, 3]
    assert item_8["source"].to_list() == [
        "fallback_category_type_popular",
        "fallback_category_type_popular",
        "fallback_category_type_popular",
    ]


def test_fallback_index_respects_candidate_pool_limits_and_reuses_global_list() -> None:
    fallback_index = FallbackIndexBuilder(
        config=FallbackConfig(
            enabled=True,
            top_k=3,
            candidate_pool_size=10,
            global_candidate_pool_size=4,
            metadata_candidate_pool_size=2,
        )
    ).build(
        item_popularity=_item_popularity_frame(),
        product_information=_product_information_frame(),
    )

    assert fallback_index.global_candidates == [1, 2, 3, 4]
    assert fallback_index.by_category_type[(10, "milk")] == [1, 2]
    assert fallback_index.by_category[10] == [1, 2]
    metadata_candidate_lists = [
        *fallback_index.by_category_type.values(),
        *fallback_index.by_category.values(),
        *fallback_index.by_type.values(),
        *fallback_index.by_brand.values(),
    ]
    assert all(7 not in candidates for candidates in metadata_candidate_lists)


def test_fallback_index_is_built_once_for_many_source_items(
        monkeypatch: pytest.MonkeyPatch,
) -> None:
    item_count = 300
    recommendations = pl.DataFrame(
        {
            "item_id": list(range(1, 101)),
            "similar_item_id": list(range(101, 201)),
            "score": [1.0] * 100,
            "rank": [1] * 100,
            "source": ["behavioral"] * 100,
        }
    )
    item_popularity = pl.DataFrame(
        {
            "item_id": list(range(1, item_count + 1)),
            "events_count": list(range(item_count, 0, -1)),
            "unique_users": list(range(item_count, 0, -1)),
            "views_count": list(range(item_count, 0, -1)),
            "clicks_count": list(range(item_count, 0, -1)),
            "favorites_count": list(range(item_count, 0, -1)),
            "to_cart_count": list(range(item_count, 0, -1)),
        }
    )
    product_information = pl.DataFrame(
        {
            "item_id": list(range(1, item_count + 1)),
            "name": [f"item_{item_id}" for item_id in range(1, item_count + 1)],
            "brand": ["brand"] * item_count,
            "type": ["type"] * item_count,
            "category_id": [10] * item_count,
            "category_name": ["category"] * item_count,
        }
    )
    build_calls = 0
    original_build = FallbackIndexBuilder.build

    def build_spy(
            self: FallbackIndexBuilder,
            item_popularity: FrameLike,
            product_information: FrameLike | None = None,
    ):
        nonlocal build_calls
        build_calls += 1
        return original_build(
            self,
            item_popularity=item_popularity,
            product_information=product_information,
        )

    monkeypatch.setattr(FallbackIndexBuilder, "build", build_spy)

    merged = merge_fallback_candidates(
        recommendations=recommendations,
        item_popularity=item_popularity,
        product_information=product_information,
        config=FallbackConfig(
            enabled=True,
            top_k=3,
            include_cold_start_items=True,
            global_candidate_pool_size=20,
            metadata_candidate_pool_size=20,
        ),
    )

    assert build_calls == 1
    assert merged.filter(pl.col("item_id") == 1).height == 3
    assert merged.filter(pl.col("source").str.starts_with("fallback")).height > 0


def test_fallback_merger_rejects_invalid_config() -> None:
    with pytest.raises(ValueError, match="fallback.top_k"):
        FallbackConfig(enabled=True, top_k=0)

    with pytest.raises(ValueError, match="fallback.source_label"):
        FallbackConfig(enabled=True, source_label="")

    with pytest.raises(ValueError, match="fallback.candidate_pool_size"):
        FallbackConfig(enabled=True, candidate_pool_size=0)

    with pytest.raises(ValueError, match="fallback.global_candidate_pool_size"):
        FallbackConfig(enabled=True, global_candidate_pool_size=0)

    with pytest.raises(ValueError, match="fallback.metadata_candidate_pool_size"):
        FallbackConfig(enabled=True, metadata_candidate_pool_size=0)

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


def test_split_and_metrics_compute_real_values() -> None:
    frame = pl.DataFrame(
        {
            "item_id": [1, 2, 3],
            "event_date": [
                date(2024, 4, 30),
                date(2024, 5, 1),
                date(2024, 5, 2),
            ],
        }
    )
    split_config = TemporalSplitConfig(
        train_until_date=date(2024, 4, 30),
        validation_start_date=date(2024, 5, 1),
        validation_end_date=date(2024, 5, 7),
    )

    train, validation = split_train_validation(frame, split_config)

    assert train["item_id"].to_list() == [1]
    assert validation["item_id"].to_list() == [2, 3]

    recommendations = pl.DataFrame(
        {
            "item_id": [1, 1, 2],
            "similar_item_id": [10, 11, 20],
            "score": [0.9, 0.8, 0.7],
            "rank": [1, 2, 1],
            "source": ["behavioral", "behavioral", "fallback"],
        }
    )
    ground_truth = pl.DataFrame(
        {
            "item_id": [1, 1, 2],
            "relevant_item_id": [11, 12, 20],
            "relevance": [1.0, 1.0, 1.0],
            "target_action_type": ["to_cart", "click", "to_cart"],
            "evidence_count": [1, 1, 1],
            "view_count": [1, 1, 0],
            "click_count": [0, 1, 0],
            "favorite_count": [0, 0, 0],
            "to_cart_count": [1, 0, 1],
        }
    )

    metrics = compute_offline_metrics(
        recommendations=recommendations,
        ground_truth=ground_truth,
        top_k=2,
    )

    assert metrics.evaluated_items == 2
    assert metrics.ground_truth_pairs == 3
    assert metrics.hit_rate_at_k == 1.0
    assert metrics.recall_at_k == pytest.approx(0.75)
    assert metrics.click_hit_rate_at_k == 0.0
    assert metrics.click_recall_at_k == 0.0
    assert metrics.view_hit_rate_at_k == 1.0
    assert metrics.view_recall_at_k == pytest.approx(0.5)
    assert metrics.to_cart_hit_rate_at_k == 1.0
    assert metrics.to_cart_recall_at_k == 1.0
    assert metrics.coverage_at_k == 1.0
    assert metrics.fallback_share_at_k == pytest.approx(1 / 3)
    assert metrics.fallback_hit_rate_at_k == pytest.approx(0.5)
    assert metrics.fallback_recall_at_k == pytest.approx(0.5)
    assert metrics.fallback_to_cart_hit_rate_at_k == pytest.approx(0.5)
    assert metrics.fallback_to_cart_recall_at_k == pytest.approx(0.5)


def test_offline_metrics_report_fallback_layer_shares_and_quality() -> None:
    recommendations = pl.DataFrame(
        {
            "item_id": [1, 1, 1, 1, 1, 1, 2, 2],
            "similar_item_id": [10, 11, 12, 13, 14, 15, 20, 21],
            "score": [1.0, 0.0, 0.0, 0.0, 0.0, 0.0, 1.0, 0.0],
            "rank": [1, 2, 3, 4, 5, 6, 1, 2],
            "source": [
                "behavioral",
                "fallback_category_type_popular",
                "fallback_category_popular",
                "fallback_type_popular",
                "fallback_brand_popular",
                "fallback_global_popular",
                "behavioral",
                "fallback_global_popular",
            ],
        }
    )
    ground_truth = pl.DataFrame(
        {
            "item_id": [1, 1, 1, 2, 2],
            "relevant_item_id": [11, 12, 15, 20, 21],
            "relevance": [1.0, 1.0, 1.0, 1.0, 1.0],
            "target_action_type": ["to_cart", "click", "to_cart", "to_cart", "view"],
            "evidence_count": [1, 1, 1, 1, 1],
            "view_count": [0, 0, 0, 0, 1],
            "click_count": [0, 1, 0, 0, 0],
            "favorite_count": [0, 0, 0, 0, 0],
            "to_cart_count": [1, 0, 1, 1, 0],
        }
    )

    metrics = compute_offline_metrics(
        recommendations=recommendations,
        ground_truth=ground_truth,
        top_k=6,
    )

    assert metrics.fallback_share_at_k == pytest.approx(6 / 8)
    assert metrics.fallback_category_type_share_at_k == pytest.approx(1 / 8)
    assert metrics.fallback_category_share_at_k == pytest.approx(1 / 8)
    assert metrics.fallback_type_share_at_k == pytest.approx(1 / 8)
    assert metrics.fallback_brand_share_at_k == pytest.approx(1 / 8)
    assert metrics.fallback_global_share_at_k == pytest.approx(2 / 8)
    assert metrics.fallback_hit_rate_at_k == 1.0
    assert metrics.fallback_recall_at_k == pytest.approx(0.75)
    assert metrics.fallback_to_cart_hit_rate_at_k == pytest.approx(0.5)
    assert metrics.fallback_to_cart_recall_at_k == pytest.approx(0.5)
