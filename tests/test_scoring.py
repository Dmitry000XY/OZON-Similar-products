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


def _aggregates_with_weighted() -> pl.DataFrame:
    return _aggregates().with_columns(
        pl.when(pl.col("similar_item_id") == 10)
        .then(2.5)
        .otherwise(0.25)
        .alias("weighted_pair_count"),
        pl.when(pl.col("similar_item_id") == 10)
        .then(2.5)
        .otherwise(0.0)
        .alias("weighted_view_count"),
        pl.lit(0.0).alias("weighted_click_count"),
        pl.lit(0.0).alias("weighted_favorite_count"),
        pl.when(pl.col("similar_item_id") == 20)
        .then(0.25)
        .otherwise(0.0)
        .alias("weighted_to_cart_count"),
    ).select(schemas.PAIR_AGGREGATES_COLUMNS)


def _complete_aggregates(frame: pl.DataFrame) -> pl.DataFrame:
    return frame.with_columns(
        pl.col("pair_count").cast(pl.Float64).alias("weighted_pair_count"),
        pl.col("view_count").cast(pl.Float64).alias("weighted_view_count"),
        pl.col("click_count").cast(pl.Float64).alias("weighted_click_count"),
        pl.col("favorite_count").cast(pl.Float64).alias("weighted_favorite_count"),
        pl.col("to_cart_count").cast(pl.Float64).alias("weighted_to_cart_count"),
    ).select(schemas.PAIR_AGGREGATES_COLUMNS)


def _item_popularity() -> pl.DataFrame:
    return pl.DataFrame(
        [
            {"item_id": 1, "events_count": 200, "unique_users": 20, "views_count": 120, "clicks_count": 40,
             "favorites_count": 20, "to_cart_count": 20},
            {"item_id": 10, "events_count": 3000, "unique_users": 1000, "views_count": 2000, "clicks_count": 500,
             "favorites_count": 300, "to_cart_count": 200},
            {"item_id": 20, "events_count": 40, "unique_users": 10, "views_count": 20, "clicks_count": 8,
             "favorites_count": 7, "to_cart_count": 5},
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


def test_count_source_raw_uses_raw_counts_even_when_weighted_counts_differ() -> None:
    scores = CoVisitationScorer(method="pair_count", count_source="raw").score(
        _aggregates_with_weighted()
    )

    assert scores.filter(pl.col("similar_item_id") == 10)[0, "score"] == 100.0
    assert scores.filter(pl.col("similar_item_id") == 20)[0, "score"] == 1.0


def test_count_source_weighted_uses_weighted_counts() -> None:
    scores = CoVisitationScorer(method="pair_count", count_source="weighted").score(
        _aggregates_with_weighted()
    )

    assert scores.filter(pl.col("similar_item_id") == 10)[0, "score"] == 2.5
    assert scores.filter(pl.col("similar_item_id") == 20)[0, "score"] == 0.25


@pytest.mark.parametrize(
    ("method", "expected"),
    [
        ("log", math.log(2.5 + 1.0)),
        ("linear", 2.5),
        ("sqrt", math.sqrt(2.5)),
    ],
)
def test_count_transform_methods(method: str, expected: float) -> None:
    scores = CoVisitationScorer(
        method="calibrated_multichannel",
        count_source="weighted",
        count_transform_method=method,
        count_transform_smoothing=1.0,
        business_weights={"view": 1.0, "click": 0.0, "favorite": 0.0, "to_cart": 0.0},
    ).score(_aggregates_with_weighted())

    assert scores.filter(pl.col("similar_item_id") == 10)[0, "score"] == pytest.approx(expected)


def test_min_weighted_pair_count_filters_weak_weighted_pairs() -> None:
    scores = CoVisitationScorer(
        method="pair_count",
        min_weighted_pair_count=1.0,
    ).score(_aggregates_with_weighted())

    assert scores["similar_item_id"].to_list() == [10]


def test_min_score_filters_after_scoring() -> None:
    scores = CoVisitationScorer(
        method="pair_count",
        count_source="weighted",
        min_score=1.0,
    ).score(_aggregates_with_weighted())

    assert scores["similar_item_id"].to_list() == [10]


def test_scorer_rejects_invalid_method() -> None:
    with pytest.raises(ValueError, match="method"):
        CoVisitationScorer(method="unknown")


def test_scorer_rejects_invalid_count_source() -> None:
    with pytest.raises(ValueError, match="count_source"):
        CoVisitationScorer(count_source="missing")


def test_scorer_rejects_invalid_count_transform() -> None:
    with pytest.raises(ValueError, match="count_transform.method"):
        CoVisitationScorer(count_transform_method="missing")


def test_weighted_count_source_requires_weighted_columns() -> None:
    with pytest.raises(ValueError, match="weighted count columns"):
        CoVisitationScorer(count_source="weighted").score(_aggregates())


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


@pytest.mark.parametrize(
    ("raw_value", "expected"),
    [("false", False), ("true", True), (0, False), (1, True)],
)
def test_from_config_parses_normalize_by_item_popularity_strictly(
    raw_value: str | int,
    expected: bool,
) -> None:
    scorer = CoVisitationScorer.from_config(
        {"scoring": {"normalize_by_item_popularity": raw_value}}
    )

    assert scorer.normalize_by_item_popularity is expected


def test_from_config_rejects_invalid_normalize_by_item_popularity() -> None:
    with pytest.raises(ValueError, match="normalize_by_item_popularity"):
        CoVisitationScorer.from_config(
            {"scoring": {"normalize_by_item_popularity": "definitely-not-bool"}}
        )

def test_score_lazy_matches_score() -> None:
    pair_aggregates = _complete_aggregates(pl.DataFrame(
        {
            "item_id": [10, 10, 20],
            "similar_item_id": [20, 30, 10],
            "pair_count": [3, 1, 2],
            "view_count": [1, 1, 0],
            "click_count": [1, 0, 2],
            "favorite_count": [0, 0, 0],
            "to_cart_count": [1, 0, 0],
            "unique_users": [2, 1, 2],
            "unique_sessions": [3, 1, 2],
            "window_start": ["2026-05-01", "2026-05-01", "2026-05-01"],
            "window_end": ["2026-05-02", "2026-05-02", "2026-05-02"],
        }
    ))

    scorer = CoVisitationScorer(
        method="calibrated_multichannel",
        action_shares={
            "view": 0.7,
            "click": 0.2,
            "favorite": 0.05,
            "to_cart": 0.05,
        },
    )

    eager = scorer.score(pair_aggregates)
    lazy = scorer.score_lazy(pair_aggregates.lazy()).collect()

    assert lazy.equals(eager)

def test_score_lazy_with_popularity_normalization_matches_score() -> None:
    pair_aggregates = _complete_aggregates(pl.DataFrame(
        {
            "item_id": [10, 10],
            "similar_item_id": [20, 30],
            "pair_count": [3, 2],
            "view_count": [1, 1],
            "click_count": [1, 1],
            "favorite_count": [0, 0],
            "to_cart_count": [1, 0],
            "unique_users": [2, 2],
            "unique_sessions": [3, 2],
            "window_start": ["2026-05-01", "2026-05-01"],
            "window_end": ["2026-05-02", "2026-05-02"],
        }
    ))

    item_popularity = pl.DataFrame(
        {
            "item_id": [10, 20, 30],
            "events_count": [10, 5, 2],
            "unique_users": [4, 3, 2],
            "views_count": [5, 3, 1],
            "clicks_count": [2, 1, 1],
            "favorites_count": [1, 0, 0],
            "to_cart_count": [2, 1, 0],
        }
    ).select(schemas.ITEM_POPULARITY_COLUMNS)

    scorer = CoVisitationScorer(
        method="calibrated_multichannel",
        normalize_by_item_popularity=True,
        popularity_column="unique_users",
        popularity_smoothing=1.0,
        popularity_power=0.5,
    )

    eager = scorer.score(pair_aggregates, item_popularity=item_popularity)
    lazy = scorer.score_lazy(
        pair_aggregates.lazy(),
        item_popularity=item_popularity.lazy(),
    ).collect()

    assert lazy.equals(eager)

def test_score_lazy_rejects_missing_popularity_column() -> None:
    pair_aggregates = _complete_aggregates(pl.DataFrame(
        {
            "item_id": [10],
            "similar_item_id": [20],
            "pair_count": [1],
            "view_count": [1],
            "click_count": [0],
            "favorite_count": [0],
            "to_cart_count": [0],
            "unique_users": [1],
            "unique_sessions": [1],
            "window_start": ["2026-05-01"],
            "window_end": ["2026-05-01"],
        }
    ))

    item_popularity = pl.DataFrame(
        {
            "item_id": [10, 20],
            "events_count": [1, 1],
            "unique_users": [1, 1],
            "views_count": [1, 1],
            "clicks_count": [0, 0],
            "favorites_count": [0, 0],
            "to_cart_count": [0, 0],
        }
    ).select(schemas.ITEM_POPULARITY_COLUMNS)

    scorer = CoVisitationScorer(
        normalize_by_item_popularity=True,
        popularity_column="missing_column",
    )

    with pytest.raises(ValueError, match="popularity_column 'missing_column' is missing"):
        scorer.score_lazy(pair_aggregates, item_popularity=item_popularity)
