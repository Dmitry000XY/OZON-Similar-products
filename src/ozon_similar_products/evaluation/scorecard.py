"""Experiment scorecard skeleton."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from ozon_similar_products.evaluation.metrics import OfflineMetrics


@dataclass(frozen=True)
class EvaluationScorecard:
    """Serializable experiment scorecard."""

    experiment_id: str
    train_until_date: str
    lookback_days: int
    top_k: int
    metrics: OfflineMetrics
    notes: str | None = None
    metadata: dict[str, Any] | None = None


def build_scorecard(
    *,
    experiment_id: str,
    train_until_date: str,
    lookback_days: int,
    top_k: int,
    metrics: OfflineMetrics,
    notes: str | None = None,
    metadata: dict[str, Any] | None = None,
) -> EvaluationScorecard:
    """Build an immutable scorecard object for reporting."""
    if not experiment_id:
        raise ValueError("experiment_id must be a non-empty string")
    if not train_until_date:
        raise ValueError("train_until_date must be a non-empty string")
    if lookback_days <= 0:
        raise ValueError("lookback_days must be a positive integer")
    if top_k <= 0:
        raise ValueError("top_k must be a positive integer")

    return EvaluationScorecard(
        experiment_id=experiment_id,
        train_until_date=train_until_date,
        lookback_days=lookback_days,
        top_k=top_k,
        metrics=metrics,
        notes=notes,
        metadata=metadata,
    )
