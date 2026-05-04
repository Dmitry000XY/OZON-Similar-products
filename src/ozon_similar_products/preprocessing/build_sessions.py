"""Session building interface for MVP pipeline."""

import polars as pl

from ozon_similar_products.data import schemas
from ozon_similar_products.data.validation import validate_clean_events, validate_sessions


class SessionBuilder:
    """Build user sessions from clean events."""

    def __init__(
        self,
        timeout_minutes: int = 30,
        max_items_per_session: int = 50,
    ) -> None:
        self.timeout_minutes = timeout_minutes
        self.max_items_per_session = max_items_per_session

    def transform_day(self, events_clean: pl.DataFrame | pl.LazyFrame) -> pl.DataFrame:
        """Build sessions for one daily partition."""
        validate_clean_events(events_clean)
        raise NotImplementedError

    def transform_window(
        self,
        daily_clean_events: list[pl.DataFrame | pl.LazyFrame],
    ) -> pl.DataFrame:
        """Build sessions for multiple daily partitions."""
        session_days = [self.transform_day(events) for events in daily_clean_events]
        if not session_days:
            return pl.DataFrame(schema={col: pl.Utf8 for col in schemas.SESSIONS_COLUMNS})
        result = pl.concat(session_days)
        validate_sessions(result)
        return result