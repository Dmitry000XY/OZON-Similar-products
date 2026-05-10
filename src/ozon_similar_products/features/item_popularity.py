"""Production item popularity builder for MVP pipeline."""

from collections.abc import Mapping, Sequence
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

    action_weights: Mapping[str, float]
    item_action_types: Sequence[str] = DEFAULT_ITEM_ACTION_TYPES

    def transform_day(self, events_clean: FrameLike) -> pl.DataFrame:
        """Compute item popularity for one clean-events partition.

        The input is expected to follow the clean events contract. Search rows,
        rows without item_id, and action types outside item_action_types are not
        counted as item popularity.
        """
        validate_clean_events(events_clean)
        self._validate_action_weights()

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

    def aggregate_window(
            self,
            daily_popularity: list[FrameLike],
    ) -> pl.DataFrame:
        """Aggregate daily item popularity tables over a rolling window.

        This method is intentionally not implemented yet because exact
        unique_users cannot be recovered from already aggregated daily tables.
        Use build() on clean events for the target window when exact unique_users
        are required.
        """
        raise NotImplementedError(
            "Exact rolling-window item popularity should be built from clean "
            "events, not from daily popularity tables, because unique_users "
            "cannot be summed across days without overcounting users."
        )

    def _validate_action_weights(self) -> None:
        """Validate that every configured item action has a weight."""
        missing_weights = set(self.item_action_types) - set(self.action_weights.keys())
        if missing_weights:
            raise ValueError(
                "Missing action weights for item popularity actions: "
                f"{sorted(missing_weights)}"
            )
