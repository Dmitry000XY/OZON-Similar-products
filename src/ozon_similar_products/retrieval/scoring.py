"""Scoring for co-visitation pair aggregates."""

import math
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any

import polars as pl

from ozon_similar_products.data import schemas
from ozon_similar_products.data.frames import empty_contract_frame
from ozon_similar_products.data.validation import (
    validate_item_popularity,
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

_VALID_METHODS = {"pair_count", "calibrated_multichannel"}
_VALID_COUNT_SOURCES = {"raw", "weighted"}
_VALID_COUNT_TRANSFORMS = {"log", "linear", "sqrt"}


def _as_mapping(value: Any) -> Mapping[str, Any]:
    """Return a string-key mapping or an empty mapping fallback."""
    if isinstance(value, Mapping):
        return value
    return {}


def _as_str(value: Any, default: str) -> str:
    """Return string value or fallback to default."""
    if isinstance(value, str):
        return value
    return default


def _as_float(value: Any, default: float) -> float:
    """Return float-convertible scalar or fallback to default."""
    if isinstance(value, bool):
        return default
    if isinstance(value, (int, float, str)):
        try:
            return float(value)
        except ValueError:
            return default
    return default


def _as_optional_float(value: Any, name: str) -> float | None:
    """Return optional float threshold with strict bool rejection."""
    if value is None:
        return None
    if isinstance(value, bool):
        raise ValueError(f"{name} must be a number or null")
    if isinstance(value, int | float | str):
        parsed = float(value)
    else:
        raise TypeError(f"{name} must be a number or null")
    if parsed < 0.0:
        raise ValueError(f"{name} must be non-negative or null")
    return parsed


def _as_int(value: Any, default: int) -> int:
    """Return int-convertible scalar or fallback to default."""
    if isinstance(value, bool):
        return default
    if isinstance(value, (int, float, str)):
        try:
            return int(value)
        except ValueError:
            return default
    return default


def _as_strict_bool(value: Any, default: bool, allow_int: bool = True) -> bool:
    """Return strict bool from bool/string (and optional int), or raise ValueError."""
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        normalized = value.strip().lower()
        if normalized == "true":
            return True
        if normalized == "false":
            return False
        raise ValueError(
            f"Expected boolean value for normalize_by_item_popularity, got {value!r}"
        )
    if allow_int and isinstance(value, int):
        if value == 1:
            return True
        if value == 0:
            return False
    raise ValueError(
        f"Expected boolean value for normalize_by_item_popularity, got {value!r}"
    )


def _as_frame(frame: FrameLike) -> pl.DataFrame:
    """Return an eager DataFrame for scoring."""
    if isinstance(frame, pl.LazyFrame):
        return frame.collect()
    return frame


def _as_lazy(frame: FrameLike) -> pl.LazyFrame:
    """Return a LazyFrame for both eager and lazy Polars inputs."""
    if isinstance(frame, pl.LazyFrame):
        return frame
    return frame.lazy()


def _empty_pair_scores() -> pl.DataFrame:
    """Return an empty pair-scores DataFrame with the public contract columns."""
    return empty_contract_frame(schemas.PAIR_SCORES_COLUMNS)


def _frame_columns(frame: FrameLike) -> set[str]:
    if isinstance(frame, pl.LazyFrame):
        return set(frame.collect_schema().names())
    return set(frame.columns)


def _with_weighted_count_columns(
        frame: FrameLike,
        *,
        require_weighted: bool,
) -> pl.LazyFrame:
    columns = _frame_columns(frame)
    missing_weighted = [
        weighted_column
        for weighted_column in schemas.WEIGHTED_COUNT_COLUMNS
        if weighted_column not in columns
    ]
    if require_weighted and missing_weighted:
        raise ValueError(
            "weighted count columns are required for scoring.count_source='weighted': "
            f"{missing_weighted}"
        )

    expressions = []
    for raw_column, weighted_column in schemas.WEIGHTED_COUNT_BY_RAW_COLUMN.items():
        if weighted_column in columns:
            expressions.append(pl.col(weighted_column).cast(pl.Float64).alias(weighted_column))
        else:
            expressions.append(pl.col(raw_column).cast(pl.Float64).alias(weighted_column))
    return _as_lazy(frame).with_columns(expressions)


def _effective_weight(
        action_type: str,
        business_weights: Mapping[str, float],
        action_shares: Mapping[str, float] | None,
        beta: float,
        reference_action_type: str,
        max_frequency_boost: Mapping[str, float],
) -> float:
    """Compute calibrated channel weight with soft inverse-frequency normalization."""
    business_weight = float(business_weights.get(action_type, 0.0))
    if business_weight <= 0.0:
        return 0.0

    if action_shares is None:
        return business_weight

    action_share = float(action_shares.get(action_type, 0.0))
    reference_share = float(action_shares.get(reference_action_type, 0.0))

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
    count_source: str = "raw"
    count_transform_method: str = "log"
    count_transform_smoothing: float = 1.0
    min_weighted_pair_count: float | None = None
    min_score: float | None = None
    normalize_by_item_popularity: bool = False
    popularity_column: str = "unique_users"
    popularity_smoothing: float = 1.0
    popularity_power: float = 0.5

    def __post_init__(self) -> None:
        if self.method not in _VALID_METHODS:
            raise ValueError("method must be one of: pair_count, calibrated_multichannel")
        if self.count_source not in _VALID_COUNT_SOURCES:
            raise ValueError("count_source must be one of: raw, weighted")
        if self.count_transform_method not in _VALID_COUNT_TRANSFORMS:
            raise ValueError("count_transform.method must be one of: log, linear, sqrt")
        if self.count_transform_smoothing <= 0.0:
            raise ValueError("count_transform.smoothing must be > 0")
        if self.beta < 0.0 or self.beta > 1.0:
            raise ValueError("beta must be between 0 and 1")
        if self.min_pair_count < 1:
            raise ValueError("min_pair_count must be at least 1")
        if self.min_unique_users < 1:
            raise ValueError("min_unique_users must be at least 1")
        if self.min_unique_sessions < 1:
            raise ValueError("min_unique_sessions must be at least 1")
        if self.min_weighted_pair_count is not None and self.min_weighted_pair_count < 0.0:
            raise ValueError("min_weighted_pair_count must be non-negative or null")
        if self.min_score is not None and self.min_score < 0.0:
            raise ValueError("min_score must be non-negative or null")
        if not self.popularity_column:
            raise ValueError("popularity_column must be a non-empty string")
        if self.popularity_smoothing < 0.0:
            raise ValueError("popularity_smoothing must be >= 0")
        if self.popularity_power < 0.0:
            raise ValueError("popularity_power must be >= 0")

    @classmethod
    def from_config(cls, config: Mapping[str, Any]) -> "CoVisitationScorer":
        scoring = _as_mapping(config.get("scoring", {}))
        calibration = _as_mapping(scoring.get("calibration", {}))
        popularity_normalization = _as_mapping(scoring.get("popularity_normalization", {}))

        method = _as_str(scoring.get("method", "pair_count"), "pair_count")
        beta = _as_float(scoring.get("beta", 0.5), 0.5)
        reference_action_type = _as_str(scoring.get("reference_action_type", "view"), "view")
        min_pair_count = _as_int(scoring.get("min_pair_count", 1), 1)
        min_unique_users = _as_int(scoring.get("min_unique_users", 1), 1)
        min_unique_sessions = _as_int(scoring.get("min_unique_sessions", 1), 1)
        count_transform = _as_mapping(scoring.get("count_transform", {}))
        popularity_column = _as_str(
            popularity_normalization.get("popularity_column", "unique_users"),
            "unique_users",
        )
        popularity_smoothing = _as_float(popularity_normalization.get("smoothing", 1.0), 1.0)
        popularity_power = _as_float(popularity_normalization.get("power", 0.5), 0.5)

        return cls(
            method=method,
            business_weights=scoring.get("business_weights", DEFAULT_BUSINESS_WEIGHTS),
            action_shares=calibration.get("action_shares_used_for_calibration"),
            beta=beta,
            reference_action_type=reference_action_type,
            max_frequency_boost=scoring.get("max_frequency_boost", DEFAULT_MAX_FREQUENCY_BOOST),
            min_pair_count=min_pair_count,
            min_unique_users=min_unique_users,
            min_unique_sessions=min_unique_sessions,
            count_source=_as_str(scoring.get("count_source", "raw"), "raw"),
            count_transform_method=_as_str(count_transform.get("method", "log"), "log"),
            count_transform_smoothing=_as_float(
                count_transform.get("smoothing", 1.0),
                1.0,
            ),
            min_weighted_pair_count=_as_optional_float(
                scoring.get("min_weighted_pair_count"),
                "scoring.min_weighted_pair_count",
            ),
            min_score=_as_optional_float(
                scoring.get("min_score"),
                "scoring.min_score",
            ),
            normalize_by_item_popularity=_as_strict_bool(
                scoring.get("normalize_by_item_popularity", False),
                default=False,
            ),
            popularity_column=popularity_column,
            popularity_smoothing=popularity_smoothing,
            popularity_power=popularity_power,
        )

    def score_lazy(
            self,
            pair_aggregates: FrameLike,
            item_popularity: FrameLike | None = None,
    ) -> pl.LazyFrame:
        """Build a lazy pair-score plan from pair aggregates."""
        pair_aggregates_lazy = _with_weighted_count_columns(
            pair_aggregates,
            require_weighted=self.count_source == "weighted",
        )
        validate_pair_aggregates(pair_aggregates_lazy)

        scored = (
            pair_aggregates_lazy
            .filter(
                (pl.col("pair_count") >= self.min_pair_count)
                & (pl.col("unique_users") >= self.min_unique_users)
                & (pl.col("unique_sessions") >= self.min_unique_sessions)
            )
        )
        if self.min_weighted_pair_count is not None:
            scored = scored.filter(pl.col("weighted_pair_count") >= self.min_weighted_pair_count)

        scored = scored.with_columns(
            self._base_score_expression().cast(pl.Float64).alias("base_score")
        )

        if self.normalize_by_item_popularity:
            if item_popularity is None:
                raise ValueError(
                    "item_popularity must be provided when normalize_by_item_popularity=True"
                )
            scored = self._apply_item_popularity_normalization_lazy(
                scored,
                item_popularity,
            )
        else:
            scored = scored.with_columns(pl.col("base_score").alias("score"))

        if self.min_score is not None:
            scored = scored.filter(pl.col("score") >= self.min_score)

        scores = (
            scored.select(schemas.PAIR_SCORES_COLUMNS)
            .sort(["item_id", "score", "similar_item_id"], descending=[False, True, False])
        )

        validate_pair_scores(scores)
        return scores

    def score(
            self,
            pair_aggregates: FrameLike,
            item_popularity: FrameLike | None = None,
    ) -> pl.DataFrame:
        """Compute pair scores from pair aggregates."""
        scores = self.score_lazy(pair_aggregates, item_popularity=item_popularity).collect()
        if scores.is_empty():
            scores = _empty_pair_scores()
        validate_pair_scores(scores)
        return scores

    def _base_score_expression(self) -> pl.Expr:
        """Build the Polars base-scoring expression for the selected method."""
        count_columns = self._count_columns()
        if self.method == "pair_count":
            return pl.col(count_columns["pair"]).cast(pl.Float64)

        score_expr = pl.lit(0.0)
        for action_type, count_column in count_columns["channels"].items():
            effective_weight = _effective_weight(
                action_type=action_type,
                business_weights=self.business_weights,
                action_shares=self.action_shares,
                beta=self.beta,
                reference_action_type=self.reference_action_type,
                max_frequency_boost=self.max_frequency_boost,
            )
            if math.isclose(effective_weight, 0.0):
                continue
            score_expr += effective_weight * self._count_transform_expr(pl.col(count_column))
        return score_expr

    def _count_columns(self) -> dict[str, Any]:
        if self.count_source == "raw":
            return {"pair": "pair_count", "channels": _COUNT_COLUMNS}
        return {
            "pair": "weighted_pair_count",
            "channels": {
                "view": "weighted_view_count",
                "click": "weighted_click_count",
                "favorite": "weighted_favorite_count",
                "to_cart": "weighted_to_cart_count",
            },
        }

    def _count_transform_expr(self, count_expr: pl.Expr) -> pl.Expr:
        count = count_expr.cast(pl.Float64)
        if self.count_transform_method == "linear":
            return count
        if self.count_transform_method == "sqrt":
            return count.sqrt()
        return (count + self.count_transform_smoothing).log()

    def _apply_item_popularity_normalization(
            self,
            scored: pl.DataFrame,
            item_popularity: FrameLike,
    ) -> pl.DataFrame:
        popularity_frame = _as_frame(item_popularity)
        validate_item_popularity(popularity_frame)

        if self.popularity_column not in popularity_frame.columns:
            raise ValueError(f"popularity_column '{self.popularity_column}' is missing")

        popularity_lookup = popularity_frame.select(
            ["item_id", pl.col(self.popularity_column).cast(pl.Float64).alias("popularity")]
        )

        normalized = (
            scored.join(
                popularity_lookup.rename({"item_id": "item_id", "popularity": "source_popularity"}),
                on="item_id",
                how="left",
            )
            .join(
                popularity_lookup.rename(
                    {"item_id": "similar_item_id", "popularity": "candidate_popularity"}
                ),
                on="similar_item_id",
                how="left",
            )
        )

        missing = normalized.filter(
            pl.col("source_popularity").is_null() | pl.col("candidate_popularity").is_null()
        )
        if not missing.is_empty():
            raise ValueError("Missing item popularity for some item_id/similar_item_id pairs")

        denominator = (
                              (pl.col("source_popularity") + self.popularity_smoothing)
                              * (pl.col("candidate_popularity") + self.popularity_smoothing)
                      ) ** self.popularity_power

        return normalized.with_columns((pl.col("base_score") / denominator).alias("score"))

    def _apply_item_popularity_normalization_lazy(
            self,
            scored: pl.LazyFrame,
            item_popularity: FrameLike,
    ) -> pl.LazyFrame:
        popularity_lazy = _as_lazy(item_popularity)
        validate_item_popularity(popularity_lazy)

        popularity_columns = set(popularity_lazy.collect_schema().names())
        if self.popularity_column not in popularity_columns:
            raise ValueError(f"popularity_column '{self.popularity_column}' is missing")

        popularity_lookup = popularity_lazy.select(
            ["item_id", pl.col(self.popularity_column).cast(pl.Float64).alias("popularity")]
        )

        normalized = (
            scored.join(
                popularity_lookup.rename(
                    {"item_id": "item_id", "popularity": "source_popularity"}
                ),
                on="item_id",
                how="left",
            )
            .join(
                popularity_lookup.rename(
                    {"item_id": "similar_item_id", "popularity": "candidate_popularity"}
                ),
                on="similar_item_id",
                how="left",
            )
        )

        denominator = (
            (pl.col("source_popularity") + self.popularity_smoothing)
            * (pl.col("candidate_popularity") + self.popularity_smoothing)
        ) ** self.popularity_power

        return normalized.with_columns(
            (pl.col("base_score") / denominator).alias("score")
        )
