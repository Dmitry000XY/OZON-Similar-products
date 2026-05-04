"""Event cleaning interface for MVP pipeline."""

import polars as pl

from ozon_similar_products.data.validation import validate_raw_events, validate_clean_events


class EventCleaner:
    """Clean raw user actions and prepare item-level events."""

    def __init__(
        self,
        item_action_types: list[str],
        action_weights: dict[str, float],
    ) -> None:
        self.item_action_types = item_action_types
        self.action_weights = action_weights

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
        result = pl.concat(cleaned_days) if cleaned_days else pl.DataFrame()
        validate_clean_events(result)
        return result