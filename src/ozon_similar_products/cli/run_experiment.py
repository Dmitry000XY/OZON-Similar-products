"""CLI entrypoint for offline recommendation experiments."""

from __future__ import annotations

import argparse
import logging
import subprocess
import time
from collections.abc import Mapping
from copy import deepcopy
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any

import polars as pl
import yaml

from ozon_similar_products.config import PROJECT_ROOT, load_yaml_config
from ozon_similar_products.data import load_configs, load_events, schemas
from ozon_similar_products.evaluation import (
    build_ground_truth_from_sessions,
    build_scorecard,
    compute_offline_metrics,
    metrics_to_flat_dict,
    write_json,
)
from ozon_similar_products.evaluation.tracking import append_experiment_index
from ozon_similar_products.output.manifest import find_manifest_path, load_manifest
from ozon_similar_products.pipeline.run_mvp import run_mvp_pipeline
from ozon_similar_products.preprocessing.build_sessions import SessionBuilder
from ozon_similar_products.preprocessing.clean_events import EventCleaner


def _parse_iso_date(value: str) -> date:
    try:
        return date.fromisoformat(value)
    except ValueError as error:
        raise argparse.ArgumentTypeError("date must be an ISO date string: YYYY-MM-DD") from error


def _date_range_strings(start_date: date, end_date: date) -> list[str]:
    if start_date > end_date:
        raise ValueError("start_date must be <= end_date")

    return [
        (start_date + timedelta(days=offset)).isoformat()
        for offset in range((end_date - start_date).days + 1)
    ]


def _git_sha() -> str | None:
    try:
        result = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            check=False,
            capture_output=True,
            text=True,
        )
    except OSError:
        return None

    if result.returncode != 0:
        return None
    return result.stdout.strip() or None


def _experiment_id(name: str | None) -> str:
    timestamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    if not name:
        return f"exp_{timestamp}"

    normalized = "".join(
        character if character.isalnum() or character in {"-", "_"} else "_"
        for character in name.strip()
    )
    normalized = normalized.strip("_")
    if not normalized:
        return f"exp_{timestamp}"

    return f"{timestamp}_{normalized}"


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


def _item_action_types(config: Mapping[str, Any]) -> list[str]:
    events_config = config.get("events", {})
    if not isinstance(events_config, Mapping):
        return list(schemas.ITEM_SIGNAL_TYPES)

    action_types = events_config.get("item_action_types", schemas.ITEM_SIGNAL_TYPES)
    if isinstance(action_types, str):
        return [action_types]
    return list(action_types)


def _config_with_experiment_paths(
    config: Mapping[str, Any],
    experiment_dir: Path,
) -> dict[str, Any]:
    overridden = deepcopy(dict(config))

    artifacts_dir = experiment_dir / "artifacts"
    recommendations_dir = experiment_dir / "recommendations"

    artifacts = dict(overridden.get("artifacts", {}))
    artifacts.update(
        {
            "events_clean_dir": str(artifacts_dir / "events_clean"),
            "sessions_dir": str(artifacts_dir / "sessions"),
            "item_popularity_dir": str(artifacts_dir / "item_popularity"),
            "action_type_distribution_dir": str(artifacts_dir / "action_type_distribution"),
            "daily_pairs_dir": str(artifacts_dir / "item_pairs"),
            "pair_aggregates_dir": str(artifacts_dir / "pair_aggregates"),
        }
    )

    outputs = dict(overridden.get("outputs", {}))
    outputs.update(
        {
            "detailed_recommendations_dir": str(recommendations_dir / "detailed"),
            "widget_recommendations_dir": str(recommendations_dir / "widget"),
            "latest_dir": str(recommendations_dir / "latest"),
        }
    )

    overridden["artifacts"] = artifacts
    overridden["outputs"] = outputs
    return overridden


def _write_config_snapshot(config: Mapping[str, Any], path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        yaml.safe_dump(dict(config), sort_keys=False, allow_unicode=True),
        encoding="utf-8",
    )
    return path


def _resolve_manifest_artifact_path(
    manifest_path: Path,
    manifest: Mapping[str, Any],
    key: str,
) -> Path:
    path_value = find_manifest_path(manifest, key)
    if path_value is None:
        raise KeyError(f"Manifest does not contain artifact path: {key}")

    artifact_path = Path(path_value)
    if artifact_path.is_absolute():
        return artifact_path

    return (manifest_path.parent / artifact_path).resolve()


def _build_validation_sessions(
    *,
    config: Mapping[str, Any],
    validation_start_date: date,
    validation_end_date: date,
) -> pl.DataFrame:
    data_config = load_configs(project_root=PROJECT_ROOT)
    action_types = _item_action_types(config)

    raw_validation_events = load_events(
        config=data_config,
        use_sample=False,
        dates=_date_range_strings(validation_start_date, validation_end_date),
        action_types=action_types,
    )

    cleaner = EventCleaner(item_action_types=action_types)
    clean_validation_events = cleaner.transform_day(raw_validation_events)

    session_builder = SessionBuilder.from_config(dict(config))
    return session_builder.transform_window([clean_validation_events])


def _find_item_popularity_artifact(experiment_dir: Path) -> Path | None:
    candidates = sorted((experiment_dir / "artifacts" / "item_popularity").glob("*.parquet"))
    if not candidates:
        return None
    return candidates[-1]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Run MVP pipeline and evaluate recommendations on a future validation window.",
    )
    parser.add_argument(
        "train_until_date",
        type=_parse_iso_date,
        help="Inclusive train window end date in ISO format: YYYY-MM-DD.",
    )
    parser.add_argument(
        "--lookback-days",
        type=int,
        default=30,
        help="Train rolling window size in days.",
    )
    parser.add_argument(
        "--validation-start-date",
        type=_parse_iso_date,
        required=True,
        help="Validation window start date in ISO format: YYYY-MM-DD.",
    )
    parser.add_argument(
        "--validation-end-date",
        type=_parse_iso_date,
        required=True,
        help="Validation window end date in ISO format: YYYY-MM-DD.",
    )
    parser.add_argument(
        "--top-k",
        type=int,
        default=None,
        help="Override recommendation top-K for this experiment.",
    )
    parser.add_argument(
        "--config-path",
        type=Path,
        default=Path("configs/baseline.yaml"),
        help="Path to experiment config.",
    )
    parser.add_argument(
        "--experiment-name",
        default=None,
        help="Human-readable experiment name.",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=Path("outputs/experiments"),
        help="Directory where experiment artifacts are saved.",
    )
    return parser.parse_args()


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    logger = logging.getLogger(__name__)

    args = parse_args()
    started = time.perf_counter()

    if args.validation_start_date <= args.train_until_date:
        raise ValueError("validation-start-date must be after train_until_date")
    if args.validation_start_date > args.validation_end_date:
        raise ValueError("validation-start-date must be <= validation-end-date")
    if args.lookback_days <= 0:
        raise ValueError("lookback-days must be a positive integer")
    if args.top_k is not None and args.top_k <= 0:
        raise ValueError("top-k must be a positive integer")

    experiment_id = _experiment_id(args.experiment_name)
    experiment_dir = args.output_dir / experiment_id
    experiment_dir.mkdir(parents=True, exist_ok=False)

    logger.info("[run_experiment] experiment_id=%s", experiment_id)

    config = load_yaml_config(args.config_path)
    config = _config_with_top_k_override(config, args.top_k)
    config = _config_with_experiment_paths(config, experiment_dir)

    config_snapshot_path = _write_config_snapshot(
        config,
        experiment_dir / "config.yaml",
    )

    top_k = int(
        args.top_k
        or config.get("topk", {}).get("top_k")
        or config.get("pipeline", {}).get("top_k")
        or 20
    )

    logger.info("[run_experiment] run training pipeline")
    run_mvp_pipeline(
        train_until_date=args.train_until_date.isoformat(),
        lookback_days=args.lookback_days,
        config_path=config_snapshot_path,
    )

    latest_manifest_path = experiment_dir / "recommendations" / "latest" / "manifest.json"
    pipeline_manifest = load_manifest(latest_manifest_path)
    detailed_recommendations_path = _resolve_manifest_artifact_path(
        latest_manifest_path,
        pipeline_manifest,
        "detailed_recommendations_path",
    )

    recommendations = pl.read_parquet(detailed_recommendations_path)

    logger.info("[run_experiment] build validation ground truth")
    validation_sessions = _build_validation_sessions(
        config=config,
        validation_start_date=args.validation_start_date,
        validation_end_date=args.validation_end_date,
    )
    ground_truth = build_ground_truth_from_sessions(validation_sessions)

    ground_truth_path = experiment_dir / "ground_truth.parquet"
    ground_truth.write_parquet(ground_truth_path)

    item_popularity_path = _find_item_popularity_artifact(experiment_dir)
    context: dict[str, Any] = {}
    if item_popularity_path is not None:
        context["item_popularity"] = pl.read_parquet(item_popularity_path)
        context["popularity_column"] = "events_count"

    logger.info("[run_experiment] compute metrics")
    metrics = compute_offline_metrics(
        recommendations=recommendations,
        ground_truth=ground_truth,
        top_k=top_k,
        context=context,
    )

    scorecard = build_scorecard(
        experiment_id=experiment_id,
        train_until_date=args.train_until_date.isoformat(),
        lookback_days=args.lookback_days,
        top_k=top_k,
        metrics=metrics,
        metadata={
            "validation_start_date": args.validation_start_date.isoformat(),
            "validation_end_date": args.validation_end_date.isoformat(),
            "git_sha": _git_sha(),
            "config_path": config_snapshot_path,
            "recommendations_path": detailed_recommendations_path,
            "ground_truth_path": ground_truth_path,
        },
    )

    metrics_path = write_json(
        experiment_dir / "metrics.json",
        metrics_to_flat_dict(metrics),
    )
    scorecard_path = write_json(
        experiment_dir / "scorecard.json",
        {
            "experiment_id": scorecard.experiment_id,
            "train_until_date": scorecard.train_until_date,
            "lookback_days": scorecard.lookback_days,
            "top_k": scorecard.top_k,
            "metrics": metrics_to_flat_dict(scorecard.metrics),
            "notes": scorecard.notes,
            "metadata": scorecard.metadata,
        },
    )

    elapsed_seconds = time.perf_counter() - started

    experiment_manifest = {
        "experiment_id": experiment_id,
        "experiment_name": args.experiment_name,
        "created_at": datetime.now(UTC),
        "git_sha": _git_sha(),
        "config_path": config_snapshot_path,
        "train_until_date": args.train_until_date.isoformat(),
        "lookback_days": args.lookback_days,
        "validation_start_date": args.validation_start_date.isoformat(),
        "validation_end_date": args.validation_end_date.isoformat(),
        "top_k": top_k,
        "pipeline_manifest_path": latest_manifest_path,
        "recommendations_path": detailed_recommendations_path,
        "ground_truth_path": ground_truth_path,
        "metrics_path": metrics_path,
        "scorecard_path": scorecard_path,
        "elapsed_seconds": elapsed_seconds,
    }
    write_json(experiment_dir / "experiment_manifest.json", experiment_manifest)

    index_row = {
        "experiment_id": experiment_id,
        "experiment_name": args.experiment_name,
        "created_at": datetime.now(UTC).isoformat(),
        "git_sha": _git_sha(),
        "train_until_date": args.train_until_date.isoformat(),
        "lookback_days": args.lookback_days,
        "validation_start_date": args.validation_start_date.isoformat(),
        "validation_end_date": args.validation_end_date.isoformat(),
        "top_k": top_k,
        "elapsed_seconds": round(elapsed_seconds, 3),
        **metrics_to_flat_dict(metrics),
    }
    append_experiment_index(args.output_dir / "index.csv", index_row)

    logger.info("[run_experiment] metrics=%s", metrics)
    logger.info("[run_experiment] done experiment_dir=%s", experiment_dir)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
