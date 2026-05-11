"""Scoring for co-visitation pair aggregates."""

import math
from collections.abc import Mapping
from dataclasses import dataclass, field

import polars as pl

from ozon_similar_products.data import schemas
from ozon_similar_products.data.frames import empty_contract_frame
from ozon_similar_products.data.validation import (
    validate_pair_aggregates,
    validate_pair_scores,
)

FrameLike = pl.DataFrame | pl.LazyFrame

DEFAULT_BUSINESS_WEIGHTS: dict[str, float] = {
    "view": 1.0,
    "click": 3.0,
    "favorite": 6.0,
    "to_cart": 8.0,
}

DEFAULT_MAX_FREQUENCY_BOOST: dict[str, float] = {
    "view": 1.0,
    "click": 10.0,
    "favorite": 15.0,
    "to_cart": 30.0,
}

_COUNT_COLUMNS = {
    "view": "view_count",
    "click": "click_count",
    "favorite": "favorite_count",
    "to_cart": "to_cart_count",
}


def _as_frame(frame: FrameLike) -> pl.DataFrame:
    """Return an eager DataFrame for scoring."""
    if isinstance(frame, pl.LazyFrame):
        return frame.collect()
    return frame


def _empty_pair_scores() -> pl.DataFrame:
    """Return an empty pair-scores DataFrame with the public contract columns."""
    return empty_contract_frame(schemas.PAIR_SCORES_COLUMNS)


def _infer_action_shares(pair_aggregates: pl.DataFrame) -> dict[str, float]:
    """Infer channel shares from aggregate counts when config shares are absent."""
    totals: dict[str, float] = {}
    for action_type, count_column in _COUNT_COLUMNS.items():
        totals[action_type] = float(pair_aggregates[count_column].sum() or 0.0)

    total_count = sum(totals.values())
    if total_count <= 0.0:
        return {action_type: 0.0 for action_type in _COUNT_COLUMNS}
    return {action_type: count / total_count for action_type, count in totals.items()}


def _effective_weight(
    action_type: str,
    business_weights: Mapping[str, float],
    action_shares: Mapping[str, float],
    beta: float,
    reference_action_type: str,
    max_frequency_boost: Mapping[str, float],
) -> float:
    """Compute calibrated channel weight with soft inverse-frequency normalization."""
    business_weight = float(business_weights.get(action_type, 0.0))
    action_share = float(action_shares.get(action_type, 0.0))
    reference_share = float(action_shares.get(reference_action_type, 0.0))

    if business_weight <= 0.0:
        return 0.0
    if beta <= 0.0 or action_share <= 0.0 or reference_share <= 0.0:
        return business_weight

    frequency_boost = (reference_share / action_share) ** beta
    capped_boost = min(
        frequency_boost,
        float(max_frequency_boost.get(action_type, frequency_boost)),
    )
    return business_weight * capped_boost


@dataclass(frozen=True)
class CoVisitationScorer:
    """Score item-item pair aggregates.

    Supported methods:
    - ``pair_count``: unweighted baseline;
    - ``calibrated_multichannel``: strong MVP scorer over separate channels.
    """

    method: str = "pair_count"
    business_weights: Mapping[str, float] = field(default_factory=lambda: DEFAULT_BUSINESS_WEIGHTS)
    action_shares: Mapping[str, float] | None = None
    beta: float = 0.5
    reference_action_type: str = "view"
    max_frequency_boost: Mapping[str, float] = field(
        default_factory=lambda: DEFAULT_MAX_FREQUENCY_BOOST
    )
    min_pair_count: int = 1
    min_unique_users: int = 1
    min_unique_sessions: int = 1

    def __post_init__(self) -> None:
        if self.beta < 0.0 or self.beta > 1.0:
            raise ValueError("beta must be between 0 and 1")
        if self.min_pair_count < 1:
            raise ValueError("min_pair_count must be at least 1")
        if self.min_unique_users < 1:
            raise ValueError("min_unique_users must be at least 1")
        if self.min_unique_sessions < 1:
            raise ValueError("min_unique_sessions must be at least 1")

    def score(self, pair_aggregates: FrameLike) -> pl.DataFrame:
        """Compute pair scores from pair aggregates."""
        validate_pair_aggregates(pair_aggregates)
        aggregates = _as_frame(pair_aggregates)

        if aggregates.is_empty():
            scores = _empty_pair_scores()
            validate_pair_scores(scores)
            return scores

        filtered = aggregates.filter(
            (pl.col("pair_count") >= self.min_pair_count)
            & (pl.col("unique_users") >= self.min_unique_users)
            & (pl.col("unique_sessions") >= self.min_unique_sessions)
        )

        if filtered.is_empty():
            scores = _empty_pair_scores()
            validate_pair_scores(scores)
            return scores

        score_expr = self._score_expression(filtered)
        scores = (
            filtered.with_columns(score_expr.cast(pl.Float64).alias("score"))
            .select(schemas.PAIR_SCORES_COLUMNS)
            .sort(["item_id", "score", "similar_item_id"], descending=[False, True, False])
        )

        validate_pair_scores(scores)
        return scores

    def _score_expression(self, pair_aggregates: pl.DataFrame) -> pl.Expr:
        """Build the Polars scoring expression for the selected method."""
        if self.method == "pair_count":
            return pl.col("pair_count").cast(pl.Float64)
        if self.method != "calibrated_multichannel":
            raise ValueError("method must be one of: pair_count, calibrated_multichannel")

        action_shares = dict(self.action_shares or _infer_action_shares(pair_aggregates))
        score_expr = pl.lit(0.0)
        for action_type, count_column in _COUNT_COLUMNS.items():
            effective_weight = _effective_weight(
                action_type=action_type,
                business_weights=self.business_weights,
                action_shares=action_shares,
                beta=self.beta,
                reference_action_type=self.reference_action_type,
                max_frequency_boost=self.max_frequency_boost,
            )
            if math.isclose(effective_weight, 0.0):
                continue
            score_expr += effective_weight * (pl.col(count_column).cast(pl.Float64) + 1.0).log()
        return score_expr
