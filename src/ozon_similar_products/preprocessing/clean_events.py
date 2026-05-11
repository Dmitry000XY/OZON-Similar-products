"""Event cleaning interface for MVP pipeline."""

import polars as pl

from ozon_similar_products.data import schemas
from ozon_similar_products.data.validation import validate_clean_events, validate_raw_events

FrameLike = pl.DataFrame | pl.LazyFrame


def _as_lazy(frame: FrameLike) -> pl.LazyFrame:
    """Return a LazyFrame for both eager and lazy Polars inputs."""
    if isinstance(frame, pl.LazyFrame):
        return frame
    return frame.lazy()


def _collect_schema(frame: FrameLike) -> pl.Schema:
    """Return a schema for both eager and lazy Polars inputs."""
    if isinstance(frame, pl.LazyFrame):
        return frame.collect_schema()
    return frame.schema


def _timestamp_expr(frame: FrameLike) -> pl.Expr:
    """Build a timestamp normalization expression based on input dtype."""
    timestamp_dtype = _collect_schema(frame)["timestamp"]
    timestamp = pl.col("timestamp")
    if timestamp_dtype == pl.String:
        return timestamp.str.to_datetime(strict=False).alias("timestamp")
    return timestamp.cast(pl.Datetime, strict=False).alias("timestamp")


class EventCleaner:
    """Clean raw user actions and prepare item-level events."""

    def __init__(
            self,
            item_action_types: list[str],
    ) -> None:
        self.item_action_types = item_action_types

    def transform_day(self, events: FrameLike) -> pl.DataFrame:
        """Clean one daily partition of raw events."""
        validate_raw_events(events)

        result = (
            _as_lazy(events)
            .unique(subset=schemas.RAW_EVENTS_COLUMNS, maintain_order=True)
            .with_columns(_timestamp_expr(events))
            .filter(
                pl.col("user_id").is_not_null()
                & pl.col("timestamp").is_not_null()
                & pl.col("action_type").is_not_null()
            )
            .filter(pl.col("action_type").is_in(self.item_action_types))
            .filter(pl.col("item_id").is_not_null())
            .with_columns(
                pl.col("timestamp").dt.date().alias("event_date"),
                pl.col("widget_name").cast(pl.String).fill_null("unknown").alias("widget_name"),
            )
            .select(schemas.CLEAN_EVENTS_COLUMNS)
            .collect()
        )

        validate_clean_events(result)
        return result

    def transform_window(
            self,
            daily_events: list[FrameLike],
    ) -> pl.DataFrame:
        """Clean multiple daily partitions and concatenate result."""
        cleaned_days = [self.transform_day(events) for events in daily_events]
        if not cleaned_days:
            return pl.DataFrame(schema={col: pl.Utf8 for col in schemas.CLEAN_EVENTS_COLUMNS})
        result = pl.concat(cleaned_days)
        validate_clean_events(result)
        return result
