"""Build timeout-based user sessions from clean events."""

from dataclasses import dataclass

import polars as pl

from ozon_similar_products.data import schemas
from ozon_similar_products.data.frames import empty_contract_frame
from ozon_similar_products.data.validation import validate_clean_events, validate_sessions

FrameLike = pl.DataFrame | pl.LazyFrame


@dataclass(frozen=True)
class SessionBuilder:
    """Build user sessions from clean item events.

    SessionBuilder has one responsibility: split a user's ordered event stream
    into short time-based contexts and assign compact session identity fields:
    ``session_index`` and ``session_start_date``. It does not score events, does
    not assign weights, and does not collapse repeated items. The downstream
    ItemPairBuilder decides how to turn events inside a session into item-level
    signals.
    """

    timeout_minutes: int = 30

    def __post_init__(self) -> None:
        if self.timeout_minutes <= 0:
            raise ValueError("timeout_minutes must be a positive integer")

    @classmethod
    def from_config(cls, config: dict) -> "SessionBuilder":
        """Create a builder from the project config dictionary."""
        pipeline_config = config.get("pipeline", {})
        return cls(
            timeout_minutes=int(pipeline_config.get("session_timeout_minutes", 30)),
        )

    def transform_day(self, events_clean: FrameLike) -> pl.DataFrame:
        """Build sessions for one clean-events partition."""
        validate_clean_events(events_clean)

        sessions = (
            _as_lazy(events_clean)
            .select(
                "user_id",
                "event_date",
                "timestamp",
                "action_type",
                "item_id",
            )
            .filter(pl.col("user_id").is_not_null())
            .filter(pl.col("timestamp").is_not_null())
            .filter(pl.col("item_id").is_not_null())
            .sort(["user_id", "timestamp", "item_id", "action_type"])
            .with_columns(
                pl.col("timestamp")
                .diff()
                .over("user_id")
                .dt.total_seconds()
                .alias("time_gap_seconds")
            )
            .with_columns(
                (
                        pl.col("time_gap_seconds").is_null()
                        | (pl.col("time_gap_seconds") > self.timeout_minutes * 60)
                )
                .cast(pl.Int64)
                .alias("is_new_session")
            )
            .with_columns(
                pl.col("is_new_session")
                .cum_sum()
                .over("user_id")
                .alias("session_index")
            )
            .with_columns(
                pl.col("event_date")
                .first()
                .over(["user_id", "session_index"])
                .alias("session_start_date")
            )
            .with_columns(
                pl.col("session_index").cast(pl.Int64),
                pl.col("session_start_date").cast(pl.Date, strict=False),
            )
            .select(schemas.SESSIONS_COLUMNS)
            .collect()
        )

        if sessions.is_empty():
            sessions = empty_contract_frame(schemas.SESSIONS_COLUMNS)

        validate_sessions(sessions)
        return sessions

    def transform_window(self, daily_clean_events: list[FrameLike]) -> pl.DataFrame:
        """Build sessions for multiple clean-events partitions.

        Daily partitions are concatenated before sessionization so events around
        midnight can stay in one session when they are within the timeout window.
        """
        if not daily_clean_events:
            return empty_contract_frame(schemas.SESSIONS_COLUMNS)

        all_events = pl.concat(
            [_as_lazy(events) for events in daily_clean_events],
            how="vertical",
        )
        return self.transform_day(all_events)


def _as_lazy(frame: FrameLike) -> pl.LazyFrame:
    """Return a LazyFrame for both eager and lazy Polars inputs."""
    if isinstance(frame, pl.LazyFrame):
        return frame
    return frame.lazy()
