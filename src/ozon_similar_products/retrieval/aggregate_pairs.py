"""Aggregate daily multichannel item pairs over a rolling window."""

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date
from pathlib import Path

import polars as pl

from ozon_similar_products.data import schemas
from ozon_similar_products.data.frames import empty_contract_frame
from ozon_similar_products.data.validation import (
    validate_daily_item_pairs,
    validate_pair_aggregates,
)

FrameLike = pl.DataFrame | pl.LazyFrame


def _as_lazy(frame: FrameLike) -> pl.LazyFrame:
    """Return a LazyFrame for both eager and lazy Polars inputs."""
    if isinstance(frame, pl.LazyFrame):
        return frame
    return frame.lazy()


def _empty_pair_aggregates() -> pl.DataFrame:
    """Return an empty pair-aggregates DataFrame with the public contract columns."""
    return empty_contract_frame(schemas.PAIR_AGGREGATES_COLUMNS)


def _validate_iso_date(value: str, name: str) -> date:
    """Validate and parse an ISO date string used as a window boundary."""
    try:
        return date.fromisoformat(value)
    except ValueError as error:
        raise ValueError(f"{name} must be an ISO date string YYYY-MM-DD") from error


def _validate_window_bounds(window_start: str, window_end: str) -> tuple[date, date]:
    """Validate and parse inclusive rolling-window bounds."""
    start_date = _validate_iso_date(window_start, "window_start")
    end_date = _validate_iso_date(window_end, "window_end")
    if start_date > end_date:
        raise ValueError("window_start must be less than or equal to window_end")
    return start_date, end_date


def _aggregate_pairs_lazy(
        pairs_window: pl.LazyFrame,
        window_start: str,
        window_end: str,
) -> pl.DataFrame:
    """Aggregate a lazy daily-pairs scan into pair aggregates."""
    start_date, end_date = _validate_window_bounds(window_start, window_end)

    filtered_pairs = (
        pairs_window
        .with_columns(pl.col("pair_date").cast(pl.Date, strict=False).alias("pair_date"))
        .filter(pl.col("pair_date").is_between(start_date, end_date))
    )

    aggregates = (
        filtered_pairs.group_by(["item_id", "similar_item_id"])
        .agg(
            pl.len().alias("pair_count"),
            (pl.col("signal_type") == "view").sum().alias("view_count"),
            (pl.col("signal_type") == "click").sum().alias("click_count"),
            (pl.col("signal_type") == "favorite").sum().alias("favorite_count"),
            (pl.col("signal_type") == "to_cart").sum().alias("to_cart_count"),
            pl.col("user_id").n_unique().alias("unique_users"),
            pl.struct(["user_id", "session_id"]).n_unique().alias("unique_sessions"),
        )
        .with_columns(
            pl.lit(window_start).alias("window_start"),
            pl.lit(window_end).alias("window_end"),
        )
        .select(schemas.PAIR_AGGREGATES_COLUMNS)
        .sort(["item_id", "similar_item_id"])
        .collect()
    )

    if aggregates.is_empty():
        aggregates = _empty_pair_aggregates()

    validate_pair_aggregates(aggregates)
    return aggregates


@dataclass(frozen=True)
class PairAggregator:
    """Aggregate daily item pairs over a rolling window.

    The aggregator does not apply business weights. It only preserves factual
    pair statistics by signal channel. This avoids double weighting and allows
    the scorer to change calibration without rebuilding pairs.
    """

    @staticmethod
    def aggregate_window(
            daily_pairs: list[FrameLike],
            window_start: str,
            window_end: str,
    ) -> pl.DataFrame:
        """Aggregate daily pairs into pair aggregates for an inclusive date window."""
        _validate_window_bounds(window_start, window_end)

        if not daily_pairs:
            empty = _empty_pair_aggregates()
            validate_pair_aggregates(empty)
            return empty

        for pairs in daily_pairs:
            validate_daily_item_pairs(pairs)

        pairs_window = pl.concat([_as_lazy(pairs) for pairs in daily_pairs])
        return _aggregate_pairs_lazy(
            pairs_window=pairs_window,
            window_start=window_start,
            window_end=window_end,
        )

    @staticmethod
    def aggregate_window_from_paths(
            daily_pair_paths: Sequence[str | Path],
            window_start: str,
            window_end: str,
    ) -> pl.DataFrame:
        """Aggregate daily pair parquet files into pair aggregates.

        This path avoids materializing all daily pairs in Python memory before
        aggregation. Polars scans parquet files lazily, filters the requested
        window and collects only the aggregated pair statistics.
        """
        _validate_window_bounds(window_start, window_end)

        if not daily_pair_paths:
            empty = _empty_pair_aggregates()
            validate_pair_aggregates(empty)
            return empty

        paths = [Path(path).as_posix() for path in daily_pair_paths]
        pairs_scan = pl.scan_parquet(paths)

        validate_daily_item_pairs(pairs_scan)

        return _aggregate_pairs_lazy(
            pairs_window=pairs_scan,
            window_start=window_start,
            window_end=window_end,
        )
