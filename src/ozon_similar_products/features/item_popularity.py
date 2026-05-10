"""Item popularity feature interface."""

import polars as pl

from ozon_similar_products.data.validation import validate_clean_events


class ItemPopularityBuilder:
    """Build item popularity features from clean events."""

    def transform_day(self, events_clean: pl.DataFrame | pl.LazyFrame) -> pl.DataFrame:
        """Compute item popularity for one daily partition."""
        validate_clean_events(events_clean)
        raise NotImplementedError

    def aggregate_window(
            self,
            daily_popularity: list[pl.DataFrame | pl.LazyFrame],
    ) -> pl.DataFrame:
        """Aggregate item popularity over a rolling window."""
        raise NotImplementedError
