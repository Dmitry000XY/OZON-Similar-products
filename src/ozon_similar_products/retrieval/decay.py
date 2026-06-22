"""Config parsing and Polars expressions for graph decay weights."""

from __future__ import annotations

import math
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any

import polars as pl

_VALID_DISTANCE_STRATEGIES = {"none", "weight_table", "exponential"}
_VALID_TIME_STRATEGIES = {"none", "weight_table", "exponential"}
_VALID_WIDGET_CONTEXT_USES = {"source", "target"}

DEFAULT_DISTANCE_WEIGHTS: dict[int | str, float] = {
    1: 1.0,
    2: 0.8,
    3: 0.6,
    5: 0.3,
    "default": 0.1,
}
DEFAULT_TIME_WEIGHTS: dict[int | str, float] = {
    0: 1.0,
    1: 0.9,
    3: 0.75,
    7: 0.5,
    14: 0.3,
    "default": 0.2,
}


def _as_mapping(value: Any) -> Mapping[str, Any]:
    if isinstance(value, Mapping):
        return value
    return {}


def _as_bool(value: Any, *, default: bool, name: str) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    raise TypeError(f"{name} must be a boolean")


def _as_optional_int(value: Any, *, name: str) -> int | None:
    if value is None:
        return None
    if isinstance(value, bool):
        raise TypeError(f"{name} must be an integer or null")
    if isinstance(value, int):
        parsed = value
    elif isinstance(value, str):
        parsed = int(value)
    else:
        raise TypeError(f"{name} must be an integer or null")
    if parsed < 0:
        raise ValueError(f"{name} must be non-negative or null")
    return parsed


def _as_float(value: Any, *, default: float, name: str) -> float:
    if value is None:
        return default
    if isinstance(value, bool):
        raise TypeError(f"{name} must be a number")
    if isinstance(value, int | float | str):
        return float(value)
    raise TypeError(f"{name} must be a number")


def _as_str(value: Any, *, default: str, name: str) -> str:
    if value is None:
        return default
    if isinstance(value, str):
        return value
    raise TypeError(f"{name} must be a string")


def _parse_widget_weights(raw_weights: Any) -> dict[str, float]:
    if raw_weights is None:
        return {}
    if not isinstance(raw_weights, Mapping):
        raise TypeError("graph.widget_context.weights must be a mapping")

    weights: dict[str, float] = {}
    for raw_widget_name, raw_weight in raw_weights.items():
        widget_name = str(raw_widget_name)
        if not widget_name:
            raise ValueError("graph.widget_context.weights keys must be non-empty strings")
        weight = float(raw_weight)
        if weight < 0.0:
            raise ValueError("graph.widget_context.weights values must be non-negative")
        weights[widget_name] = weight
    return weights


def _normalized_widget_expr(widget_expr: pl.Expr) -> pl.Expr:
    widget = widget_expr.cast(pl.String).str.strip_chars()
    return (
        pl.when(widget.is_null() | (widget == ""))
        .then(pl.lit("unknown"))
        .otherwise(widget)
    )


def _parse_weight_table(
    raw_table: Any,
    *,
    default_table: Mapping[int | str, float],
    name: str,
) -> tuple[dict[int, float], float]:
    raw_mapping = raw_table if isinstance(raw_table, Mapping) else default_table
    weights: dict[int, float] = {}
    default_weight = 1.0

    for raw_key, raw_weight in raw_mapping.items():
        weight = float(raw_weight)
        if weight < 0.0 or weight > 1.0:
            raise ValueError(f"{name} weights must be between 0 and 1")

        if str(raw_key) == "default":
            default_weight = weight
            continue

        key = int(raw_key)
        if key < 0:
            raise ValueError(f"{name} bucket keys must be non-negative")
        weights[key] = weight

    if not weights:
        raise ValueError(f"{name} must define at least one numeric bucket")

    return weights, default_weight


def _bucket_floor_weight_expr(
    value_expr: pl.Expr,
    weights: Mapping[int, float],
    default_weight: float,
) -> pl.Expr:
    """Return a bucket/floor lookup expression.

    Values use the nearest lower configured bucket. Values above the largest
    bucket use the explicit default bucket to avoid long-tail links staying too
    strong forever.
    """
    max_bucket = max(weights)
    expr = pl.lit(default_weight)
    for bucket in sorted(weights):
        expr = (
            pl.when((value_expr >= bucket) & (value_expr <= max_bucket))
            .then(float(weights[bucket]))
            .otherwise(expr)
        )
    return expr


@dataclass(frozen=True)
class DistanceDecayConfig:
    """Configurable distance decay for item pairs inside one session."""

    enabled: bool = False
    strategy: str = "none"
    max_distance: int | None = None
    weight_by_distance: Mapping[int, float] = field(
        default_factory=lambda: {
            key: value
            for key, value in DEFAULT_DISTANCE_WEIGHTS.items()
            if isinstance(key, int)
        }
    )
    default_weight: float = float(DEFAULT_DISTANCE_WEIGHTS["default"])
    alpha: float = 0.5
    min_weight: float = 0.05

    def __post_init__(self) -> None:
        if self.strategy not in _VALID_DISTANCE_STRATEGIES:
            raise ValueError("graph.distance_decay.strategy must be none, weight_table, or exponential")
        if not self.enabled and self.strategy != "none":
            return
        if self.strategy == "exponential":
            if self.alpha < 0.0:
                raise ValueError("graph.distance_decay.exponential.alpha must be >= 0")
            if self.min_weight < 0.0 or self.min_weight > 1.0:
                raise ValueError("graph.distance_decay.exponential.min_weight must be between 0 and 1")
        if self.max_distance is not None and self.max_distance < 1:
            raise ValueError("graph.distance_decay.max_distance must be >= 1 or null")

    @classmethod
    def from_config(cls, config: Mapping[str, Any]) -> "DistanceDecayConfig":
        graph = _as_mapping(config.get("graph", {}))
        raw = _as_mapping(graph.get("distance_decay", {}))
        exponential = _as_mapping(raw.get("exponential", {}))
        weights, default_weight = _parse_weight_table(
            raw.get("weight_by_distance"),
            default_table=DEFAULT_DISTANCE_WEIGHTS,
            name="graph.distance_decay.weight_by_distance",
        )
        return cls(
            enabled=_as_bool(
                raw.get("enabled"),
                default=False,
                name="graph.distance_decay.enabled",
            ),
            strategy=str(raw.get("strategy", "none")),
            max_distance=_as_optional_int(
                raw.get("max_distance"),
                name="graph.distance_decay.max_distance",
            ),
            weight_by_distance=weights,
            default_weight=default_weight,
            alpha=_as_float(
                exponential.get("alpha"),
                default=0.5,
                name="graph.distance_decay.exponential.alpha",
            ),
            min_weight=_as_float(
                exponential.get("min_weight"),
                default=0.05,
                name="graph.distance_decay.exponential.min_weight",
            ),
        )

    def weight_expr(self, distance_expr: pl.Expr) -> pl.Expr:
        if not self.enabled or self.strategy == "none":
            return pl.lit(1.0)
        if self.strategy == "weight_table":
            return _bucket_floor_weight_expr(
                distance_expr,
                self.weight_by_distance,
                self.default_weight,
            )
        return pl.max_horizontal(
            (-(float(self.alpha)) * (distance_expr.cast(pl.Float64) - 1.0)).exp(),
            pl.lit(float(self.min_weight)),
        )


@dataclass(frozen=True)
class WidgetContextConfig:
    """Configurable widget-name graph weighting."""

    enabled: bool = False
    use: str = "target"
    default_weight: float = 1.0
    unknown_weight: float = 1.0
    weights: Mapping[str, float] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if self.use not in _VALID_WIDGET_CONTEXT_USES:
            raise ValueError("graph.widget_context.use must be source or target")
        if self.default_weight < 0.0:
            raise ValueError("graph.widget_context.default_weight must be non-negative")
        if self.unknown_weight < 0.0:
            raise ValueError("graph.widget_context.unknown_weight must be non-negative")
        for widget_name, weight in self.weights.items():
            if not isinstance(widget_name, str) or not widget_name:
                raise ValueError("graph.widget_context.weights keys must be non-empty strings")
            if float(weight) < 0.0:
                raise ValueError("graph.widget_context.weights values must be non-negative")

    @classmethod
    def from_config(cls, config: Mapping[str, Any]) -> "WidgetContextConfig":
        graph = _as_mapping(config.get("graph", {}))
        raw = _as_mapping(graph.get("widget_context", {}))
        if "blocked_widgets" in raw:
            raise ValueError(
                "graph.widget_context.blocked_widgets is not supported; "
                "use widget weights instead"
            )
        return cls(
            enabled=_as_bool(
                raw.get("enabled"),
                default=False,
                name="graph.widget_context.enabled",
            ),
            use=_as_str(
                raw.get("use"),
                default="target",
                name="graph.widget_context.use",
            ),
            default_weight=_as_float(
                raw.get("default_weight"),
                default=1.0,
                name="graph.widget_context.default_weight",
            ),
            unknown_weight=_as_float(
                raw.get("unknown_weight"),
                default=1.0,
                name="graph.widget_context.unknown_weight",
            ),
            weights=_parse_widget_weights(raw.get("weights")),
        )

    @property
    def context_column_name(self) -> str:
        if self.use == "source":
            return "source_widget_name"
        return "target_widget_name"

    def weight_expr(self, widget_expr: pl.Expr) -> pl.Expr:
        if not self.enabled:
            return pl.lit(1.0)

        widget = _normalized_widget_expr(widget_expr)
        expr = (
            pl.when(widget == "unknown")
            .then(float(self.unknown_weight))
            .otherwise(float(self.default_weight))
        )
        for widget_name, weight in self.weights.items():
            expr = pl.when(widget == widget_name).then(float(weight)).otherwise(expr)
        return expr


@dataclass(frozen=True)
class TimeDecayConfig:
    """Configurable time decay for daily pair stats inside a rolling window."""

    enabled: bool = False
    strategy: str = "none"
    weight_by_age_days: Mapping[int, float] = field(
        default_factory=lambda: {
            key: value
            for key, value in DEFAULT_TIME_WEIGHTS.items()
            if isinstance(key, int)
        }
    )
    default_weight: float = float(DEFAULT_TIME_WEIGHTS["default"])
    half_life_days: float = 7.0
    min_weight: float = 0.05

    def __post_init__(self) -> None:
        if self.strategy not in _VALID_TIME_STRATEGIES:
            raise ValueError("graph.time_decay.strategy must be none, weight_table, or exponential")
        if not self.enabled and self.strategy != "none":
            return
        if self.strategy == "exponential":
            if self.half_life_days <= 0.0:
                raise ValueError("graph.time_decay.exponential.half_life_days must be > 0")
            if self.min_weight < 0.0 or self.min_weight > 1.0:
                raise ValueError("graph.time_decay.exponential.min_weight must be between 0 and 1")

    @classmethod
    def from_config(cls, config: Mapping[str, Any]) -> "TimeDecayConfig":
        graph = _as_mapping(config.get("graph", {}))
        raw = _as_mapping(graph.get("time_decay", {}))
        exponential = _as_mapping(raw.get("exponential", {}))
        weights, default_weight = _parse_weight_table(
            raw.get("weight_by_age_days"),
            default_table=DEFAULT_TIME_WEIGHTS,
            name="graph.time_decay.weight_by_age_days",
        )
        return cls(
            enabled=_as_bool(
                raw.get("enabled"),
                default=False,
                name="graph.time_decay.enabled",
            ),
            strategy=str(raw.get("strategy", "none")),
            weight_by_age_days=weights,
            default_weight=default_weight,
            half_life_days=_as_float(
                exponential.get("half_life_days"),
                default=7.0,
                name="graph.time_decay.exponential.half_life_days",
            ),
            min_weight=_as_float(
                exponential.get("min_weight"),
                default=0.05,
                name="graph.time_decay.exponential.min_weight",
            ),
        )

    def weight_expr(self, age_days_expr: pl.Expr) -> pl.Expr:
        if not self.enabled or self.strategy == "none":
            return pl.lit(1.0)
        if self.strategy == "weight_table":
            return _bucket_floor_weight_expr(
                age_days_expr,
                self.weight_by_age_days,
                self.default_weight,
            )
        return pl.max_horizontal(
            (-(math.log(2.0)) * age_days_expr.cast(pl.Float64) / self.half_life_days).exp(),
            pl.lit(float(self.min_weight)),
        )
