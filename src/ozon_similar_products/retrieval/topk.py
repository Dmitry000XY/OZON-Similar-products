"""Top-K selection for co-visitation recommendations."""

from __future__ import annotations

from dataclasses import dataclass

import polars as pl

from ozon_similar_products.data import schemas
from ozon_similar_products.data.validation import (
    validate_pair_scores,
    validate_recommendations,
)

FrameLike = pl.DataFrame | pl.LazyFrame

_DIAGNOSTIC_COLUMNS = [
    "pair_count",
    "view_count",
    "click_count",
    "favorite_count",
    "to_cart_count",
    "weighted_pair_count",
    "weighted_view_count",
    "weighted_click_count",
    "weighted_favorite_count",
    "weighted_to_cart_count",
    "unique_users",
    "unique_sessions",
]

_DEDUPLICATION_SORT_COLUMNS = [
    "item_id",
    "similar_item_id",
    "score",
    "pair_count",
    "to_cart_count",
    "favorite_count",
    "click_count",
    "view_count",
    "unique_users",
    "unique_sessions",
]

_DEDUPLICATION_SORT_DESCENDING = [
    False,
    False,
    True,
    True,
    True,
    True,
    True,
    True,
    True,
    True,
]


@dataclass(frozen=True)
class TopKSelector:
    """Select top-K similar items for each item.

    The selector receives scored item-item pairs and turns them into a ranked
    recommendations table. It does not calculate scores and does not save any
    files. Its responsibility is limited to filtering, stable ordering, ranking,
    and keeping only the best candidates per item.

    The selector treats ``score`` as already prepared by the scorer. Channel
    counts such as ``view_count`` and ``to_cart_count`` are kept only as
    diagnostics for manual review and debugging.
    """

    top_k: int = 20
    source: str = "behavioral"
    min_pair_count: int | None = None
    min_unique_users: int | None = None
    min_unique_sessions: int | None = None
    deduplicate: bool = True

    def __post_init__(self) -> None:
        if self.top_k <= 0:
            raise ValueError("top_k must be a positive integer")

        for name, value in (
            ("min_pair_count", self.min_pair_count),
            ("min_unique_users", self.min_unique_users),
            ("min_unique_sessions", self.min_unique_sessions),
        ):
            if value is not None and value < 0:
                raise ValueError(f"{name} must be non-negative or None")

        if not self.source:
            raise ValueError("source must be a non-empty string")

    def select(self, pair_scores: FrameLike) -> pl.DataFrame:
        """Select top-K recommendations per item_id.

        Args:
            pair_scores: DataFrame or LazyFrame with the pair scores contract.

        Returns:
            DataFrame with the recommendations contract. Diagnostic pair-score
            columns are preserved when they are present in the input.
        """
        pair_scores = _with_weighted_count_columns(pair_scores)
        validate_pair_scores(pair_scores)

        ranked = (
            _as_lazy(pair_scores)
            .filter(pl.col("item_id").is_not_null())
            .filter(pl.col("similar_item_id").is_not_null())
            .filter(pl.col("score").is_not_null())
            .filter(pl.col("item_id") != pl.col("similar_item_id"))
        )
        ranked = self._apply_thresholds(ranked)
        if self.deduplicate:
            ranked = self._deduplicate_pairs(ranked)
        ranked = self._rank_candidates(ranked)

        recommendations = ranked.select(_output_columns(pair_scores)).collect()
        validate_recommendations(recommendations)
        return recommendations

    def _apply_thresholds(self, frame: pl.LazyFrame) -> pl.LazyFrame:
        """Apply optional quality thresholds to pair statistics."""
        if self.min_pair_count is not None:
            frame = frame.filter(pl.col("pair_count") >= self.min_pair_count)
        if self.min_unique_users is not None:
            frame = frame.filter(pl.col("unique_users") >= self.min_unique_users)
        if self.min_unique_sessions is not None:
            frame = frame.filter(pl.col("unique_sessions") >= self.min_unique_sessions)
        return frame

    @staticmethod
    def _deduplicate_pairs(frame: pl.LazyFrame) -> pl.LazyFrame:
        """Keep one best row for each item_id/similar_item_id pair."""
        return (
            frame.sort(
                _DEDUPLICATION_SORT_COLUMNS,
                descending=_DEDUPLICATION_SORT_DESCENDING,
            )
            .unique(
                subset=["item_id", "similar_item_id"],
                keep="first",
                maintain_order=True,
            )
        )

    def _rank_candidates(self, frame: pl.LazyFrame) -> pl.LazyFrame:
        """Stable-sort candidates, assign rank, and keep top-K per item."""
        return (
            frame.sort(
                ["item_id", "score", "similar_item_id"],
                descending=[False, True, False],
            )
            .with_columns(
                pl.col("similar_item_id")
                .cum_count()
                .over("item_id")
                .cast(pl.Int64)
                .alias("rank")
            )
            .filter(pl.col("rank") <= self.top_k)
            .with_columns(pl.lit(self.source).alias("source"))
        )


def _as_lazy(frame: FrameLike) -> pl.LazyFrame:
    """Return a LazyFrame for both eager and lazy Polars inputs."""
    if isinstance(frame, pl.LazyFrame):
        return frame
    return frame.lazy()


def _frame_columns(frame: FrameLike) -> list[str]:
    """Return column names for DataFrame or LazyFrame."""
    if isinstance(frame, pl.LazyFrame):
        return list(frame.collect_schema().names())
    return list(frame.columns)


def _with_weighted_count_columns(frame: FrameLike) -> pl.LazyFrame:
    input_columns = set(_frame_columns(frame))
    expressions = []
    for raw_column, weighted_column in schemas.WEIGHTED_COUNT_BY_RAW_COLUMN.items():
        if weighted_column in input_columns:
            expressions.append(pl.col(weighted_column).cast(pl.Float64).alias(weighted_column))
        else:
            expressions.append(pl.col(raw_column).cast(pl.Float64).alias(weighted_column))
    return _as_lazy(frame).with_columns(expressions)


def _output_columns(pair_scores: FrameLike) -> list[str]:
    """Build output column order for recommendations."""
    input_columns = set(_frame_columns(pair_scores))
    diagnostic_columns = [
        column for column in _DIAGNOSTIC_COLUMNS if column in input_columns
    ]
    return [*schemas.RECOMMENDATIONS_COLUMNS, *diagnostic_columns]
