"""Scoring interface for co-visitation pairs."""

import polars as pl

from ozon_similar_products.data.validation import validate_pair_aggregates


class CoVisitationScorer:
    """Score item-item pair aggregates."""

    def __init__(self, method: str = "pair_count") -> None:
        self.method = method

    def score(self, pair_aggregates: pl.DataFrame | pl.LazyFrame) -> pl.DataFrame:
        """Compute pair scores."""
        validate_pair_aggregates(pair_aggregates)
        raise NotImplementedError