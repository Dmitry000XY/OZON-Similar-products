"""Item pair building interface for co-visitation retrieval."""

import polars as pl

from ozon_similar_products.data.validation import validate_sessions


class ItemPairBuilder:
    """Build directed item-item pairs from sessions."""

    def __init__(self, max_items_per_session: int = 50) -> None:
        self.max_items_per_session = max_items_per_session

    def transform_day(self, sessions: pl.DataFrame | pl.LazyFrame) -> pl.DataFrame:
        """Build directed item-item pairs for one daily partition."""
        validate_sessions(sessions)
        raise NotImplementedError