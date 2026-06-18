"""Offline train/validation split helpers."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date

import polars as pl

FrameLike = pl.DataFrame | pl.LazyFrame


@dataclass(frozen=True)
class TemporalSplitConfig:
    """Temporal split config for offline evaluation."""

    train_until_date: date
    validation_start_date: date
    validation_end_date: date
    date_column: str = "event_date"

    def __post_init__(self) -> None:
        if not self.date_column:
            raise ValueError("date_column must be a non-empty string")
        if self.validation_start_date <= self.train_until_date:
            raise ValueError("validation_start_date must be after train_until_date")
        if self.validation_start_date > self.validation_end_date:
            raise ValueError("validation_start_date must be <= validation_end_date")


def _as_lazy(frame: FrameLike) -> pl.LazyFrame:
    if isinstance(frame, pl.LazyFrame):
        return frame
    return frame.lazy()


def _frame_columns(frame: FrameLike) -> list[str]:
    if isinstance(frame, pl.LazyFrame):
        return list(frame.collect_schema().names())
    return list(frame.columns)


def split_train_validation(
    frame: FrameLike,
    config: TemporalSplitConfig,
) -> tuple[pl.DataFrame, pl.DataFrame]:
    """Split a frame into train and validation partitions by date.

    Train contains rows with ``date_column <= train_until_date``.
    Validation contains rows with
    ``validation_start_date <= date_column <= validation_end_date``.
    """

    if config.date_column not in _frame_columns(frame):
        raise ValueError(f"date column is missing: {config.date_column}")

    split_date_column = "__evaluation_split_date"
    lazy_frame = _as_lazy(frame).with_columns(
        pl.col(config.date_column).cast(pl.Date, strict=False).alias(split_date_column)
    )

    train = (
        lazy_frame.filter(pl.col(split_date_column) <= config.train_until_date)
        .drop(split_date_column)
        .collect()
    )
    validation = (
        lazy_frame.filter(
            pl.col(split_date_column).is_between(
                config.validation_start_date,
                config.validation_end_date,
            )
        )
        .drop(split_date_column)
        .collect()
    )

    return train, validation
