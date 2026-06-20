"""Scoring/top-K/fallback/output helpers over prebuilt train artifacts."""

from __future__ import annotations

import logging
from collections.abc import Mapping
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import polars as pl

from ozon_similar_products.business.fallback import (
    FALLBACK_SOURCE_LABELS,
    FallbackConfig,
    FallbackLayer,
)
from ozon_similar_products.config import PROJECT_ROOT
from ozon_similar_products.data import load_configs, load_products, schemas
from ozon_similar_products.output.writers import RecommendationWriter
from ozon_similar_products.pipeline.run_pipeline import (
    PipelineRunResult,
    _action_shares_from_distribution,
    _as_bool,
    _as_mapping,
    _as_non_empty_str,
    _as_optional_int,
    _as_path,
    _as_positive_int,
    _outputs_root,
    publish_latest_run,
)
from ozon_similar_products.retrieval.scoring import CoVisitationScorer
from ozon_similar_products.retrieval.topk import TopKSelector


def run_scoring_output_from_artifacts(
    *,
    config: Mapping[str, Any],
    pair_aggregates: pl.DataFrame | pl.LazyFrame,
    item_popularity: pl.DataFrame | pl.LazyFrame,
    action_distribution: pl.DataFrame,
    train_until_date: str,
    lookback_days: int,
    window_start: str,
    window_end: str,
    output_dir: str | Path,
    run_id: str,
    update_latest: bool = False,
    row_counts: Mapping[str, int] | None = None,
    allow_empty_latest_update: bool | None = None,
) -> PipelineRunResult:
    """Run scoring/top-K/fallback/output from already materialized train artifacts."""
    logger = logging.getLogger(__name__)
    data_config = load_configs(project_root=PROJECT_ROOT)
    pipeline_config = _as_mapping(config.get("pipeline", {}))
    outputs_config = _as_mapping(config.get("outputs", {}))
    topk_config = _as_mapping(config.get("topk", {}))
    counts = dict(row_counts or {})

    if allow_empty_latest_update is None:
        allow_empty_latest_update = _as_bool(
            pipeline_config.get("allow_empty_latest_update"),
            default=False,
            parameter_name="pipeline.allow_empty_latest_update",
        )

    logger.info("[scoring_output] score pairs")
    scorer = CoVisitationScorer.from_config(config)
    if scorer.action_shares is None:
        derived_action_shares = _action_shares_from_distribution(action_distribution)
        if derived_action_shares is not None:
            scorer = scorer.__class__(
                method=scorer.method,
                business_weights=scorer.business_weights,
                beta=scorer.beta,
                log1p_counts=scorer.log1p_counts,
                normalize_by_item_popularity=scorer.normalize_by_item_popularity,
                action_shares=derived_action_shares,
            )

    if scorer.normalize_by_item_popularity:
        pair_scores = scorer.score_lazy(pair_aggregates, item_popularity=item_popularity)
    else:
        pair_scores = scorer.score_lazy(pair_aggregates)

    pair_scores_rows = pair_scores.select(pl.len()).collect().item()
    logger.info(
        "[scoring_output] pair scores rows=%s calibration_used=%s",
        pair_scores_rows,
        scorer.action_shares is not None,
    )

    top_k = _as_positive_int(
        value=topk_config.get("top_k", pipeline_config.get("top_k")),
        default=20,
        parameter_name="topk.top_k",
    )
    selector = TopKSelector(
        top_k=top_k,
        source=_as_non_empty_str(
            value=topk_config.get("source"),
            default="behavioral",
            parameter_name="topk.source",
        ),
        min_pair_count=_as_optional_int(topk_config.get("min_pair_count")),
        min_unique_users=_as_optional_int(topk_config.get("min_unique_users")),
        min_unique_sessions=_as_optional_int(topk_config.get("min_unique_sessions")),
    )
    recommendations = selector.select(pair_scores)

    fallback_config = FallbackConfig.from_config(config, top_k=top_k)
    if fallback_config.enabled:
        logger.info(
            "[scoring_output] apply fallback top_k=%s include_cold_start_items=%s",
            fallback_config.top_k,
            fallback_config.include_cold_start_items,
        )
        product_information = load_products(
            data_config,
            columns=schemas.PRODUCT_INFORMATION_COLUMNS,
        )
        recommendations = FallbackLayer(config=fallback_config).apply(
            recommendations,
            item_popularity=item_popularity,
            product_information=product_information,
        )

    recommendations_rows = recommendations.height
    fallback_rows = recommendations.filter(pl.col("source").is_in(FALLBACK_SOURCE_LABELS)).height
    if recommendations_rows == 0:
        logger.warning("[scoring_output] recommendations empty")

    outputs_root = _outputs_root(outputs_config)
    latest_dir = _as_path(outputs_config.get("latest_dir"), "outputs/latest")
    run_dir = Path(output_dir).resolve() if output_dir is not None else outputs_root / "runs" / run_id
    writer = RecommendationWriter()

    recommendations_dir = run_dir / "recommendations"
    detailed_path = writer.save_detailed(recommendations, recommendations_dir / "detailed.parquet")
    products = load_products(data_config, columns=["item_id", "name"])
    enriched_path = writer.save_enriched(
        recommendations,
        products,
        recommendations_dir / "enriched.parquet",
    )
    widget_path = writer.save_widget_format(recommendations, recommendations_dir / "lookup.parquet")

    detailed_relative_path = detailed_path.relative_to(run_dir).as_posix()
    enriched_relative_path = enriched_path.relative_to(run_dir).as_posix()
    widget_relative_path = widget_path.relative_to(run_dir).as_posix()
    manifest = {
        "run_id": run_id,
        "generated_at": datetime.now(UTC),
        "train_until_date": train_until_date,
        "lookback_days": lookback_days,
        "window_start": window_start,
        "window_end": window_end,
        "score_method": scorer.method,
        "top_k": top_k,
        "calibration_used": scorer.action_shares is not None,
        "fallback_enabled": fallback_config.enabled,
        "fallback_source_label": fallback_config.source_label,
        "detailed_recommendations_path": detailed_relative_path,
        "enriched_recommendations_path": enriched_relative_path,
        "widget_recommendations_path": widget_relative_path,
        "lookup_recommendations_path": widget_relative_path,
        "paths": {
            "detailed_recommendations_path": detailed_relative_path,
            "enriched_recommendations_path": enriched_relative_path,
            "widget_recommendations_path": widget_relative_path,
            "lookup_recommendations_path": widget_relative_path,
        },
        "rows": {
            "raw_events": counts.get("raw_events", 0),
            "clean_events": counts.get("clean_events", 0),
            "sessions": counts.get("sessions", 0),
            "daily_pairs": counts.get("daily_pairs", 0),
            "pair_aggregates": counts.get("pair_aggregates", 0),
            "pair_scores": pair_scores_rows,
            "recommendations": recommendations_rows,
            "fallback_recommendations": fallback_rows,
        },
    }
    run_manifest_path = writer.save_manifest(manifest, run_dir)
    result = PipelineRunResult(
        run_id=run_id,
        run_dir=run_dir,
        manifest_path=run_manifest_path,
        detailed_recommendations_path=detailed_path,
        enriched_recommendations_path=enriched_path,
        lookup_recommendations_path=widget_path,
        manifest=manifest,
    )
    if update_latest and (recommendations_rows > 0 or allow_empty_latest_update):
        publish_latest_run(result, latest_dir)
    elif recommendations_rows == 0:
        logger.warning(
            "[scoring_output] latest manifest not updated "
            "(empty recommendations, allow_empty_latest_update=%s)",
            allow_empty_latest_update,
        )
    return result
