"""Pair aggregation interface."""

import polars as pl

from ozon_similar_products.data.validation import validate_pair_aggregates


class PairAggregator:
    """Aggregate daily item pairs over a rolling window."""

    def aggregate_window(
        self,
        daily_pairs: list[pl.DataFrame | pl.LazyFrame],
        window_start: str,
        window_end: str,
    ) -> pl.DataFrame:
        """Aggregate daily pairs into pair aggregates."""
        raise NotImplementedError