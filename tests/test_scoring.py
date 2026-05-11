import math

import polars as pl
import pytest

from ozon_similar_products.data import schemas
from ozon_similar_products.data.validation import validate_pair_scores
from ozon_similar_products.retrieval.scoring import CoVisitationScorer


def _aggregates() -> pl.DataFrame:
    return pl.DataFrame(
        [
            {
                "item_id": 1,
                "similar_item_id": 10,
                "pair_count": 100,
                "view_count": 100,
                "click_count": 0,
                "favorite_count": 0,
                "to_cart_count": 0,
                "unique_users": 20,
                "unique_sessions": 40,
                "window_start": "2026-05-01",
                "window_end": "2026-05-30",
            },
            {
                "item_id": 1,
                "similar_item_id": 20,
                "pair_count": 1,
                "view_count": 0,
                "click_count": 0,
                "favorite_count": 0,
                "to_cart_count": 1,
                "unique_users": 1,
                "unique_sessions": 1,
                "window_start": "2026-05-01",
                "window_end": "2026-05-30",
            },
        ]
    )


def _item_popularity() -> pl.DataFrame:
    return pl.DataFrame(
        [
            {"item_id": 1, "events_count": 200, "unique_users": 20, "views_count": 120, "clicks_count": 40, "favorites_count": 20, "to_cart_count": 20},
            {"item_id": 10, "events_count": 3000, "unique_users": 1000, "views_count": 2000, "clicks_count": 500, "favorites_count": 300, "to_cart_count": 200},
            {"item_id": 20, "events_count": 40, "unique_users": 10, "views_count": 20, "clicks_count": 8, "favorites_count": 7, "to_cart_count": 5},
        ]
    )


def test_pair_count_score_keeps_channel_diagnostics() -> None:
    scores = CoVisitationScorer(method="pair_count").score(_aggregates())

    assert scores[0, "score"] == 100.0
    assert "view_count" in scores.columns
    assert "to_cart_count" in scores.columns


def test_calibrated_multichannel_can_make_cart_dominate_many_views() -> None:
    scores = CoVisitationScorer(
        method="calibrated_multichannel",
        business_weights={"view": 1.0, "click": 3.0, "favorite": 6.0, "to_cart": 8.0},
        action_shares={"view": 0.80, "click": 0.10, "favorite": 0.06, "to_cart": 0.04},
        beta=0.5,
        max_frequency_boost={"view": 1.0, "click": 10.0, "favorite": 15.0, "to_cart": 30.0},
    ).score(_aggregates())

    view_score = scores.filter(pl.col("similar_item_id") == 10)[0, "score"]
    cart_score = scores.filter(pl.col("similar_item_id") == 20)[0, "score"]

    assert view_score == pytest.approx(math.log1p(100))
    assert cart_score == pytest.approx(8.0 * math.sqrt(0.80 / 0.04) * math.log1p(1))
    assert cart_score > view_score


def test_calibrated_without_action_shares_uses_business_weights_only() -> None:
    scores = CoVisitationScorer(
        method="calibrated_multichannel",
        business_weights={"view": 1.0, "click": 3.0, "favorite": 6.0, "to_cart": 8.0},
        action_shares=None,
    ).score(_aggregates())

    view_score = scores.filter(pl.col("similar_item_id") == 10)[0, "score"]
    cart_score = scores.filter(pl.col("similar_item_id") == 20)[0, "score"]

    assert view_score == pytest.approx(math.log1p(100))
    assert cart_score == pytest.approx(8.0 * math.log1p(1))


def test_scorer_thresholds_filter_weak_pairs() -> None:
    scores = CoVisitationScorer(method="pair_count", min_unique_users=2).score(_aggregates())

    assert scores.height == 1
    assert scores[0, "similar_item_id"] == 10


def test_scorer_rejects_invalid_method() -> None:
    with pytest.raises(ValueError, match="method"):
        CoVisitationScorer(method="unknown")


def test_popularity_normalization_penalizes_popular_candidate() -> None:
    aggregates = pl.DataFrame(
        [
            {
                "item_id": 1,
                "similar_item_id": 10,
                "pair_count": 10,
                "view_count": 10,
                "click_count": 0,
                "favorite_count": 0,
                "to_cart_count": 0,
                "unique_users": 5,
                "unique_sessions": 5,
                "window_start": "2026-05-01",
                "window_end": "2026-05-30",
            },
            {
                "item_id": 1,
                "similar_item_id": 20,
                "pair_count": 10,
                "view_count": 10,
                "click_count": 0,
                "favorite_count": 0,
                "to_cart_count": 0,
                "unique_users": 5,
                "unique_sessions": 5,
                "window_start": "2026-05-01",
                "window_end": "2026-05-30",
            },
        ]
    )
    scores = CoVisitationScorer(
        method="pair_count",
        normalize_by_item_popularity=True,
    ).score(aggregates, item_popularity=_item_popularity())

    score_popular = scores.filter(pl.col("similar_item_id") == 10)[0, "score"]
    score_less_popular = scores.filter(pl.col("similar_item_id") == 20)[0, "score"]
    assert score_less_popular > score_popular


def test_popularity_normalization_requires_item_popularity() -> None:
    with pytest.raises(ValueError, match="item_popularity"):
        CoVisitationScorer(normalize_by_item_popularity=True).score(_aggregates())


def test_missing_popularity_column_raises_error() -> None:
    with pytest.raises(ValueError, match="popularity_column"):
        CoVisitationScorer(
            normalize_by_item_popularity=True,
            popularity_column="missing_column",
        ).score(_aggregates(), item_popularity=_item_popularity())


def test_output_contract_after_normalization() -> None:
    scores = CoVisitationScorer(
        method="pair_count",
        normalize_by_item_popularity=True,
    ).score(_aggregates(), item_popularity=_item_popularity())

    validate_pair_scores(scores)
    assert scores.columns == schemas.PAIR_SCORES_COLUMNS
