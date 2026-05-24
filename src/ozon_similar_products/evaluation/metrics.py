"""Offline metrics skeleton for recommendation evaluation."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import polars as pl

FrameLike = pl.DataFrame | pl.LazyFrame


@dataclass(frozen=True)
class OfflineMetrics:
    """Container for key offline metrics."""

    hit_rate_at_k: float | None = None
    weighted_recall_at_k: float | None = None
    ndcg_at_k: float | None = None
    mrr_at_k: float | None = None
    coverage_at_k: float | None = None
    popularity_bias_at_k: float | None = None
    fallback_share_at_k: float | None = None
    metadata_gap_share_at_k: float | None = None


def compute_offline_metrics(
    recommendations: FrameLike,
    ground_truth: FrameLike,
    *,
    top_k: int,
    context: dict[str, Any] | None = None,
) -> OfflineMetrics:
    """Compute offline metrics for one evaluation slice.

    Metric implementation is postponed to PR4.
    """
    _ = recommendations
    _ = ground_truth
    _ = top_k
    _ = context
    raise NotImplementedError(
        "Offline metrics implementation is not available yet. Planned for PR4."
    )
