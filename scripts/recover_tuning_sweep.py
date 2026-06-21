"""Recover tuning sweep summaries and optionally prune heavy trial artifacts.

This utility is intended for long local-heavy tuning sweeps that were interrupted
before `run_tune.py` wrote the final sweep-level files. It reconstructs a
results table from per-trial metrics/config files and can delete heavyweight
per-trial intermediate artifacts after metrics have been preserved.
"""

from __future__ import annotations

import argparse
import csv
import json
import math
import shutil
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import yaml

TRIALS_DIR_NAME = "trials"
DEFAULT_PRIMARY_METRIC = "to_cart_hit_rate_at_k"
DEFAULT_SUPPORTING_METRICS = [
    "ndcg_at_k",
    "recall_at_k",
    "mrr_at_k",
    "coverage_at_k",
    "to_cart_recall_at_k",
]
DEFAULT_PENALTY_METRICS = ["popularity_bias_at_k"]


@dataclass(frozen=True)
class TrialArtifacts:
    trial_id: str
    run_dir: Path
    metrics_path: Path
    config_path: Path | None
    manifest_path: Path | None
    stage: int | None = None


def _load_json(path: Path) -> dict[str, Any]:
    with path.open(encoding="utf-8") as file:
        payload = json.load(file)
    if not isinstance(payload, dict):
        raise ValueError(f"JSON payload must be an object: {path}")
    return payload


def _load_yaml(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    with path.open(encoding="utf-8") as file:
        payload = yaml.safe_load(file) or {}
    if not isinstance(payload, dict):
        raise ValueError(f"YAML payload must be an object: {path}")
    return payload


def _json_ready(value: Any) -> Any:
    if isinstance(value, Path):
        return value.as_posix()
    if isinstance(value, Mapping):
        return {str(key): _json_ready(item) for key, item in value.items()}
    if isinstance(value, Sequence) and not isinstance(value, str | bytes | bytearray):
        return [_json_ready(item) for item in value]
    return value


def _write_json(path: Path, payload: Mapping[str, Any]) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(_json_ready(dict(payload)), ensure_ascii=False, indent=2, sort_keys=True),
        encoding="utf-8",
    )
    return path


def _write_yaml(path: Path, payload: Mapping[str, Any]) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        yaml.safe_dump(dict(payload), sort_keys=False, allow_unicode=True),
        encoding="utf-8",
    )
    return path


def _write_csv(path: Path, rows: Sequence[Mapping[str, Any]]) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    fieldnames: list[str] = []
    for row in rows:
        for key in row:
            if key not in fieldnames:
                fieldnames.append(key)
    with path.open("w", encoding="utf-8", newline="") as file:
        writer = csv.DictWriter(file, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)
    return path


def _trial_artifacts_from_metrics_path(sweep_dir: Path, metrics_path: Path) -> TrialArtifacts | None:
    try:
        relative_parts = metrics_path.relative_to(sweep_dir).parts
    except ValueError:
        return None
    if TRIALS_DIR_NAME not in relative_parts:
        return None
    trials_index = relative_parts.index(TRIALS_DIR_NAME)
    if len(relative_parts) <= trials_index + 2:
        return None
    trial_id = relative_parts[trials_index + 1]
    run_dir = metrics_path.parent
    stage = None
    if run_dir.name.startswith("stage_"):
        try:
            stage = int(run_dir.name.removeprefix("stage_"))
        except ValueError:
            stage = None
    config_path = run_dir / "config.yaml"
    manifest_path = run_dir / "manifest.json"
    return TrialArtifacts(
        trial_id=trial_id,
        stage=stage,
        run_dir=run_dir,
        metrics_path=metrics_path,
        config_path=config_path if config_path.exists() else None,
        manifest_path=manifest_path if manifest_path.exists() else None,
    )


def discover_trial_artifacts(sweep_dir: Path) -> list[TrialArtifacts]:
    artifacts: list[TrialArtifacts] = []
    for metrics_path in sorted((sweep_dir / TRIALS_DIR_NAME).rglob("metrics.json")):
        item = _trial_artifacts_from_metrics_path(sweep_dir, metrics_path)
        if item is not None:
            artifacts.append(item)
    return artifacts


def _objective_config(search_space: Mapping[str, Any]) -> Mapping[str, Any]:
    objective = search_space.get("objective", {})
    return objective if isinstance(objective, Mapping) else {}


def _metric_value(metrics: Mapping[str, Any], name: str, *, default: float = 0.0) -> float:
    value = metrics.get(name)
    if value is None or isinstance(value, bool):
        return default
    return float(value)


def _clamp01(value: float) -> float:
    return max(0.0, min(1.0, float(value)))


def _constraint_satisfied(row: Mapping[str, Any], constraint_name: str, expected: Any) -> bool:
    numeric_expected = float(expected)
    if constraint_name.startswith("min_"):
        return _metric_value(row, constraint_name.removeprefix("min_")) >= numeric_expected
    if constraint_name.startswith("max_"):
        return _metric_value(row, constraint_name.removeprefix("max_")) <= numeric_expected
    raise ValueError(f"Unsupported objective constraint: {constraint_name}")


def _geometric_mean(values: Sequence[float]) -> float:
    if not values or any(value <= 0.0 for value in values):
        return 0.0
    return math.exp(sum(math.log(value) for value in values) / len(values))


def _objective_fields(row: Mapping[str, Any], search_space: Mapping[str, Any]) -> tuple[bool, float, str]:
    objective = _objective_config(search_space)
    primary_metric = str(objective.get("primary_metric", DEFAULT_PRIMARY_METRIC))
    supporting_metrics = objective.get("supporting_metrics")
    if not isinstance(supporting_metrics, list):
        supporting_metrics = objective.get("tie_breakers")
    if not isinstance(supporting_metrics, list):
        supporting_metrics = DEFAULT_SUPPORTING_METRICS
    penalty_metrics = objective.get("penalty_metrics")
    if not isinstance(penalty_metrics, list):
        penalty_metrics = DEFAULT_PENALTY_METRICS
    constraints = objective.get("constraints", {})
    if not isinstance(constraints, Mapping):
        constraints = {}
    if any(not _constraint_satisfied(row, str(name), expected) for name, expected in constraints.items()):
        return False, 0.0, primary_metric
    primary = _clamp01(_metric_value(row, primary_metric))
    components = [primary]
    components.extend(_clamp01(_metric_value(row, str(name))) for name in supporting_metrics)
    components.extend(1.0 - _clamp01(_metric_value(row, str(name))) for name in penalty_metrics)
    return True, round(primary * _geometric_mean(components), 12), primary_metric


def _objective_sort_key(row: Mapping[str, Any]) -> tuple[float, float, float, str]:
    primary_metric = str(row.get("objective_primary_metric") or DEFAULT_PRIMARY_METRIC)
    return (
        float(row.get("objective_score") or 0.0),
        _metric_value(row, primary_metric),
        _metric_value(row, "ndcg_at_k"),
        str(row.get("trial_id", "")),
    )


def build_recovered_rows(sweep_dir: Path, search_space: Mapping[str, Any]) -> tuple[list[dict[str, Any]], dict[str, TrialArtifacts]]:
    rows: list[dict[str, Any]] = []
    artifacts_by_key: dict[str, TrialArtifacts] = {}
    for artifact in discover_trial_artifacts(sweep_dir):
        metrics = _load_json(artifact.metrics_path)
        row: dict[str, Any] = {
            "trial_id": artifact.trial_id,
            "run_dir": artifact.run_dir.relative_to(sweep_dir).as_posix(),
        }
        if artifact.stage is not None:
            row["stage"] = artifact.stage
        row.update(metrics)
        eligible, score, primary_metric = _objective_fields(row, search_space)
        row["objective_score"] = score
        row["objective_eligible"] = eligible
        row["objective_rank"] = None
        row["objective_primary_metric"] = primary_metric
        rows.append(row)
        artifacts_by_key[_row_key(row)] = artifact

    eligible_rows = sorted((row for row in rows if row["objective_eligible"]), key=_objective_sort_key, reverse=True)
    for rank, row in enumerate(eligible_rows, start=1):
        row["objective_rank"] = rank
    return rows, artifacts_by_key


def _row_key(row: Mapping[str, Any]) -> str:
    if row.get("stage") is None:
        return str(row["trial_id"])
    return f"{row['trial_id']}::stage_{row['stage']}"


def _preferred_stage(rows: Sequence[Mapping[str, Any]]) -> int | None:
    stages = [int(row["stage"]) for row in rows if row.get("stage") is not None]
    return max(stages) if stages else None


def _best_row(rows: Sequence[Mapping[str, Any]]) -> Mapping[str, Any]:
    if not rows:
        raise ValueError("No completed trial metrics were found")
    preferred_stage = _preferred_stage(rows)
    candidates = [row for row in rows if row.get("stage") == preferred_stage] if preferred_stage is not None else list(rows)
    eligible = [row for row in candidates if row.get("objective_eligible")]
    return max(eligible or candidates, key=_objective_sort_key)


def cleanup_trial_artifacts(sweep_dir: Path) -> list[str]:
    removed: list[str] = []
    for trial_dir in sorted((sweep_dir / TRIALS_DIR_NAME).rglob("*")):
        if not trial_dir.is_dir():
            continue
        should_remove = False
        if trial_dir.name in {"artifacts", "recommendations"}:
            should_remove = True
        elif trial_dir.name == "debug" and trial_dir.parent.name == "evaluation":
            should_remove = True
        if should_remove:
            removed.append(trial_dir.relative_to(sweep_dir).as_posix())
            shutil.rmtree(trial_dir, ignore_errors=True)
    return removed


def _write_recovery_outputs(
    *,
    sweep_dir: Path,
    rows: Sequence[Mapping[str, Any]],
    best_row: Mapping[str, Any],
    best_artifact: TrialArtifacts,
    canonical: bool,
    overwrite: bool,
) -> None:
    prefix = "" if canonical else "recovered_"
    outputs = {
        f"{prefix}results.csv": lambda path: _write_csv(path, rows),
        f"{prefix}best_metrics.json": lambda path: _write_json(path, dict(best_row)),
        f"{prefix}summary.json": lambda path: _write_json(
            path,
            {
                "created_at": datetime.now(UTC).isoformat(),
                "source": "recover_tuning_sweep.py",
                "trials_run": len(rows),
                "best_trial_id": best_row["trial_id"],
                "best_stage": best_row.get("stage"),
                "results_path": f"{prefix}results.csv",
                "best_metrics_path": f"{prefix}best_metrics.json",
                "best_config_path": f"{prefix}best_config.yaml" if best_artifact.config_path else None,
            },
        ),
    }
    if best_artifact.config_path is not None:
        outputs[f"{prefix}best_config.yaml"] = lambda path: _write_yaml(path, _load_yaml(best_artifact.config_path))

    for filename, writer in outputs.items():
        path = sweep_dir / filename
        if path.exists() and not overwrite:
            continue
        writer(path)


def recover_sweep(
    sweep_dir: Path,
    *,
    canonical: bool,
    overwrite: bool,
    cleanup: bool,
) -> None:
    sweep_dir = sweep_dir.resolve()
    search_space = _load_yaml(sweep_dir / "search_space.yaml")
    rows, artifacts_by_key = build_recovered_rows(sweep_dir, search_space)
    best = _best_row(rows)
    best_artifact = artifacts_by_key[_row_key(best)]
    _write_recovery_outputs(
        sweep_dir=sweep_dir,
        rows=rows,
        best_row=best,
        best_artifact=best_artifact,
        canonical=canonical,
        overwrite=overwrite,
    )
    removed = cleanup_trial_artifacts(sweep_dir) if cleanup else []
    print(f"Recovered {len(rows)} completed trial rows from {sweep_dir}")
    print(f"Best trial: {best.get('trial_id')} stage={best.get('stage')} objective_score={best.get('objective_score')}")
    if cleanup:
        print(f"Removed {len(removed)} heavy trial directories")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Recover a tuning sweep and prune heavy trial artifacts.")
    parser.add_argument("sweep_dir", type=Path)
    parser.add_argument(
        "--canonical",
        action="store_true",
        help="Write results.csv, best_config.yaml, best_metrics.json, and summary.json instead of recovered_* files.",
    )
    parser.add_argument(
        "--overwrite",
        action="store_true",
        help="Overwrite existing output files. By default existing files are preserved.",
    )
    parser.add_argument(
        "--cleanup-trial-artifacts",
        action="store_true",
        help="Delete per-trial artifacts, recommendations, and evaluation/debug directories after recovery.",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    recover_sweep(
        args.sweep_dir,
        canonical=args.canonical,
        overwrite=args.overwrite,
        cleanup=args.cleanup_trial_artifacts,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
