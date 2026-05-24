"""Offline train/validation split skeleton."""

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


def split_train_validation(
    frame: FrameLike,
    config: TemporalSplitConfig,
) -> tuple[pl.DataFrame, pl.DataFrame]:
    """Split events/artifacts into train and validation partitions.

    The full split policy is postponed to PR4.
    """
    _ = frame
    _ = config
    raise NotImplementedError(
        "Temporal split implementation is not available yet. Planned for PR4."
    )
