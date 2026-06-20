import polars as pl
import pytest

from ozon_similar_products.evaluation import compute_offline_metrics


def _recommendations_for_explicit_null_threshold() -> pl.DataFrame:
    return pl.DataFrame(
        {
            "item_id": [1, 1, 1],
            "similar_item_id": [10, 11, 12],
            "score": [1.0, 0.9, 0.8],
            "rank": [1, 2, 3],
            "source": ["behavioral", "behavioral", "behavioral"],
        }
    )


def _mixed_action_ground_truth() -> pl.DataFrame:
    return pl.DataFrame(
        {
            "item_id": [1, 1, 1],
            "relevant_item_id": [10, 11, 12],
            "relevance": [0.3, 0.6, 1.0],
            "target_action_type": ["click", "favorite", "to_cart"],
            "evidence_count": [1, 1, 1],
            "view_count": [0, 0, 0],
            "click_count": [1, 0, 0],
            "favorite_count": [0, 1, 0],
            "to_cart_count": [0, 0, 1],
        }
    )


def test_explicit_null_min_ranking_relevance_disables_threshold_fallback() -> None:
    metrics = compute_offline_metrics(
        recommendations=_recommendations_for_explicit_null_threshold(),
        ground_truth=_mixed_action_ground_truth(),
        top_k=3,
        ranking_relevant_action_types=["to_cart"],
        min_ranking_relevance=None,
    )

    assert metrics.ranking_ground_truth_pairs == 1
    assert metrics.ranking_evaluated_items == 1
    assert metrics.mrr_at_k == pytest.approx(1 / 3)
    assert metrics.recall_at_k == 1.0


def test_omitted_min_ranking_relevance_uses_default_threshold() -> None:
    metrics = compute_offline_metrics(
        recommendations=_recommendations_for_explicit_null_threshold(),
        ground_truth=_mixed_action_ground_truth(),
        top_k=3,
        ranking_relevant_action_types=["to_cart"],
    )

    assert metrics.ranking_ground_truth_pairs == 3
    assert metrics.mrr_at_k == 1.0
    assert metrics.recall_at_k == 1.0
