"""Ground-truth builders for offline recommendation evaluation."""

from __future__ import annotations

from collections.abc import Mapping

import polars as pl

from ozon_similar_products.data.frames import empty_contract_frame
from ozon_similar_products.data.validation import validate_daily_pair_counts

FrameLike = pl.DataFrame | pl.LazyFrame

GROUND_TRUTH_COLUMNS = [
    "item_id",
    "relevant_item_id",
    "relevance",
    "target_action_type",
    "evidence_count",
    "view_count",
    "click_count",
    "favorite_count",
    "to_cart_count",
]

DEFAULT_ACTION_RELEVANCE_WEIGHTS: dict[str, float] = {
    "view": 0.1,
    "click": 0.3,
    "favorite": 0.6,
    "to_cart": 1.0,
}


def _collect_if_lazy(frame: FrameLike) -> pl.DataFrame:
    if isinstance(frame, pl.LazyFrame):
        return frame.collect()
    return frame


def _empty_ground_truth() -> pl.DataFrame:
    return empty_contract_frame(GROUND_TRUTH_COLUMNS)


def validate_ground_truth(frame: FrameLike) -> None:
    columns = (
        list(frame.collect_schema().names())
        if isinstance(frame, pl.LazyFrame)
        else list(frame.columns)
    )
    missing = set(GROUND_TRUTH_COLUMNS) - set(columns)
    if missing:
        raise ValueError(f"ground_truth: missing expected columns: {sorted(missing)}")


def _weighted_relevance_expr(action_weights: Mapping[str, float]) -> pl.Expr:
    return (
        pl.col("view_count").cast(pl.Float64) * float(action_weights.get("view", 0.0))
        + pl.col("click_count").cast(pl.Float64) * float(action_weights.get("click", 0.0))
        + pl.col("favorite_count").cast(pl.Float64) * float(action_weights.get("favorite", 0.0))
        + pl.col("to_cart_count").cast(pl.Float64) * float(action_weights.get("to_cart", 0.0))
    ).alias("relevance")


def _binary_relevance_expr() -> pl.Expr:
    return pl.lit(1.0).alias("relevance")


def _target_action_type_expr() -> pl.Expr:
    """Return the strongest observed validation action for the target item."""

    return (
        pl.when(pl.col("to_cart_count") > 0)
        .then(pl.lit("to_cart"))
        .when(pl.col("favorite_count") > 0)
        .then(pl.lit("favorite"))
        .when(pl.col("click_count") > 0)
        .then(pl.lit("click"))
        .when(pl.col("view_count") > 0)
        .then(pl.lit("view"))
        .otherwise(pl.lit(None, dtype=pl.Utf8))
        .alias("target_action_type")
    )


def build_ground_truth_from_daily_pair_counts(
    daily_pair_counts: FrameLike,
    *,
    relevance_mode: str = "binary",
    action_weights: Mapping[str, float] | None = None,
    min_relevance: float = 0.0,
) -> pl.DataFrame:
    """Build compact evaluation ground truth from validation daily pair counts.

    This is the scalable path for experiment evaluation. It reuses the same
    item-pair semantics as the main pipeline and avoids a large all-window
    session self-join.
    """

    validate_daily_pair_counts(daily_pair_counts)

    if min_relevance < 0.0:
        raise ValueError("min_relevance must be >= 0")

    if relevance_mode not in {"binary", "graded"}:
        raise ValueError("relevance_mode must be either 'binary' or 'graded'")

    weights = dict(action_weights or DEFAULT_ACTION_RELEVANCE_WEIGHTS)
    if relevance_mode == "graded" and not weights:
        raise ValueError("action_weights must not be empty")

    pair_counts = _collect_if_lazy(daily_pair_counts)

    if pair_counts.is_empty():
        ground_truth = _empty_ground_truth()
        validate_ground_truth(ground_truth)
        return ground_truth

    aggregated = (
        pair_counts.group_by(["item_id", "similar_item_id"])
        .agg(
            pl.col("pair_count").sum().alias("evidence_count"),
            pl.col("view_count").sum().alias("view_count"),
            pl.col("click_count").sum().alias("click_count"),
            pl.col("favorite_count").sum().alias("favorite_count"),
            pl.col("to_cart_count").sum().alias("to_cart_count"),
        )
        .with_columns(
            _binary_relevance_expr()
            if relevance_mode == "binary"
            else _weighted_relevance_expr(weights),
            _target_action_type_expr(),
        )
        .filter(pl.col("relevance") > min_relevance)
        .select(
            pl.col("item_id"),
            pl.col("similar_item_id").alias("relevant_item_id"),
            pl.col("relevance"),
            pl.col("target_action_type"),
            pl.col("evidence_count"),
            pl.col("view_count"),
            pl.col("click_count"),
            pl.col("favorite_count"),
            pl.col("to_cart_count"),
        )
        .sort(["item_id", "relevance", "relevant_item_id"], descending=[False, True, False])
    )

    if aggregated.is_empty():
        ground_truth = _empty_ground_truth()
        validate_ground_truth(ground_truth)
        return ground_truth

    ground_truth = aggregated.select(GROUND_TRUTH_COLUMNS)
    validate_ground_truth(ground_truth)
    return ground_truth
