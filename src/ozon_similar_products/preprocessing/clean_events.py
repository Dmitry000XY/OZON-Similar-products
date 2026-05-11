"""Event cleaning interface for MVP pipeline."""

import polars as pl

from ozon_similar_products.data import schemas
from ozon_similar_products.data.validation import validate_clean_events, validate_raw_events


class EventCleaner:
    """Clean raw user actions and prepare item-level events."""

    def __init__(
            self,
            item_action_types: list[str],
    ) -> None:
        self.item_action_types = item_action_types

    def transform_day(self, events: pl.DataFrame | pl.LazyFrame) -> pl.DataFrame:
        """Clean one daily partition of raw events."""
        validate_raw_events(events)
        raise NotImplementedError

    def transform_window(
            self,
            daily_events: list[pl.DataFrame | pl.LazyFrame],
    ) -> pl.DataFrame:
        """Clean multiple daily partitions and concatenate result."""
        cleaned_days = [self.transform_day(events) for events in daily_events]
        if not cleaned_days:
            return pl.DataFrame(schema={col: pl.Utf8 for col in schemas.CLEAN_EVENTS_COLUMNS})
        result = pl.concat(cleaned_days)
        validate_clean_events(result)
        return result
