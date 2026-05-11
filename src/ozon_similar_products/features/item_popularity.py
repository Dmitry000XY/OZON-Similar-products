"""Production item popularity builder for MVP pipeline."""

from collections.abc import Sequence
from dataclasses import dataclass

import polars as pl

from ozon_similar_products.data.validation import (
    validate_clean_events,
    validate_item_popularity,
)

FrameLike = pl.DataFrame | pl.LazyFrame

DEFAULT_ITEM_ACTION_TYPES = ("view", "click", "favorite", "to_cart")


def _as_lazy(frame: FrameLike) -> pl.LazyFrame:
    """Return a LazyFrame for both eager and lazy Polars inputs."""
    if isinstance(frame, pl.LazyFrame):
        return frame
    return frame.lazy()


@dataclass(frozen=True)
class ItemPopularityBuilder:
    """Build item popularity features from clean events."""

    item_action_types: Sequence[str] = DEFAULT_ITEM_ACTION_TYPES

    def transform_day(self, events_clean: FrameLike) -> pl.DataFrame:
        """Compute item popularity for one clean-events partition.

        The input is expected to follow the clean events contract. Search rows,
        rows without item_id, and action types outside item_action_types are not
        counted as item popularity.
        """
        validate_clean_events(events_clean)

        item_popularity = (
            _as_lazy(events_clean)
            .filter(pl.col("item_id").is_not_null())
            .filter(pl.col("action_type").is_in(self.item_action_types))
            .group_by("item_id")
            .agg(
                pl.len().alias("events_count"),
                pl.col("user_id").n_unique().alias("unique_users"),
                (pl.col("action_type") == "view").sum().alias("views_count"),
                (pl.col("action_type") == "click").sum().alias("clicks_count"),
                (pl.col("action_type") == "favorite").sum().alias("favorites_count"),
                (pl.col("action_type") == "to_cart").sum().alias("to_cart_count"),
                pl.col("action_weight").sum().alias("weighted_events"),
            )
            .sort(
                ["weighted_events", "events_count", "item_id"],
                descending=[True, True, False],
            )
            .collect()
        )

        validate_item_popularity(item_popularity)
        return item_popularity

    def build(self, events_clean: FrameLike) -> pl.DataFrame:
        """Build item popularity from clean events.

        Alias for transform_day() for callers that do not care whether the input
        is a single day or a prepared clean-events window.
        """
        return self.transform_day(events_clean)

    def build_by_date(self, events_clean: FrameLike) -> pl.DataFrame:
        """Build item popularity diagnostics by event date."""
        validate_clean_events(events_clean)

        return self._build_grouped_popularity(
            events_clean=events_clean,
            group_columns=["event_date", "item_id"],
            sort_columns=["event_date", "weighted_events", "events_count", "item_id"],
            descending=[False, True, True, False],
            include_action_counts=True,
        )

    def build_by_action_type(self, events_clean: FrameLike) -> pl.DataFrame:
        """Build item popularity diagnostics by action type."""
        validate_clean_events(events_clean)

        return self._build_grouped_popularity(
            events_clean=events_clean,
            group_columns=["item_id", "action_type"],
            sort_columns=["item_id", "weighted_events", "events_count", "action_type"],
            descending=[False, True, True, False],
            include_action_counts=False,
        )

    def build_by_widget_name(self, events_clean: FrameLike) -> pl.DataFrame:
        """Build item popularity diagnostics by widget name."""
        validate_clean_events(events_clean)

        return self._build_grouped_popularity(
            events_clean=events_clean,
            group_columns=["item_id", "widget_name"],
            sort_columns=["item_id", "weighted_events", "events_count", "widget_name"],
            descending=[False, True, True, False],
            include_action_counts=True,
        )

    def aggregate_window(
            self,
            daily_popularity: list[FrameLike],
    ) -> pl.DataFrame:
        """Aggregate daily item popularity tables over a rolling window.

        This method is intentionally not implemented because exact unique_users
        cannot be recovered from already aggregated daily tables. To compute item
        popularity for a rolling window, pass clean events for the whole window to
        build(events_clean_window).
        """
        raise NotImplementedError(
            "Exact rolling-window item popularity should be built from clean "
            "events with build(events_clean_window), not from already aggregated "
            "daily popularity tables, because unique_users cannot be summed across "
            "days without overcounting users."
        )

    def _build_grouped_popularity(
            self,
            events_clean: FrameLike,
            group_columns: Sequence[str],
            sort_columns: Sequence[str],
            descending: Sequence[bool],
            include_action_counts: bool,
    ) -> pl.DataFrame:
        """Build grouped item popularity diagnostics."""
        aggregations: list[pl.Expr] = [
            pl.len().alias("events_count"),
            pl.col("user_id").n_unique().alias("unique_users"),
            pl.col("action_weight").sum().alias("weighted_events"),
        ]

        if include_action_counts:
            aggregations.extend(
                [
                    (pl.col("action_type") == "view").sum().alias("views_count"),
                    (pl.col("action_type") == "click").sum().alias("clicks_count"),
                    (pl.col("action_type") == "favorite").sum().alias("favorites_count"),
                    (pl.col("action_type") == "to_cart").sum().alias("to_cart_count"),
                ]
            )

        return (
            _as_lazy(events_clean)
            .filter(pl.col("item_id").is_not_null())
            .filter(pl.col("action_type").is_in(self.item_action_types))
            .group_by(list(group_columns))
            .agg(aggregations)
            .sort(list(sort_columns), descending=list(descending))
            .collect()
        )
