"""Graph update strategies for full retrain and future incremental updates."""

from typing import Protocol

import polars as pl


class GraphUpdateStrategy(Protocol):
    """Interface for graph update strategies."""

    def update(
        self,
        train_until_date: str,
        lookback_days: int,
    ) -> pl.DataFrame:
        """Return pair aggregates for a training window."""


class FullRetrainStrategy:
    """Full graph rebuild over a rolling window."""

    def update(
        self,
        train_until_date: str,
        lookback_days: int,
    ) -> pl.DataFrame:
        """Read all daily pairs in window and aggregate from scratch."""
        raise NotImplementedError


class IncrementalUpdateStrategy:
    """Future strategy: add new day and remove expired day."""

    def update(
        self,
        train_until_date: str,
        lookback_days: int,
    ) -> pl.DataFrame:
        """Update graph incrementally."""
        raise NotImplementedError
