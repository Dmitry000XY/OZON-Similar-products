"""Ground-truth builders for offline recommendation evaluation."""

from __future__ import annotations

from collections.abc import Mapping, Sequence

import polars as pl

from ozon_similar_products.data.frames import empty_contract_frame
from ozon_similar_products.data.validation import validate_sessions

FrameLike = pl.DataFrame | pl.LazyFrame

GROUND_TRUTH_COLUMNS = [
    "item_id",
    "relevant_item_id",
    "relevance",
    "target_action_type",
    "evidence_count",
]

DEFAULT_ACTION_RELEVANCE_WEIGHTS: dict[str, float] = {
    "view": 0.1,
    "click": 0.3,
    "favorite": 0.6,
    "to_cart": 1.0,
}


def _as_lazy(frame: FrameLike) -> pl.LazyFrame:
    if isinstance(frame, pl.LazyFrame):
        return frame
    return frame.lazy()


def _empty_ground_truth() -> pl.DataFrame:
    return empty_contract_frame(GROUND_TRUTH_COLUMNS)


def _relevance_expr(action_weights: Mapping[str, float]) -> pl.Expr:
    expr = pl.lit(0.0)
    for action_type, weight in action_weights.items():
        expr = pl.when(pl.col("action_type") == action_type).then(float(weight)).otherwise(expr)
    return expr.cast(pl.Float64).alias("__relevance")


def validate_ground_truth(frame: FrameLike) -> None:
    columns = (
        list(frame.collect_schema().names())
        if isinstance(frame, pl.LazyFrame)
        else list(frame.columns)
    )
    missing = set(GROUND_TRUTH_COLUMNS) - set(columns)
    if missing:
        raise ValueError(f"ground_truth: missing expected columns: {sorted(missing)}")


def build_ground_truth_from_sessions(
    sessions: FrameLike,
    *,
    action_weights: Mapping[str, float] | None = None,
    item_action_types: Sequence[str] | None = None,
    min_relevance: float = 0.0,
) -> pl.DataFrame:
    """Build directed item-to-item ground truth from validation sessions.

    For every validation session, each item is treated as a source item and
    every other item in the same session is treated as a relevant candidate.

    Target-item action type defines relevance strength. By default, ``to_cart``
    is the strongest signal, while view/click/favorite are weaker signals.
    """

    validate_sessions(sessions)

    if min_relevance < 0.0:
        raise ValueError("min_relevance must be >= 0")

    weights = dict(action_weights or DEFAULT_ACTION_RELEVANCE_WEIGHTS)
    if not weights:
        raise ValueError("action_weights must not be empty")

    allowed_action_types = list(item_action_types or weights.keys())
    if not allowed_action_types:
        raise ValueError("item_action_types must not be empty")

    session_items = (
        _as_lazy(sessions)
        .select(
            "user_id",
            "session_index",
            "item_id",
            "action_type",
        )
        .filter(pl.col("item_id").is_not_null())
        .filter(pl.col("action_type").is_in(allowed_action_types))
        .with_columns(_relevance_expr(weights))
        .filter(pl.col("__relevance") > min_relevance)
        .group_by(["user_id", "session_index", "item_id"])
        .agg(
            pl.col("__relevance").max().alias("relevance"),
            pl.col("action_type")
            .sort_by(pl.col("__relevance"), descending=True)
            .first()
            .alias("target_action_type"),
            pl.len().alias("evidence_count"),
        )
    )

    pairs = (
        session_items.join(
            session_items,
            on=["user_id", "session_index"],
            how="inner",
            suffix="_target",
        )
        .filter(pl.col("item_id") != pl.col("item_id_target"))
        .select(
            pl.col("item_id"),
            pl.col("item_id_target").alias("relevant_item_id"),
            pl.col("relevance_target").alias("relevance"),
            pl.col("target_action_type_target").alias("target_action_type"),
            pl.col("evidence_count_target").alias("evidence_count"),
        )
        .collect()
    )

    if pairs.is_empty():
        ground_truth = _empty_ground_truth()
        validate_ground_truth(ground_truth)
        return ground_truth

    best_target_actions = (
        pairs.sort(
            ["item_id", "relevant_item_id", "relevance", "target_action_type"],
            descending=[False, False, True, False],
        )
        .unique(
            subset=["item_id", "relevant_item_id"],
            keep="first",
            maintain_order=True,
        )
        .select(["item_id", "relevant_item_id", "target_action_type"])
    )

    ground_truth = (
        pairs.group_by(["item_id", "relevant_item_id"])
        .agg(
            pl.col("relevance").max().alias("relevance"),
            pl.col("evidence_count").sum().alias("evidence_count"),
        )
        .join(
            best_target_actions,
            on=["item_id", "relevant_item_id"],
            how="left",
        )
        .select(GROUND_TRUTH_COLUMNS)
        .sort(["item_id", "relevance", "relevant_item_id"], descending=[False, True, False])
    )

    validate_ground_truth(ground_truth)
    return ground_truth
