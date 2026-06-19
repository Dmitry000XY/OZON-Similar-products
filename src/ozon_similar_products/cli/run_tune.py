"""CLI entrypoint for parameter tuning over an explicit search space."""

from __future__ import annotations

import argparse
import csv
import random
import shutil
from collections.abc import Iterable, Mapping, Sequence
from copy import deepcopy
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any

import yaml

from ozon_similar_products.cli.run_full import execute_full_run, validation_window
from ozon_similar_products.config import PROJECT_ROOT, load_yaml_config
from ozon_similar_products.evaluation import metrics_to_flat_dict, write_json


def _parse_iso_date(value: str) -> date:
    try:
        return date.fromisoformat(value)
    except ValueError as error:
        raise argparse.ArgumentTypeError(
            "date must be an ISO date string: YYYY-MM-DD"
        ) from error


def _resolve_project_path(path: Path) -> Path:
    if path.is_absolute():
        return path
    return (PROJECT_ROOT / path).resolve()


def _safe_sweep_id(name: str | None = None) -> str:
    timestamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    if not name:
        return f"sweep_{timestamp}"
    normalized = "".join(
        character if character.isalnum() or character in {"-", "_"} else "_"
        for character in name.strip()
    ).strip("_")
    return f"{timestamp}_{normalized}" if normalized else f"sweep_{timestamp}"


def set_by_dot_path(config: Mapping[str, Any], dot_path: str, value: Any) -> dict[str, Any]:
    """Return a deep config copy with one dot-path override applied."""
    if not dot_path:
        raise ValueError("dot_path must be non-empty")

    overridden = deepcopy(dict(config))
    cursor: dict[str, Any] = overridden
    parts = dot_path.split(".")
    for part in parts[:-1]:
        current = cursor.get(part)
        if current is None:
            current = {}
        if not isinstance(current, Mapping):
            raise TypeError(f"Cannot set {dot_path}: {part} is not a mapping")
        current_copy = dict(current)
        cursor[part] = current_copy
        cursor = current_copy
    cursor[parts[-1]] = value
    return overridden


def apply_overrides(config: Mapping[str, Any], overrides: Mapping[str, Any]) -> dict[str, Any]:
    """Apply a set of dot-path overrides to a config copy."""
    result = deepcopy(dict(config))
    for dot_path, value in overrides.items():
        result = set_by_dot_path(result, dot_path, value)
    return result


def _parameter_values(parameter_name: str, spec: Mapping[str, Any]) -> list[Any]:
    parameter_type = spec.get("type", "choice")
    if parameter_type != "choice":
        raise ValueError(f"Unsupported search-space type for {parameter_name}: {parameter_type}")
    values = spec.get("values")
    if not isinstance(values, list) or not values:
        raise ValueError(f"Search-space parameter {parameter_name} must define non-empty values")
    return values


def generate_grid_trials(search_space: Mapping[str, Any]) -> list[dict[str, Any]]:
    """Generate all grid combinations from search_space.yaml parameters."""
    parameters = search_space.get("parameters")
    if not isinstance(parameters, Mapping) or not parameters:
        raise ValueError("search_space.yaml must contain non-empty parameters")

    names = list(parameters.keys())
    value_lists = [
        _parameter_values(str(name), spec if isinstance(spec, Mapping) else {})
        for name, spec in parameters.items()
    ]

    trials: list[dict[str, Any]] = [{}]
    for name, values in zip(names, value_lists, strict=True):
        trials = [
            {**trial, str(name): value}
            for trial in trials
            for value in values
        ]
    return trials


def select_trials(
    search_space: Mapping[str, Any],
    *,
    strategy: str,
    max_trials: int,
    seed: int = 42,
) -> list[dict[str, Any]]:
    """Select trial overrides for grid/random/successive_halving strategies."""
    if max_trials <= 0:
        raise ValueError("max_trials must be a positive integer")

    all_trials = generate_grid_trials(search_space)
    if strategy == "grid":
        return all_trials[:max_trials]
    if strategy == "random":
        rng = random.Random(seed)
        shuffled = list(all_trials)
        rng.shuffle(shuffled)
        return shuffled[:max_trials]
    if strategy == "successive_halving":
        raise NotImplementedError("successive_halving tuning is not implemented yet")
    raise ValueError(f"Unsupported tuning strategy: {strategy}")


def _metric_value(metrics: Mapping[str, Any], name: str) -> float:
    value = metrics.get(name)
    if value is None:
        return float("-inf")
    if isinstance(value, bool):
        return float("-inf")
    return float(value)


def best_trial_row(
    rows: Iterable[Mapping[str, Any]],
    search_space: Mapping[str, Any],
) -> Mapping[str, Any]:
    """Select the best trial by objective metric and tie breakers."""
    rows = list(rows)
    if not rows:
        raise ValueError("Cannot select best trial from an empty result set")

    objective = search_space.get("objective", {})
    if not isinstance(objective, Mapping):
        objective = {}
    primary_metric = str(objective.get("primary_metric", "to_cart_hit_rate_at_k"))
    tie_breakers = objective.get(
        "tie_breakers",
        ["ndcg_at_k", "weighted_recall_at_k", "coverage_at_k"],
    )
    if not isinstance(tie_breakers, list):
        tie_breakers = []

    def sort_key(row: Mapping[str, Any]) -> tuple[float, ...]:
        metric_names = [primary_metric, *(str(name) for name in tie_breakers)]
        penalty = _metric_value(row, "popularity_bias_at_k")
        return (*(_metric_value(row, name) for name in metric_names), -penalty)

    return max(rows, key=sort_key)


def _write_yaml(path: Path, payload: Mapping[str, Any]) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        yaml.safe_dump(dict(payload), sort_keys=False, allow_unicode=True),
        encoding="utf-8",
    )
    return path


def _write_results_csv(path: Path, rows: Sequence[Mapping[str, Any]]) -> Path:
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


def _strip_trial_recommendation_parquets(trial_dir: Path) -> None:
    recommendations_dir = trial_dir / "recommendations"
    if recommendations_dir.exists():
        shutil.rmtree(recommendations_dir)


def run_tuning(
    *,
    train_until_date: date,
    lookback_days: int,
    validation_days: int,
    top_k: int | None,
    config_path: Path,
    search_space_path: Path,
    max_trials: int,
    tuning_strategy: str,
    output_dir: Path = Path("outputs/tuning"),
    sweep_name: str | None = None,
) -> Path:
    """Run a tuning sweep and return its output directory."""
    validation_window(train_until_date, validation_days)

    base_config = load_yaml_config(config_path)
    search_space = load_yaml_config(search_space_path)
    trial_overrides = select_trials(
        search_space,
        strategy=tuning_strategy,
        max_trials=max_trials,
    )

    sweep_id = _safe_sweep_id(sweep_name)
    sweep_dir = _resolve_project_path(output_dir) / sweep_id
    sweep_dir.mkdir(parents=True, exist_ok=False)

    _write_yaml(sweep_dir / "base_config.yaml", base_config)
    _write_yaml(sweep_dir / "search_space.yaml", search_space)

    rows: list[dict[str, Any]] = []
    trial_configs: dict[str, dict[str, Any]] = {}
    for index, overrides in enumerate(trial_overrides, start=1):
        trial_id = f"trial_{index:04d}"
        trial_dir = sweep_dir / "trials" / trial_id
        trial_config = apply_overrides(base_config, overrides)
        trial_configs[trial_id] = trial_config

        trial_config_path = _write_yaml(trial_dir / "config.yaml", trial_config)
        result = execute_full_run(
            train_until_date=train_until_date,
            lookback_days=lookback_days,
            validation_days=validation_days,
            top_k=top_k,
            config_path=trial_config_path,
            run_id=trial_id,
            run_dir=trial_dir,
            keep_evaluation_artifacts=False,
            publish_latest=False,
        )
        metrics = metrics_to_flat_dict(result.metrics)
        shutil.copy2(result.metrics_path, trial_dir / "metrics.json")
        shutil.copy2(result.manifest_path, trial_dir / "manifest.json")
        _strip_trial_recommendation_parquets(trial_dir)

        rows.append(
            {
                "trial_id": trial_id,
                "run_dir": trial_dir.as_posix(),
                **overrides,
                **metrics,
            }
        )

    _write_results_csv(sweep_dir / "results.csv", rows)
    best_row = best_trial_row(rows, search_space)
    best_trial_id = str(best_row["trial_id"])
    best_config = trial_configs[best_trial_id]
    best_metrics = {
        key: value
        for key, value in best_row.items()
        if key not in {"trial_id", "run_dir"} and key not in trial_overrides[0]
    }

    _write_yaml(sweep_dir / "best_config.yaml", best_config)
    write_json(sweep_dir / "best_metrics.json", best_metrics)
    write_json(
        sweep_dir / "summary.json",
        {
            "sweep_id": sweep_id,
            "created_at": datetime.now(UTC),
            "strategy": tuning_strategy,
            "max_trials": max_trials,
            "trials_run": len(rows),
            "best_trial_id": best_trial_id,
            "best_metrics_path": "best_metrics.json",
            "best_config_path": "best_config.yaml",
            "results_path": "results.csv",
            "train_until_date": train_until_date.isoformat(),
            "lookback_days": lookback_days,
            "validation_days": validation_days,
            "top_k": top_k,
        },
    )
    return sweep_dir


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Tune recommendation parameters.")
    parser.add_argument("train_until_date", type=_parse_iso_date)
    parser.add_argument("--lookback-days", type=int, default=30)
    parser.add_argument("--validation-days", type=int, default=1)
    parser.add_argument("--top-k", type=int, default=None)
    parser.add_argument("--config-path", type=Path, default=Path("configs/production.yaml"))
    parser.add_argument(
        "--search-space-path",
        type=Path,
        default=Path("configs/tuning/search_space.yaml"),
    )
    parser.add_argument("--max-trials", type=int, default=30)
    parser.add_argument(
        "--tuning-strategy",
        choices=["grid", "random", "successive_halving"],
        default="random",
    )
    parser.add_argument("--output-dir", type=Path, default=Path("outputs/tuning"))
    parser.add_argument("--sweep-name", default=None)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    try:
        run_tuning(
            train_until_date=args.train_until_date,
            lookback_days=args.lookback_days,
            validation_days=args.validation_days,
            top_k=args.top_k,
            config_path=args.config_path,
            search_space_path=args.search_space_path,
            max_trials=args.max_trials,
            tuning_strategy=args.tuning_strategy,
            output_dir=args.output_dir,
            sweep_name=args.sweep_name,
        )
    except Exception as error:
        print(f"[run_tune] failed: {error}")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
