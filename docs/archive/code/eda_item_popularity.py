"""Archived EDA helper.

This module is kept for historical context only.
Do not use it in the production pipeline.

The current architecture keeps item popularity factual and applies
business weights only in CoVisitationScorer.
"""

from collections.abc import Mapping, Sequence
from pathlib import Path

import polars as pl

from ozon_similar_products.config import load_yaml_config

FrameLike = pl.DataFrame | pl.LazyFrame

DEFAULT_EXCLUDED_ACTION_TYPES = {"search"}


def _as_lazy(frame: FrameLike) -> pl.LazyFrame:
    if isinstance(frame, pl.LazyFrame):
        return frame
    return frame.lazy()


def _collect_schema(frame: FrameLike) -> pl.Schema:
    if isinstance(frame, pl.LazyFrame):
        return frame.collect_schema()
    return frame.schema


def load_event_weights(config_path: str | Path = "configs/baseline.yaml") -> dict[str, float]:
    """Load event weights from the baseline config."""
    config = load_yaml_config(config_path)
    return {action: float(weight) for action, weight in config["event_weights"].items()}


def default_item_action_types(event_weights: Mapping[str, float]) -> tuple[str, ...]:
    """Return action types that represent direct item interactions."""
    return tuple(
        action for action in event_weights.keys() if action not in DEFAULT_EXCLUDED_ACTION_TYPES
    )


def weighted_item_popularity(
        events: FrameLike,
        event_weights: Mapping[str, float],
        allowed_action_types: Sequence[str] | None = None,
        item_id_col: str = "item_id",
        action_col: str = "action_type",
        user_id_col: str = "user_id",
        timestamp_col: str = "timestamp",
        top_n: int | None = None,
) -> pl.DataFrame:
    """Calculate item popularity using weighted direct item interactions."""
    schema = _collect_schema(events)
    required_columns = {item_id_col, action_col}
    missing_columns = required_columns - set(schema.keys())
    if missing_columns:
        raise ValueError(f"Missing columns for item popularity: {sorted(missing_columns)}")

    action_types = tuple(allowed_action_types or default_item_action_types(event_weights))
    missing_weights = set(action_types) - set(event_weights.keys())
    if missing_weights:
        raise ValueError(f"Missing event weights for actions: {sorted(missing_weights)}")

    if not action_types:
        return pl.DataFrame(
            schema={
                item_id_col: schema[item_id_col],
                "events": pl.Int64,
                "weighted_events": pl.Float64,
            }
        )

    weights_frame = pl.DataFrame(
        {
            action_col: list(action_types),
            "event_weight": [float(event_weights[action]) for action in action_types],
        }
    ).lazy()

    filtered = (
        _as_lazy(events)
        .filter(pl.col(item_id_col).is_not_null())
        .filter(pl.col(action_col).is_in(action_types))
        .join(weights_frame, on=action_col, how="inner")
    )

    aggregations: list[pl.Expr] = [
        pl.len().alias("events"),
        pl.col("event_weight").sum().alias("weighted_events"),
        pl.col(action_col).drop_nulls().n_unique().alias("action_types"),
    ]
    if user_id_col in schema:
        aggregations.append(pl.col(user_id_col).drop_nulls().n_unique().alias("unique_users"))
    if timestamp_col in schema:
        aggregations.extend(
            [
                pl.col(timestamp_col).min().alias("first_event_at"),
                pl.col(timestamp_col).max().alias("last_event_at"),
            ]
        )

    result = (
        filtered.group_by(item_id_col)
        .agg(aggregations)
        .sort(["weighted_events", "events"], descending=[True, True])
    )
    if top_n is not None:
        result = result.limit(top_n)
    return result.collect()
