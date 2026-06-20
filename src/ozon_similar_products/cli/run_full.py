"""CLI entrypoint for full recommendation runs with offline evaluation."""

from __future__ import annotations

import argparse
import logging
import shutil
import time
from collections.abc import Mapping
from copy import deepcopy
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any

import polars as pl
import yaml

from ozon_similar_products.config import PROJECT_ROOT, load_yaml_config
from ozon_similar_products.data import load_configs, load_events, schemas
from ozon_similar_products.data.frames import empty_contract_frame
from ozon_similar_products.evaluation import (
    build_ground_truth_from_daily_pair_counts,
    build_scorecard,
    compute_offline_metrics,
    metrics_to_flat_dict,
    write_json,
)
from ozon_similar_products.evaluation.metrics import OfflineMetrics
from ozon_similar_products.evaluation.validation_cache import (
    load_or_build_validation_cache,
    validation_cache_metadata,
)
from ozon_similar_products.output.writers import RecommendationWriter
from ozon_similar_products.pipeline.run_pipeline import PipelineRunResult, run_pipeline
from ozon_similar_products.preprocessing.build_sessions import SessionBuilder
from ozon_similar_products.preprocessing.clean_events import EventCleaner
from ozon_similar_products.retrieval.build_pairs import ItemPairBuilder


@dataclass(frozen=True)
class FullRunResult:
    """Paths and metrics produced by one full run."""

    run_id: str
    run_dir: Path
    manifest_path: Path
    metrics_path: Path
    scorecard_path: Path
    evaluation_manifest_path: Path
    metrics: OfflineMetrics
    pipeline_result: PipelineRunResult


def _parse_iso_date(value: str) -> date:
    try:
        return date.fromisoformat(value)
    except ValueError as error:
        raise argparse.ArgumentTypeError(
            "date must be an ISO date string: YYYY-MM-DD"
        ) from error


def validation_window(train_until_date: date, validation_days: int) -> tuple[date, date]:
    """Return the inclusive validation window immediately after train_until_date."""
    if validation_days <= 0:
        raise ValueError("validation_days must be a positive integer")
    validation_start_date = train_until_date + timedelta(days=1)
    validation_end_date = train_until_date + timedelta(days=validation_days)
    return validation_start_date, validation_end_date


def _date_range_strings(start_date: date, end_date: date) -> list[str]:
    if start_date > end_date:
        raise ValueError("start_date must be <= end_date")

    return [
        (start_date + timedelta(days=offset)).isoformat()
        for offset in range((end_date - start_date).days + 1)
    ]


def _git_sha() -> str | None:
    return None


def _timestamp_slug() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%d_%H-%M-%SZ")


def _safe_label(value: str | None) -> str:
    if not value:
        return ""
    normalized = "".join(
        character if character.isalnum() or character in {"-", "_"} else "_"
        for character in value.strip()
    ).strip("_")
    return normalized


def make_run_id(
    *,
    train_until_date: date,
    lookback_days: int,
    validation_days: int,
    top_k: int,
    run_name: str | None,
) -> str:
    parts = [
        "run",
        _timestamp_slug(),
        f"train-{train_until_date.isoformat()}",
        f"lookback-{lookback_days}d",
        f"validation-{validation_days}d",
        f"top-{top_k}",
    ]
    label = _safe_label(run_name)
    if label:
        parts.append(label)
    return "_".join(parts)


def _config_with_top_k_override(
    config: Mapping[str, Any],
    top_k: int | None,
) -> dict[str, Any]:
    overridden = deepcopy(dict(config))
    if top_k is None:
        return overridden

    for section_name in ("pipeline", "topk"):
        section = overridden.get(section_name)
        if section is None:
            overridden[section_name] = {"top_k": top_k}
            continue
        if not isinstance(section, Mapping):
            raise TypeError(f"{section_name} section must be a mapping")

        section_copy = dict(section)
        section_copy["top_k"] = top_k
        overridden[section_name] = section_copy

    business = overridden.get("business")
    if isinstance(business, Mapping):
        business_copy = dict(business)
        fallback = business_copy.get("fallback")
        if isinstance(fallback, Mapping):
            fallback_copy = dict(fallback)
            fallback_copy["top_k"] = top_k
            business_copy["fallback"] = fallback_copy
            overridden["business"] = business_copy

    return overridden


def _top_k_from_config(config: Mapping[str, Any], requested_top_k: int | None) -> int:
    if requested_top_k is not None:
        if requested_top_k <= 0:
            raise ValueError("top_k must be a positive integer")
        return requested_top_k

    topk = config.get("topk", {})
    pipeline = config.get("pipeline", {})
    top_k = (
        topk.get("top_k") if isinstance(topk, Mapping) else None
    ) or (
        pipeline.get("top_k") if isinstance(pipeline, Mapping) else None
    ) or 20
    if isinstance(top_k, bool) or int(top_k) <= 0:
        raise ValueError("top_k must be a positive integer")
    return int(top_k)


def _item_action_types(config: Mapping[str, Any]) -> list[str]:
    events_config = config.get("events", {})
    if not isinstance(events_config, Mapping):
        return list(schemas.ITEM_SIGNAL_TYPES)

    action_types = events_config.get("item_action_types", schemas.ITEM_SIGNAL_TYPES)
    if isinstance(action_types, str):
        return [action_types]

    return list(action_types)


def _write_config_snapshot(config: Mapping[str, Any], path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        yaml.safe_dump(dict(config), sort_keys=False, allow_unicode=True),
        encoding="utf-8",
    )
    return path


def _resolve_project_path(value: Any, default: str) -> Path:
    path = Path(value) if isinstance(value, str | Path) else Path(default)
    if path.is_absolute():
        return path
    return (PROJECT_ROOT / path).resolve()


def _validation_cache_root(config: Mapping[str, Any]) -> Path:
    outputs_config = config.get("outputs", {})
    root_dir = "outputs"
    if isinstance(outputs_config, Mapping):
        root_dir = str(outputs_config.get("root_dir", root_dir))
    return _resolve_project_path(
        Path(root_dir) / "cache" / "validation",
        "outputs/cache/validation",
    )


def _build_validation_pair_counts(
    *,
    config: Mapping[str, Any],
    validation_start_date: date,
    validation_end_date: date,
    logger: logging.Logger,
) -> pl.DataFrame:
    """Build compact validation pair counts day by day."""
    data_config = load_configs(project_root=PROJECT_ROOT)
    action_types = _item_action_types(config)

    cleaner = EventCleaner(item_action_types=action_types)
    session_builder = SessionBuilder.from_config(dict(config))
    pair_builder = ItemPairBuilder.from_config(dict(config))

    count_frames: list[pl.DataFrame] = []
    missing_dates: list[str] = []

    for partition_date in _date_range_strings(
        validation_start_date,
        validation_end_date,
    ):
        try:
            raw_day = load_events(
                config=data_config,
                use_sample=False,
                dates=[partition_date],
                action_types=action_types,
            )
        except FileNotFoundError:
            missing_dates.append(partition_date)
            continue

        clean_day = cleaner.transform_day(raw_day)
        sessions_day = session_builder.transform_day(clean_day)
        stats = pair_builder.build_daily_pair_stats(sessions_day)

        if not stats.counts.is_empty():
            count_frames.append(stats.counts)

        logger.info(
            "[run_full] validation date=%s raw_rows=%s clean_rows=%s "
            "sessions_rows=%s pair_count_rows=%s",
            partition_date,
            raw_day.height,
            clean_day.height,
            sessions_day.height,
            stats.counts.height,
        )

    if missing_dates:
        logger.warning("[run_full] validation raw events missing for dates=%s", missing_dates)

    if not count_frames:
        logger.warning("[run_full] validation pair counts are empty")
        return empty_contract_frame(schemas.DAILY_PAIR_COUNTS_COLUMNS)

    return (
        pl.concat(count_frames, how="vertical")
        .group_by(["pair_date", "item_id", "similar_item_id"])
        .agg(
            pl.col("pair_count").sum().alias("pair_count"),
            pl.col("view_count").sum().alias("view_count"),
            pl.col("click_count").sum().alias("click_count"),
            pl.col("favorite_count").sum().alias("favorite_count"),
            pl.col("to_cart_count").sum().alias("to_cart_count"),
            pl.col("weighted_pair_count").sum().alias("weighted_pair_count"),
            pl.col("weighted_view_count").sum().alias("weighted_view_count"),
            pl.col("weighted_click_count").sum().alias("weighted_click_count"),
            pl.col("weighted_favorite_count").sum().alias("weighted_favorite_count"),
            pl.col("weighted_to_cart_count").sum().alias("weighted_to_cart_count"),
        )
        .select(schemas.DAILY_PAIR_COUNTS_COLUMNS)
        .sort(["pair_date", "item_id", "similar_item_id"])
    )


def _find_item_popularity_artifact(
    config: Mapping[str, Any],
    window_start: str,
    window_end: str,
) -> Path | None:
    artifacts = config.get("artifacts", {})
    if not isinstance(artifacts, Mapping):
        return None

    item_popularity_dir = _resolve_project_path(
        artifacts.get("item_popularity_dir"),
        "data/processed/item_popularity",
    )
    expected = item_popularity_dir / f"window_start={window_start}_window_end={window_end}.parquet"
    if not expected.exists():
        return None
    return expected


def _publish_latest_full_run(run_dir: Path, latest_dir: Path, full_manifest: Mapping[str, Any]) -> None:
    writer = RecommendationWriter()
    if latest_dir.exists():
        shutil.rmtree(latest_dir)

    (latest_dir / "recommendations").mkdir(parents=True, exist_ok=True)
    (latest_dir / "evaluation").mkdir(parents=True, exist_ok=True)

    shutil.copy2(
        run_dir / "recommendations" / "detailed.parquet",
        latest_dir / "recommendations" / "detailed.parquet",
    )
    shutil.copy2(
        run_dir / "recommendations" / "enriched.parquet",
        latest_dir / "recommendations" / "enriched.parquet",
    )
    shutil.copy2(
        run_dir / "recommendations" / "lookup.parquet",
        latest_dir / "recommendations" / "lookup.parquet",
    )
    for filename in ("metrics.json", "scorecard.json", "evaluation_manifest.json"):
        shutil.copy2(run_dir / "evaluation" / filename, latest_dir / "evaluation" / filename)

    latest_manifest = dict(full_manifest)
    latest_manifest["paths"] = {
        "detailed_recommendations_path": "recommendations/detailed.parquet",
        "enriched_recommendations_path": "recommendations/enriched.parquet",
        "widget_recommendations_path": "recommendations/lookup.parquet",
        "lookup_recommendations_path": "recommendations/lookup.parquet",
        "metrics_path": "evaluation/metrics.json",
        "scorecard_path": "evaluation/scorecard.json",
        "evaluation_manifest_path": "evaluation/evaluation_manifest.json",
    }
    latest_manifest.update(latest_manifest["paths"])
    writer.save_manifest(latest_manifest, latest_dir)


def execute_full_run(
    *,
    train_until_date: date,
    lookback_days: int,
    validation_days: int,
    top_k: int | None,
    config_path: Path,
    run_name: str | None = None,
    output_dir: Path = Path("outputs/runs"),
    run_id: str | None = None,
    run_dir: Path | None = None,
    latest_dir: Path = Path("outputs/latest"),
    keep_evaluation_artifacts: bool = False,
    publish_latest: bool = True,
    validation_pair_counts: pl.DataFrame | None = None,
    ground_truth: pl.DataFrame | None = None,
    used_validation_cache: bool = False,
    validation_cache_hit: bool = False,
    validation_cache_key: str | None = None,
    validation_cache_dir: Path | None = None,
    used_scoring_only_mode: bool = False,
) -> FullRunResult:
    """Run train recommendations, build validation ground truth, and compute metrics."""
    logger = logging.getLogger(__name__)
    started = time.perf_counter()

    if lookback_days <= 0:
        raise ValueError("lookback_days must be a positive integer")

    validation_start_date, validation_end_date = validation_window(
        train_until_date,
        validation_days,
    )
    config = load_yaml_config(config_path)
    resolved_top_k = _top_k_from_config(config, top_k)
    config = _config_with_top_k_override(config, resolved_top_k)
    evaluation_config = config.get("evaluation", {})
    if not isinstance(evaluation_config, Mapping):
        evaluation_config = {}
    relevance_mode = str(evaluation_config.get("relevance_mode", "binary"))
    relevance_weights = evaluation_config.get("relevance_weights")
    if not isinstance(relevance_weights, Mapping):
        relevance_weights = None

    resolved_run_id = run_id or make_run_id(
        train_until_date=train_until_date,
        lookback_days=lookback_days,
        validation_days=validation_days,
        top_k=resolved_top_k,
        run_name=run_name,
    )
    resolved_run_dir = (
        _resolve_project_path(run_dir, "outputs/runs")
        if run_dir is not None
        else _resolve_project_path(output_dir, "outputs/runs") / resolved_run_id
    )
    resolved_run_dir.mkdir(parents=True, exist_ok=run_dir is not None)

    config_snapshot_path = _write_config_snapshot(config, resolved_run_dir / "config.yaml")

    pipeline_result = run_pipeline(
        train_until_date=train_until_date.isoformat(),
        lookback_days=lookback_days,
        config_path=config_snapshot_path,
        output_dir=resolved_run_dir,
        run_id=resolved_run_id,
        update_latest=False,
    )

    recommendations = pl.read_parquet(pipeline_result.detailed_recommendations_path)

    validation_pair_counts_seconds = 0.0
    ground_truth_seconds = 0.0
    if ground_truth is not None:
        logger.info("[run_full] reuse prebuilt validation ground truth")
        if validation_pair_counts is None:
            validation_pair_counts = empty_contract_frame(schemas.DAILY_PAIR_COUNTS_COLUMNS)
    elif validation_pair_counts is not None:
        logger.info("[run_full] reuse prebuilt validation pair counts")
        ground_truth_started = time.perf_counter()
        ground_truth = build_ground_truth_from_daily_pair_counts(
            validation_pair_counts,
            relevance_mode=relevance_mode,
            action_weights=relevance_weights,
        )
        ground_truth_seconds = time.perf_counter() - ground_truth_started
    else:
        cache_started = time.perf_counter()
        metadata = validation_cache_metadata(
            config=config,
            validation_start_date=validation_start_date,
            validation_end_date=validation_end_date,
            relevance_mode=relevance_mode,
            relevance_weights=relevance_weights,
            item_action_types=_item_action_types(config),
            git_sha=_git_sha(),
        )
        validation_cache = load_or_build_validation_cache(
            cache_root=_validation_cache_root(config),
            metadata=metadata,
            relevance_mode=relevance_mode,
            relevance_weights=relevance_weights,
            build_validation_pair_counts=lambda: _build_validation_pair_counts(
                config=config,
                validation_start_date=validation_start_date,
                validation_end_date=validation_end_date,
                logger=logger,
            ),
            logger=logger,
        )
        cache_elapsed = time.perf_counter() - cache_started
        validation_pair_counts = validation_cache.validation_pair_counts
        ground_truth = validation_cache.ground_truth
        used_validation_cache = True
        validation_cache_hit = validation_cache.cache_hit
        validation_cache_key = validation_cache.cache_key
        validation_cache_dir = validation_cache.cache_dir
        if validation_cache.cache_hit:
            logger.info("[run_full] validation cache hit in %.2fs", cache_elapsed)
        else:
            logger.info("[run_full] validation cache miss/build in %.2fs", cache_elapsed)
            validation_pair_counts_seconds = cache_elapsed

    evaluation_dir = resolved_run_dir / "evaluation"
    debug_dir = evaluation_dir / "debug"
    if keep_evaluation_artifacts:
        debug_dir.mkdir(parents=True, exist_ok=True)
        validation_pair_counts.write_parquet(debug_dir / "validation_pair_counts.parquet")
        ground_truth.write_parquet(debug_dir / "ground_truth.parquet")

    context: dict[str, Any] = {}
    item_popularity_path = _find_item_popularity_artifact(
        config,
        str(pipeline_result.manifest["window_start"]),
        str(pipeline_result.manifest["window_end"]),
    )
    if item_popularity_path is not None:
        context["item_popularity"] = pl.read_parquet(item_popularity_path)
        context["popularity_column"] = "events_count"

    logger.info("[run_full] compute metrics")
    metrics_started = time.perf_counter()
    metrics = compute_offline_metrics(
        recommendations=recommendations,
        ground_truth=ground_truth,
        top_k=resolved_top_k,
        context=context,
    )
    metrics_seconds = time.perf_counter() - metrics_started

    scorecard = build_scorecard(
        experiment_id=resolved_run_id,
        train_until_date=train_until_date.isoformat(),
        lookback_days=lookback_days,
        top_k=resolved_top_k,
        metrics=metrics,
        metadata={
            "validation_start_date": validation_start_date.isoformat(),
            "validation_end_date": validation_end_date.isoformat(),
            "validation_days": validation_days,
            "git_sha": _git_sha(),
            "config_path": config_snapshot_path,
            "recommendations_path": pipeline_result.detailed_recommendations_path,
            "keep_evaluation_artifacts": keep_evaluation_artifacts,
            "used_validation_cache": used_validation_cache,
            "validation_cache_hit": validation_cache_hit,
            "validation_cache_key": validation_cache_key,
            "validation_cache_dir": validation_cache_dir,
            "used_scoring_only_mode": used_scoring_only_mode,
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

    elapsed_seconds = time.perf_counter() - started
    evaluation_manifest: dict[str, Any] = {
        "run_id": resolved_run_id,
        "created_at": datetime.now(UTC),
        "git_sha": _git_sha(),
        "train_until_date": train_until_date.isoformat(),
        "lookback_days": lookback_days,
        "validation_start_date": validation_start_date.isoformat(),
        "validation_end_date": validation_end_date.isoformat(),
        "validation_days": validation_days,
        "top_k": resolved_top_k,
        "recommendations_path": "recommendations/detailed.parquet",
        "metrics_path": "evaluation/metrics.json",
        "scorecard_path": "evaluation/scorecard.json",
        "debug_artifacts_kept": keep_evaluation_artifacts,
        "elapsed_seconds": elapsed_seconds,
        "validation_pair_counts_seconds": validation_pair_counts_seconds,
        "ground_truth_seconds": ground_truth_seconds,
        "metrics_seconds": metrics_seconds,
        "used_validation_cache": used_validation_cache,
        "validation_cache_hit": validation_cache_hit,
        "validation_cache_key": validation_cache_key,
        "validation_cache_dir": validation_cache_dir,
        "used_scoring_only_mode": used_scoring_only_mode,
    }
    if keep_evaluation_artifacts:
        evaluation_manifest["debug_paths"] = {
            "validation_pair_counts_path": "evaluation/debug/validation_pair_counts.parquet",
            "ground_truth_path": "evaluation/debug/ground_truth.parquet",
        }
    evaluation_manifest_path = write_json(
        evaluation_dir / "evaluation_manifest.json",
        evaluation_manifest,
    )

    full_manifest = dict(pipeline_result.manifest)
    full_manifest.update(
        {
            "run_id": resolved_run_id,
            "run_type": "full",
            "git_sha": _git_sha(),
            "validation_start_date": validation_start_date.isoformat(),
            "validation_end_date": validation_end_date.isoformat(),
            "validation_days": validation_days,
            "metrics_path": "evaluation/metrics.json",
            "scorecard_path": "evaluation/scorecard.json",
            "evaluation_manifest_path": "evaluation/evaluation_manifest.json",
            "elapsed_seconds": elapsed_seconds,
            "used_validation_cache": used_validation_cache,
            "validation_cache_hit": validation_cache_hit,
            "validation_cache_key": validation_cache_key,
            "validation_cache_dir": validation_cache_dir,
            "used_scoring_only_mode": used_scoring_only_mode,
            "paths": {
                **dict(pipeline_result.manifest["paths"]),
                "metrics_path": "evaluation/metrics.json",
                "scorecard_path": "evaluation/scorecard.json",
                "evaluation_manifest_path": "evaluation/evaluation_manifest.json",
            },
        }
    )
    manifest_path = RecommendationWriter().save_manifest(full_manifest, resolved_run_dir)

    if publish_latest:
        _publish_latest_full_run(
            resolved_run_dir,
            _resolve_project_path(latest_dir, "outputs/latest"),
            full_manifest,
        )

    logger.info("[run_full] metrics=%s", metrics)
    logger.info("[run_full] done run_dir=%s", resolved_run_dir)
    return FullRunResult(
        run_id=resolved_run_id,
        run_dir=resolved_run_dir,
        manifest_path=manifest_path,
        metrics_path=metrics_path,
        scorecard_path=scorecard_path,
        evaluation_manifest_path=evaluation_manifest_path,
        metrics=metrics,
        pipeline_result=pipeline_result,
    )


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Run recommendations and evaluate them on the following validation window.",
    )
    parser.add_argument(
        "train_until_date",
        type=_parse_iso_date,
        help="Inclusive train window end date in ISO format: YYYY-MM-DD.",
    )
    parser.add_argument("--lookback-days", type=int, default=30)
    parser.add_argument("--validation-days", type=int, default=1)
    parser.add_argument("--top-k", type=int, default=None)
    parser.add_argument(
        "--config-path",
        type=Path,
        default=Path("configs/production.yaml"),
    )
    parser.add_argument("--run-name", default=None)
    parser.add_argument("--output-dir", type=Path, default=Path("outputs/runs"))
    parser.add_argument("--latest-dir", type=Path, default=Path("outputs/latest"))
    parser.add_argument("--keep-evaluation-artifacts", action="store_true")
    return parser.parse_args()


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    logger = logging.getLogger(__name__)
    args = parse_args()

    try:
        execute_full_run(
            train_until_date=args.train_until_date,
            lookback_days=args.lookback_days,
            validation_days=args.validation_days,
            top_k=args.top_k,
            config_path=args.config_path,
            run_name=args.run_name,
            output_dir=args.output_dir,
            latest_dir=args.latest_dir,
            keep_evaluation_artifacts=args.keep_evaluation_artifacts,
        )
    except Exception:
        logger.exception(
            "[run_full] failed train_until_date=%s lookback_days=%s validation_days=%s config=%s",
            args.train_until_date,
            args.lookback_days,
            args.validation_days,
            args.config_path,
        )
        return 1

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
