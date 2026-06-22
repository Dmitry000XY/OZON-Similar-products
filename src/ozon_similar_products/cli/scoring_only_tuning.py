"""Fast scoring-only tuning helpers."""

from __future__ import annotations

import logging
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any

import polars as pl
import yaml

from ozon_similar_products.cli.run_full import (
    FullRunResult,
    _build_validation_pair_counts,
    _config_with_top_k_override,
    _git_sha,
    _item_action_types,
    _ranking_evaluation_options,
    validation_window,
)
from ozon_similar_products.evaluation import (
    build_scorecard,
    compute_offline_metrics,
    metrics_to_flat_dict,
    write_json,
)
from ozon_similar_products.evaluation.validation_cache import (
    load_or_build_validation_cache,
    validation_cache_metadata,
)
from ozon_similar_products.output.writers import RecommendationWriter
from ozon_similar_products.pipeline.run_pipeline import (
    PipelineRunResult,
    _as_mapping,
    _as_positive_int,
    _daily_pair_stats_paths_for_date,
    _date_range_strings,
    _pair_aggregator_from_config,
    run_pipeline,
)
from ozon_similar_products.pipeline.scoring_output import run_scoring_output_from_artifacts

SAFE_SCORING_ONLY_PREFIXES = ("scoring.", "topk.", "business.fallback.")
FrameLike = pl.DataFrame | pl.LazyFrame


@dataclass(frozen=True)
class FastScoringContext:
    """Prebuilt artifacts that are safe to reuse across scoring-only trials."""

    pair_aggregate_parts: tuple[FrameLike, ...]
    item_popularity: pl.DataFrame
    action_distribution: pl.DataFrame
    ground_truth: pl.DataFrame
    validation_pair_counts: pl.DataFrame
    train_until_date: date
    lookback_days: int
    validation_days: int
    window_start: str
    window_end: str
    row_counts: dict[str, int]
    validation_cache_key: str
    validation_cache_hit: bool
    validation_cache_dir: Path
    base_pipeline_result: PipelineRunResult

    @property
    def pair_aggregates(self) -> FrameLike | tuple[FrameLike, ...]:
        """Backward-compatible access to the prebuilt aggregate inputs."""
        if len(self.pair_aggregate_parts) == 1:
            return self.pair_aggregate_parts[0]
        return self.pair_aggregate_parts


def validate_scoring_only_search_space(search_space: Mapping[str, Any]) -> None:
    """Ensure fast scoring-only mode cannot change train artifacts."""
    parameters = search_space.get("parameters")
    if not isinstance(parameters, Mapping) or not parameters:
        raise ValueError("search_space.yaml must contain non-empty parameters")

    unsafe_parameters = [
        str(parameter_name)
        for parameter_name in parameters
        if not str(parameter_name).startswith(SAFE_SCORING_ONLY_PREFIXES)
    ]
    if unsafe_parameters:
        allowed = ", ".join(SAFE_SCORING_ONLY_PREFIXES)
        raise ValueError(
            "fast scoring-only mode only supports parameters with prefixes "
            f"{allowed}; got unsafe parameters: {unsafe_parameters}"
        )


def _with_fast_artifact_dirs(config: Mapping[str, Any], artifact_root: Path) -> dict[str, Any]:
    updated = dict(config)
    artifacts = dict(updated.get("artifacts", {})) if isinstance(updated.get("artifacts"), Mapping) else {}
    for key in (
        "events_clean_dir",
        "sessions_dir",
        "item_popularity_dir",
        "action_type_distribution_dir",
        "daily_pairs_dir",
        "pair_aggregates_dir",
    ):
        artifacts[key] = (artifact_root / key.removesuffix("_dir")).as_posix()
    updated["artifacts"] = artifacts
    return updated


def _write_yaml(path: Path, payload: Mapping[str, Any]) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        yaml.safe_dump(dict(payload), sort_keys=False, allow_unicode=True),
        encoding="utf-8",
    )
    return path


def _window_artifact_path(config: Mapping[str, Any], key: str, default: str, window_start: str, window_end: str) -> Path:
    artifacts = config.get("artifacts", {})
    if not isinstance(artifacts, Mapping):
        artifact_dir = Path(default)
    else:
        artifact_dir = Path(str(artifacts.get(key, default)))
    return artifact_dir / f"window_start={window_start}_window_end={window_end}.parquet"


def _artifact_dir(config: Mapping[str, Any], key: str, default: str) -> Path:
    artifacts = config.get("artifacts", {})
    if not isinstance(artifacts, Mapping):
        return Path(default)
    return Path(str(artifacts.get(key, default)))


def _pair_aggregate_part_path(
        output_dir: Path,
        *,
        window_start: str,
        window_end: str,
        bucket_id: int,
        bucket_count: int,
) -> Path:
    return (
        output_dir
        / f"window_start={window_start}_window_end={window_end}_bucket={bucket_id:05d}-of-{bucket_count:05d}.parquet"
    )


def _daily_pair_stat_paths_for_window(
        *,
        config: Mapping[str, Any],
        window_start: str,
        window_end: str,
) -> tuple[list[Path], list[Path], list[Path]]:
    daily_pairs_dir = _artifact_dir(
        config,
        "daily_pairs_dir",
        "data/processed/item_pairs",
    )
    count_paths: list[Path] = []
    user_key_paths: list[Path] = []
    session_key_paths: list[Path] = []
    incomplete_dates: list[str] = []

    for partition_date in _date_range_strings(window_start, window_end):
        count_path, widget_count_path, user_key_path, session_key_path = (
            _daily_pair_stats_paths_for_date(daily_pairs_dir, partition_date)
        )
        required_paths = (count_path, widget_count_path, user_key_path, session_key_path)
        existing_count = sum(path.exists() for path in required_paths)
        if existing_count == len(required_paths):
            count_paths.append(count_path)
            user_key_paths.append(user_key_path)
            session_key_paths.append(session_key_path)
        elif existing_count != 0:
            incomplete_dates.append(partition_date)

    if incomplete_dates:
        raise FileNotFoundError(
            "Incomplete daily pair stats for fast scoring-only bucketed aggregation: "
            f"dates={incomplete_dates}, daily_pairs_dir={daily_pairs_dir}"
        )

    return count_paths, user_key_paths, session_key_paths


def _build_pair_aggregate_part_paths(
        *,
        config: Mapping[str, Any],
        window_start: str,
        window_end: str,
        output_dir: Path,
        logger: logging.Logger,
) -> tuple[Path, ...]:
    pipeline_config = _as_mapping(config.get("pipeline", {}))
    aggregation_item_buckets = _as_positive_int(
        value=pipeline_config.get("aggregation_item_buckets"),
        default=1,
        parameter_name="pipeline.aggregation_item_buckets",
    )

    if aggregation_item_buckets == 1:
        return (
            _window_artifact_path(
                config,
                "pair_aggregates_dir",
                "data/processed/pair_aggregates",
                window_start,
                window_end,
            ),
        )

    count_paths, user_key_paths, session_key_paths = _daily_pair_stat_paths_for_window(
        config=config,
        window_start=window_start,
        window_end=window_end,
    )
    output_dir.mkdir(parents=True, exist_ok=True)
    aggregator = _pair_aggregator_from_config(config)
    part_paths: list[Path] = []

    logger.info(
        "[scoring_only] build bucketed pair aggregate parts buckets=%s from_daily_stat_partitions=%s",
        aggregation_item_buckets,
        len(count_paths),
    )
    for bucket_id in range(aggregation_item_buckets):
        pair_aggregates = aggregator.aggregate_window_from_daily_stats_paths(
            count_paths=count_paths,
            user_key_paths=user_key_paths,
            session_key_paths=session_key_paths,
            window_start=window_start,
            window_end=window_end,
            item_bucket_id=bucket_id,
            item_bucket_count=aggregation_item_buckets,
        )
        part_path = _pair_aggregate_part_path(
            output_dir,
            window_start=window_start,
            window_end=window_end,
            bucket_id=bucket_id,
            bucket_count=aggregation_item_buckets,
        )
        pair_aggregates.write_parquet(part_path)
        part_paths.append(part_path)
        logger.debug(
            "[scoring_only] pair aggregate part=%s/%s rows=%s path=%s",
            bucket_id + 1,
            aggregation_item_buckets,
            pair_aggregates.height,
            part_path,
        )

    logger.info(
        "[scoring_only] built pair aggregate parts buckets=%s output_dir=%s",
        len(part_paths),
        output_dir,
    )
    return tuple(part_paths)


def _row_counts_from_manifest(manifest: Mapping[str, Any]) -> dict[str, int]:
    rows = manifest.get("rows")
    if not isinstance(rows, Mapping):
        return {}
    return {
        str(key): int(value)
        for key, value in rows.items()
        if isinstance(value, int | float | str) and not isinstance(value, bool)
    }


def build_fast_scoring_context(
    *,
    base_config: Mapping[str, Any],
    sweep_dir: Path,
    train_until_date: date,
    lookback_days: int,
    validation_days: int,
    top_k: int | None,
    logger: logging.Logger,
) -> FastScoringContext:
    """Build train artifacts and validation artifacts once for scoring-only trials."""
    base_train_config = _config_with_top_k_override(base_config, top_k)
    fast_root = sweep_dir / "fast_scoring_base"
    artifact_root = fast_root / "artifacts"
    base_train_config = _with_fast_artifact_dirs(base_train_config, artifact_root)
    config_path = _write_yaml(fast_root / "config.yaml", base_train_config)

    pipeline_result = run_pipeline(
        train_until_date=train_until_date.isoformat(),
        lookback_days=lookback_days,
        config_path=config_path,
        output_dir=fast_root,
        run_id="fast_scoring_base",
        update_latest=False,
    )
    window_start = str(pipeline_result.manifest["window_start"])
    window_end = str(pipeline_result.manifest["window_end"])

    pair_aggregate_part_paths = _build_pair_aggregate_part_paths(
        config=base_train_config,
        window_start=window_start,
        window_end=window_end,
        output_dir=artifact_root / "pair_aggregate_parts",
        logger=logger,
    )
    pair_aggregate_parts = tuple(
        pl.scan_parquet(path.as_posix()) for path in pair_aggregate_part_paths
    )
    item_popularity = pl.read_parquet(
        _window_artifact_path(
            base_train_config,
            "item_popularity_dir",
            "data/processed/item_popularity",
            window_start,
            window_end,
        )
    )
    action_distribution = pl.read_parquet(
        _window_artifact_path(
            base_train_config,
            "action_type_distribution_dir",
            "data/processed/action_type_distribution",
            window_start,
            window_end,
        )
    )

    evaluation_config = base_train_config.get("evaluation", {})
    if not isinstance(evaluation_config, Mapping):
        evaluation_config = {}
    relevance_mode = str(evaluation_config.get("relevance_mode", "binary"))
    relevance_weights = evaluation_config.get("relevance_weights")
    if not isinstance(relevance_weights, Mapping):
        relevance_weights = None
    validation_start_date, validation_end_date = validation_window(
        train_until_date,
        validation_days,
    )
    metadata = validation_cache_metadata(
        config=base_train_config,
        validation_start_date=validation_start_date,
        validation_end_date=validation_end_date,
        relevance_mode=relevance_mode,
        relevance_weights=relevance_weights,
        item_action_types=_item_action_types(base_train_config),
        git_sha=_git_sha(),
    )
    validation_cache = load_or_build_validation_cache(
        cache_root=sweep_dir / "validation_cache",
        metadata=metadata,
        relevance_mode=relevance_mode,
        relevance_weights=relevance_weights,
        build_validation_pair_counts=lambda: _build_validation_pair_counts(
            config=base_train_config,
            validation_start_date=validation_start_date,
            validation_end_date=validation_end_date,
            logger=logger,
        ),
        logger=logger,
    )

    return FastScoringContext(
        pair_aggregate_parts=pair_aggregate_parts,
        item_popularity=item_popularity,
        action_distribution=action_distribution,
        ground_truth=validation_cache.ground_truth,
        validation_pair_counts=validation_cache.validation_pair_counts,
        train_until_date=train_until_date,
        lookback_days=lookback_days,
        validation_days=validation_days,
        window_start=window_start,
        window_end=window_end,
        row_counts=_row_counts_from_manifest(pipeline_result.manifest),
        validation_cache_key=validation_cache.cache_key,
        validation_cache_hit=validation_cache.cache_hit,
        validation_cache_dir=validation_cache.cache_dir,
        base_pipeline_result=pipeline_result,
    )


def execute_scoring_only_trial(
    *,
    context: FastScoringContext,
    trial_config: Mapping[str, Any],
    trial_config_path: Path,
    run_id: str,
    run_dir: Path,
    top_k: int | None,
) -> FullRunResult:
    """Run one scoring-only trial and write the same evaluation files as full runs."""
    started = datetime.now(UTC)
    config = _config_with_top_k_override(trial_config, top_k)
    pipeline_result = run_scoring_output_from_artifacts(
        config=config,
        pair_aggregates=context.pair_aggregate_parts,
        item_popularity=context.item_popularity,
        action_distribution=context.action_distribution,
        train_until_date=context.train_until_date.isoformat(),
        lookback_days=context.lookback_days,
        window_start=context.window_start,
        window_end=context.window_end,
        output_dir=run_dir,
        run_id=run_id,
        update_latest=False,
        row_counts=context.row_counts,
    )
    recommendations = pl.read_parquet(pipeline_result.detailed_recommendations_path)
    ranking_relevant_action_types, min_ranking_relevance = _ranking_evaluation_options(config)
    metrics = compute_offline_metrics(
        recommendations=recommendations,
        ground_truth=context.ground_truth,
        top_k=int(top_k) if top_k is not None else int(pipeline_result.manifest["top_k"]),
        context={"item_popularity": context.item_popularity, "popularity_column": "events_count"},
        ranking_relevant_action_types=ranking_relevant_action_types,
        min_ranking_relevance=min_ranking_relevance,
    )

    evaluation_dir = run_dir / "evaluation"
    scorecard = build_scorecard(
        experiment_id=run_id,
        train_until_date=context.train_until_date.isoformat(),
        lookback_days=context.lookback_days,
        top_k=int(top_k) if top_k is not None else int(pipeline_result.manifest["top_k"]),
        metrics=metrics,
        metadata={
            "config_path": trial_config_path,
            "recommendations_path": pipeline_result.detailed_recommendations_path,
            "used_scoring_only_mode": True,
            "used_validation_cache": True,
            "validation_cache_hit": context.validation_cache_hit,
            "validation_cache_key": context.validation_cache_key,
            "validation_cache_dir": context.validation_cache_dir,
        },
    )
    metrics_path = write_json(evaluation_dir / "metrics.json", metrics_to_flat_dict(metrics))
    scorecard_path = write_json(
        evaluation_dir / "scorecard.json",
        {
            "run_id": scorecard.experiment_id,
            "train_until_date": scorecard.train_until_date,
            "lookback_days": scorecard.lookback_days,
            "top_k": scorecard.top_k,
            "metrics": metrics_to_flat_dict(scorecard.metrics),
            "notes": scorecard.notes,
            "metadata": scorecard.metadata,
        },
    )
    evaluation_manifest_path = write_json(
        evaluation_dir / "evaluation_manifest.json",
        {
            "run_id": run_id,
            "created_at": started,
            "train_until_date": context.train_until_date.isoformat(),
            "lookback_days": context.lookback_days,
            "validation_days": context.validation_days,
            "top_k": scorecard.top_k,
            "metrics_path": "evaluation/metrics.json",
            "scorecard_path": "evaluation/scorecard.json",
            "used_scoring_only_mode": True,
            "used_validation_cache": True,
            "validation_cache_hit": context.validation_cache_hit,
            "validation_cache_key": context.validation_cache_key,
            "validation_cache_dir": context.validation_cache_dir,
        },
    )

    manifest = dict(pipeline_result.manifest)
    manifest.update(
        {
            "run_type": "scoring_only_trial",
            "metrics_path": "evaluation/metrics.json",
            "scorecard_path": "evaluation/scorecard.json",
            "evaluation_manifest_path": "evaluation/evaluation_manifest.json",
            "used_scoring_only_mode": True,
            "used_validation_cache": True,
            "validation_cache_hit": context.validation_cache_hit,
            "validation_cache_key": context.validation_cache_key,
            "validation_cache_dir": context.validation_cache_dir,
            "paths": {
                **dict(pipeline_result.manifest["paths"]),
                "metrics_path": "evaluation/metrics.json",
                "scorecard_path": "evaluation/scorecard.json",
                "evaluation_manifest_path": "evaluation/evaluation_manifest.json",
            },
        }
    )
    manifest_path = RecommendationWriter().save_manifest(manifest, run_dir)
    pipeline_result = PipelineRunResult(
        run_id=pipeline_result.run_id,
        run_dir=pipeline_result.run_dir,
        manifest_path=manifest_path,
        detailed_recommendations_path=pipeline_result.detailed_recommendations_path,
        enriched_recommendations_path=pipeline_result.enriched_recommendations_path,
        lookup_recommendations_path=pipeline_result.lookup_recommendations_path,
        manifest=manifest,
    )
    return FullRunResult(
        run_id=run_id,
        run_dir=run_dir,
        manifest_path=manifest_path,
        metrics_path=metrics_path,
        scorecard_path=scorecard_path,
        evaluation_manifest_path=evaluation_manifest_path,
        metrics=metrics,
        pipeline_result=pipeline_result,
    )
