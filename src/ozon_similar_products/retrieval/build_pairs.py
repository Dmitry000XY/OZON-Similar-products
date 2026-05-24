"""Build directed multichannel item-item pairs from user sessions."""

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any

import polars as pl

from ozon_similar_products.data import schemas
from ozon_similar_products.data.frames import empty_contract_frame
from ozon_similar_products.data.validation import (
    validate_daily_item_pairs,
    validate_sessions,
)

FrameLike = pl.DataFrame | pl.LazyFrame


def _as_lazy(frame: FrameLike) -> pl.LazyFrame:
    """Return a LazyFrame for both eager and lazy Polars inputs."""
    if isinstance(frame, pl.LazyFrame):
        return frame
    return frame.lazy()


def _empty_daily_pairs() -> pl.DataFrame:
    """Return an empty daily-pairs DataFrame with the public contract columns."""
    return empty_contract_frame(schemas.DAILY_ITEM_PAIRS_COLUMNS)


def _build_signal_priority(action_types: Sequence[str]) -> dict[str, int]:
    """Build strongest-signal priorities from action order.

    The first action type is the weakest signal, the last one is the strongest.
    In production this order should come from config, for example:
    ``view -> click -> favorite -> to_cart``.
    """
    return {
        action_type: priority
        for priority, action_type in enumerate(action_types, start=1)
    }


def _priority_expr(priority: Mapping[str, int]) -> pl.Expr:
    """Build a Polars expression that maps action_type to priority."""
    expr = pl.lit(0)
    for action_type, action_priority in priority.items():
        expr = pl.when(pl.col("action_type") == action_type).then(action_priority).otherwise(expr)
    return expr.cast(pl.Int64).alias("signal_priority")


@dataclass(frozen=True)
class ItemPairBuilder:
    """Build directed item-item pairs from sessions.

    The builder does not apply weights. It keeps action channels instead of
    compressing them into a single score too early. Each item inside a session
    is collapsed to one strongest item-level signal, then directed pairs are
    created. For pair A -> B, downstream channel is the target signal of B.

    Signal strength order is configurable through ``item_action_types`` or
    ``signal_priority``. By default, we use the public action order from
    ``schemas.ITEM_SIGNAL_TYPES``. Production pipeline code should pass values
    from ``configs/baseline.yaml`` instead of hardcoding priorities here.
    """

    max_items_per_session: int = 50
    item_action_types: Sequence[str] = field(
        default_factory=lambda: tuple(schemas.ITEM_SIGNAL_TYPES)
    )
    signal_priority: Mapping[str, int] | None = None

    def __post_init__(self) -> None:
        if self.max_items_per_session < 2:
            raise ValueError("max_items_per_session must be at least 2")
        if not self.item_action_types:
            raise ValueError("item_action_types must not be empty")

    @classmethod
    def from_config(cls, config: Mapping[str, Any]) -> "ItemPairBuilder":
        """Create builder from project config.

        Expected config shape:
        ```yaml
        pipeline:
          max_items_per_session: 50
        events:
          item_action_types: [view, click, favorite, to_cart]
        item_pair_builder:
          signal_priority:
            view: 1
            click: 2
            favorite: 3
            to_cart: 4
        ```
        """
        pipeline_config = config.get("pipeline", {})
        events_config = config.get("events", {})
        pair_builder_config = config.get("item_pair_builder", {})

        raw_action_types = events_config.get("item_action_types", schemas.ITEM_SIGNAL_TYPES)
        if isinstance(raw_action_types, str):
            item_action_types = (raw_action_types,)
        elif isinstance(raw_action_types, Sequence):
            item_action_types = tuple(raw_action_types)
        else:
            raise TypeError("events.item_action_types must be a string or a sequence of strings")

        if not item_action_types or any(
                not isinstance(action_type, str) or not action_type.strip()
                for action_type in item_action_types
        ):
            raise ValueError(
                "events.item_action_types must contain non-empty string values"
            )
        signal_priority = pair_builder_config.get("signal_priority")
        if signal_priority is not None:
            signal_priority = {
                str(action_type): int(priority)
                for action_type, priority in signal_priority.items()
            }

        return cls(
            max_items_per_session=int(pipeline_config.get("max_items_per_session", 50)),
            item_action_types=item_action_types,
            signal_priority=signal_priority,
        )

    def transform_day(self, sessions: FrameLike) -> pl.DataFrame:
        """Build directed item-item pairs for one sessions partition."""
        validate_sessions(sessions)

        session_items = self._build_session_items(sessions)
        valid_sessions = self._build_valid_sessions(session_items)

        valid_session_items = session_items.join(
            valid_sessions,
            on=["user_id", "session_index"],
            how="inner",
        )

        pairs = (
            valid_session_items
            .join(
                valid_session_items,
                on=["user_id", "session_index"],
                how="inner",
                suffix="_similar",
            )
            .filter(pl.col("item_id") != pl.col("item_id_similar"))
            .select(
                pl.col("session_start_date").cast(pl.Date, strict=False).alias("pair_date"),
                pl.col("item_id"),
                pl.col("item_id_similar").alias("similar_item_id"),
                pl.col("user_id"),
                pl.col("session_index"),
                pl.col("item_action_type").alias("source_action_type"),
                pl.col("item_action_type_similar").alias("target_action_type"),
                pl.col("item_action_type_similar").alias("signal_type"),
            )
            .sort(["pair_date", "item_id", "similar_item_id", "user_id", "session_index"])
            .collect()
        )

        if pairs.is_empty():
            pairs = _empty_daily_pairs()

        validate_daily_item_pairs(pairs)
        return pairs

    def _build_session_items(self, sessions: FrameLike) -> pl.LazyFrame:
        """Collapse repeated item actions inside a session to one strongest signal."""
        priority = self._resolved_signal_priority()
        return (
            _as_lazy(sessions)
            .select(
                "user_id",
                "session_index",
                "session_start_date",
                "event_date",
                "item_id",
                "action_type",
            )
            .filter(pl.col("item_id").is_not_null())
            .filter(pl.col("action_type").is_in(list(self.item_action_types)))
            .with_columns(_priority_expr(priority))
            .group_by(["user_id", "session_index", "session_start_date", "item_id"])
            .agg(
                pl.col("action_type")
                .sort_by(pl.col("signal_priority"), descending=True)
                .first()
                .alias("item_action_type"),
                pl.col("signal_priority").max().alias("item_signal_priority"),
            )
        )

    def _build_valid_sessions(self, session_items: pl.LazyFrame) -> pl.LazyFrame:
        """Keep sessions that can create pairs and are not too long/noisy."""
        return (
            session_items.group_by(["user_id", "session_index"])
            .agg(pl.len().alias("items_count"))
            .filter(pl.col("items_count").is_between(2, self.max_items_per_session))
            .select("user_id", "session_index")
        )

    def _resolved_signal_priority(self) -> Mapping[str, int]:
        """Return explicit priority map or derive it from configured action order."""
        if self.signal_priority is not None:
            return self.signal_priority
        return _build_signal_priority(self.item_action_types)
