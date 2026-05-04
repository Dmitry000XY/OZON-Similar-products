"""Top-K selection interface."""

import polars as pl

from ozon_similar_products.data.validation import validate_pair_scores


class TopKSelector:
    """Select top-K similar items for each item."""

    def __init__(self, top_k: int = 20) -> None:
        self.top_k = top_k

    def select(self, pair_scores: pl.DataFrame | pl.LazyFrame) -> pl.DataFrame:
        """Select top-K recommendations per item_id."""
        validate_pair_scores(pair_scores)
        raise NotImplementedError
