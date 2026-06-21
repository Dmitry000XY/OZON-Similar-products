"""CLI entrypoint for parameter tuning over an explicit search space."""
from __future__ import annotations

import argparse
import csv
import logging
import math
import random
import shutil
import time
from collections.abc import Iterable, Iterator, Mapping, Sequence
from copy import deepcopy
from datetime import UTC, date, datetime
from decimal import Decimal
from itertools import islice, product
from pathlib import Path
from typing import Any

import yaml

from ozon_similar_products.cli.run_full import execute_full_run, validation_window
from ozon_similar_products.cli.scoring_only_tuning import (
    FastScoringContext,
    build_fast_scoring_context,
    execute_scoring_only_trial,
    validate_scoring_only_search_space,
)
from ozon_similar_products.config import PROJECT_ROOT, load_yaml_config
from ozon_similar_products.evaluation import metrics_to_flat_dict, write_json


def _parse_iso_date(value: str) -> date:
    try:
        return date.fromisoformat(value)
    except ValueError as error:
        raise argparse.ArgumentTypeError("date must be an ISO date string: YYYY-MM-DD") from error


def _resolve_project_path(path: Path) -> Path:
    return path if path.is_absolute() else (PROJECT_ROOT / path).resolve()


def _timestamp_slug() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%d_%H-%M-%SZ")


def _safe_label(value: str | None) -> str:
    if not value:
        return ""
    return "".join(
        character if character.isalnum() or character in {"-", "_"} else "_"
        for character in value.strip()
    ).strip("_")


def _safe_sweep_id(
    name: str | None = None,
    *,
    strategy: str | None = None,
    train_until_date: date | None = None,
    lookback_days: int | None = None,
    validation_days: int | None = None,
    top_k: int | None = None,
) -> str:
    parts = ["sweep", _timestamp_slug()]
    strategy_label = _safe_label(strategy)
    if strategy_label:
        parts.append(strategy_label)
    if train_until_date is not None:
        parts.append(f"train-{train_until_date.isoformat()}")
    if lookback_days is not None:
        parts.append(f"lookback-{lookback_days}d")
    if validation_days is not None:
        parts.append(f"validation-{validation_days}d")
    if top_k is not None:
        parts.append(f"top-{top_k}")
    name_label = _safe_label(name)
    if name_label:
        parts.append(name_label)
    return "_".join(parts)


def set_by_dot_path(config: Mapping[str, Any], dot_path: str, value: Any) -> dict[str, Any]:
    if not dot_path:
        raise ValueError("dot_path must be non-empty")
    result = deepcopy(dict(config))
    cursor: dict[str, Any] = result
    parts = dot_path.split(".")
    for part in parts[:-1]:
        current = cursor.get(part)
        if current is None:
            current = {}
        if not isinstance(current, Mapping):
            raise TypeError(f"Cannot set {dot_path}: {part} is not a mapping")
        cursor[part] = dict(current)
        cursor = cursor[part]
    cursor[parts[-1]] = value
    return result


def apply_overrides(config: Mapping[str, Any], overrides: Mapping[str, Any]) -> dict[str, Any]:
    result = deepcopy(dict(config))
    for dot_path, value in overrides.items():
        result = set_by_dot_path(result, dot_path, value)
    return result


def _new_rng() -> random.Random:
    return random.SystemRandom()


def _normalize_number(value: float) -> float:
    return round(float(value), 12)


def _as_positive_int(value: Any, parameter_name: str) -> int:
    if isinstance(value, bool):
        raise ValueError(f"{parameter_name} must be a positive integer")
    parsed = int(value)
    if parsed <= 0:
        raise ValueError(f"{parameter_name} must be a positive integer")
    return parsed


def _as_non_negative_float(value: Any, parameter_name: str) -> float:
    if isinstance(value, bool):
        raise ValueError(f"{parameter_name} must be a non-negative number")
    parsed = float(value)
    if parsed < 0.0:
        raise ValueError(f"{parameter_name} must be a non-negative number")
    return parsed


def _decimal_places(value: float) -> int:
    text = format(value, "f").rstrip("0").rstrip(".")
    return 0 if "." not in text else len(text.split(".", maxsplit=1)[1])


def _float_range_values(parameter_name: str, spec: Mapping[str, Any]) -> list[float]:
    minimum, maximum = float(spec["min"]), float(spec["max"])
    if minimum > maximum:
        raise ValueError(f"{parameter_name}: min must be <= max")
    if spec.get("step") is None:
        raise ValueError(f"{parameter_name}: float_range requires step for grid expansion")
    step = _as_non_negative_float(spec["step"], f"{parameter_name}.step")
    if step <= 0.0:
        raise ValueError(f"{parameter_name}.step must be > 0")
    current = Decimal(str(minimum))
    maximum_decimal = Decimal(str(maximum))
    step_decimal = Decimal(str(step))
    digits = _decimal_places(step)
    values: list[float] = []
    while current <= maximum_decimal:
        values.append(round(float(current), digits))
        current += step_decimal
    if not values:
        raise ValueError(f"{parameter_name}: float_range expands to no values")
    return values


def _log_float_range_values(parameter_name: str, spec: Mapping[str, Any]) -> list[float]:
    minimum, maximum = float(spec["min"]), float(spec["max"])
    if minimum <= 0.0 or maximum <= 0.0:
        raise ValueError(f"{parameter_name}: log_float_range bounds must be > 0")
    if minimum > maximum:
        raise ValueError(f"{parameter_name}: min must be <= max")
    count = _as_positive_int(spec.get("num", 3), f"{parameter_name}.num")
    if count == 1:
        return [_normalize_number(minimum)]
    log_min, log_max = math.log(minimum), math.log(maximum)
    step = (log_max - log_min) / float(count - 1)
    return [_normalize_number(math.exp(log_min + index * step)) for index in range(count)]


def _parameter_values(parameter_name: str, spec: Mapping[str, Any]) -> list[Any]:
    parameter_type = str(spec.get("type", "choice"))
    if parameter_type == "choice":
        values = spec.get("values")
        if not isinstance(values, list) or not values:
            raise ValueError(f"Search-space parameter {parameter_name} must define non-empty values")
        return values
    if parameter_type == "int_range":
        minimum, maximum = int(spec["min"]), int(spec["max"])
        step = _as_positive_int(spec.get("step", 1), f"{parameter_name}.step")
        if minimum > maximum:
            raise ValueError(f"{parameter_name}: min must be <= max")
        return list(range(minimum, maximum + 1, step))
    if parameter_type == "float_range":
        return _float_range_values(parameter_name, spec)
    if parameter_type == "log_float_range":
        return _log_float_range_values(parameter_name, spec)
    raise ValueError(f"Unsupported search-space type for {parameter_name}: {parameter_type}")


def _sample_parameter_value(
    parameter_name: str,
    spec: Mapping[str, Any],
    *,
    rng: random.Random,
    discrete_only: bool = False,
) -> Any:
    parameter_type = str(spec.get("type", "choice"))
    if parameter_type in {"choice", "int_range"}:
        return rng.choice(_parameter_values(parameter_name, spec))
    if parameter_type == "float_range":
        if discrete_only or spec.get("step") is not None:
            return rng.choice(_parameter_values(parameter_name, spec))
        return _normalize_number(rng.uniform(float(spec["min"]), float(spec["max"])))
    if parameter_type == "log_float_range":
        if discrete_only:
            return rng.choice(_parameter_values(parameter_name, spec))
        minimum, maximum = float(spec["min"]), float(spec["max"])
        if minimum <= 0.0 or maximum <= 0.0:
            raise ValueError(f"{parameter_name}: log_float_range bounds must be > 0")
        return _normalize_number(math.exp(rng.uniform(math.log(minimum), math.log(maximum))))
    raise ValueError(f"Unsupported search-space type for {parameter_name}: {parameter_type}")


def _sample_trial(
    search_space: Mapping[str, Any],
    *,
    rng: random.Random,
    discrete_only: bool = False,
) -> dict[str, Any]:
    parameters = search_space.get("parameters")
    if not isinstance(parameters, Mapping) or not parameters:
        raise ValueError("search_space.yaml must contain non-empty parameters")
    return {
        str(name): _sample_parameter_value(
            str(name),
            spec if isinstance(spec, Mapping) else {},
            rng=rng,
            discrete_only=discrete_only,
        )
        for name, spec in parameters.items()
    }


def _parameter_is_mutable(parameter_name: str, spec: Mapping[str, Any]) -> bool:
    return len(_parameter_values(parameter_name, spec)) > 1


def _mutate_parameter_value(
    parameter_name: str,
    spec: Mapping[str, Any],
    current_value: Any,
    *,
    rng: random.Random,
) -> Any:
    values = _parameter_values(parameter_name, spec)
    if len(values) <= 1:
        return current_value
    if str(spec.get("type", "choice")) == "choice":
        alternatives = [value for value in values if value != current_value]
        return rng.choice(alternatives) if alternatives else current_value
    if current_value not in values:
        return min(values, key=lambda value: abs(float(value) - float(current_value)))
    index = values.index(current_value)
    neighbors = []
    if index > 0:
        neighbors.append(values[index - 1])
    if index < len(values) - 1:
        neighbors.append(values[index + 1])
    return rng.choice(neighbors) if neighbors else current_value


def _mutate_trial(
    current_trial: Mapping[str, Any],
    search_space: Mapping[str, Any],
    *,
    mutations: int,
    rng: random.Random,
) -> dict[str, Any]:
    parameters = search_space.get("parameters")
    if not isinstance(parameters, Mapping) or not parameters:
        raise ValueError("search_space.yaml must contain non-empty parameters")
    mutable_parameters = [
        str(name)
        for name, spec in parameters.items()
        if isinstance(spec, Mapping) and _parameter_is_mutable(str(name), spec)
    ]
    if not mutable_parameters:
        return dict(current_trial)
    updated = dict(current_trial)
    for parameter_name in rng.sample(mutable_parameters, k=min(max(1, mutations), len(mutable_parameters))):
        spec = parameters[parameter_name]
        if isinstance(spec, Mapping):
            updated[parameter_name] = _mutate_parameter_value(
                parameter_name,
                spec,
                updated.get(parameter_name),
                rng=rng,
            )
    return updated


def _iter_grid_trials(search_space: Mapping[str, Any]) -> Iterator[dict[str, Any]]:
    parameters = search_space.get("parameters")
    if not isinstance(parameters, Mapping) or not parameters:
        raise ValueError("search_space.yaml must contain non-empty parameters")
    names = [str(name) for name in parameters]
    value_lists = [
        _parameter_values(str(name), spec if isinstance(spec, Mapping) else {})
        for name, spec in parameters.items()
    ]
    for values in product(*value_lists):
        yield dict(zip(names, values, strict=True))


def generate_grid_trials(search_space: Mapping[str, Any]) -> list[dict[str, Any]]:
    return list(_iter_grid_trials(search_space))


def _select_sampled_trials(search_space: Mapping[str, Any], *, max_trials: int) -> list[dict[str, Any]]:
    rng = _new_rng()
    return [_sample_trial(search_space, rng=rng) for _ in range(max_trials)]


def select_trials(search_space: Mapping[str, Any], *, strategy: str, max_trials: int) -> list[dict[str, Any]]:
    if max_trials <= 0:
        raise ValueError("max_trials must be a positive integer")
    if strategy == "grid":
        return list(islice(_iter_grid_trials(search_space), max_trials))
    if strategy in {"random", "successive_halving"}:
        return _select_sampled_trials(search_space, max_trials=max_trials)
    raise ValueError(f"Unsupported tuning strategy: {strategy}")


def _objective_config(search_space: Mapping[str, Any]) -> Mapping[str, Any]:
    objective = search_space.get("objective", {})
    return objective if isinstance(objective, Mapping) else {}


def _primary_metric_name(search_space: Mapping[str, Any]) -> str:
    return str(_objective_config(search_space).get("primary_metric", "to_cart_hit_rate_at_k"))


def _supporting_metric_names(search_space: Mapping[str, Any]) -> list[str]:
    objective = _objective_config(search_space)
    if isinstance(objective.get("supporting_metrics"), list):
        return [str(name) for name in objective["supporting_metrics"]]
    if isinstance(objective.get("tie_breakers"), list):
        return [str(name) for name in objective["tie_breakers"]]
    return [
        "strong_ndcg_at_k",
        "strong_recall_at_k",
        "strong_mrr_at_k",
        "coverage_at_k",
        "to_cart_recall_at_k",
    ]


def _penalty_metric_names(search_space: Mapping[str, Any]) -> list[str]:
    penalty_metrics = _objective_config(search_space).get("penalty_metrics")
    return [str(name) for name in penalty_metrics] if isinstance(penalty_metrics, list) else ["popularity_bias_at_k"]


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


def _objective_fields(row: Mapping[str, Any], search_space: Mapping[str, Any]) -> tuple[bool, float]:
    objective = _objective_config(search_space)
    constraints = objective.get("constraints", {})
    if not isinstance(constraints, Mapping):
        constraints = {}
    if any(not _constraint_satisfied(row, str(name), expected) for name, expected in constraints.items()):
        return False, 0.0
    primary = _clamp01(_metric_value(row, _primary_metric_name(search_space)))
    components = [primary]
    components.extend(_clamp01(_metric_value(row, name)) for name in _supporting_metric_names(search_space))
    components.extend(1.0 - _clamp01(_metric_value(row, name)) for name in _penalty_metric_names(search_space))
    return True, primary * _geometric_mean(components)


def _objective_sort_key(row: Mapping[str, Any], search_space: Mapping[str, Any]) -> tuple[float, float, float, str]:
    return (
        float(row.get("objective_score") or 0.0),
        _metric_value(row, _primary_metric_name(search_space)),
        _metric_value(row, "strong_ndcg_at_k", default=_metric_value(row, "ndcg_at_k")),
        str(row.get("trial_id", "")),
    )


def _annotate_objective_rows(rows: Iterable[Mapping[str, Any]], search_space: Mapping[str, Any]) -> list[dict[str, Any]]:
    annotated = [dict(row) for row in rows]
    primary_metric = _primary_metric_name(search_space)
    for row in annotated:
        eligible, score = _objective_fields(row, search_space)
        row["objective_score"] = _normalize_number(score)
        row["objective_eligible"] = eligible
        row["objective_rank"] = None
        row["objective_primary_metric"] = primary_metric
    eligible = sorted((row for row in annotated if row["objective_eligible"]), key=lambda row: _objective_sort_key(row, search_space), reverse=True)
    for rank, row in enumerate(eligible, start=1):
        row["objective_rank"] = rank
    return annotated


def best_trial_row(rows: Iterable[Mapping[str, Any]], search_space: Mapping[str, Any], *, preferred_stage: int | None = None) -> Mapping[str, Any]:
    annotated = _annotate_objective_rows(rows, search_space)
    if not annotated:
        raise ValueError("Cannot select best trial from an empty result set")
    candidates = annotated
    if preferred_stage is not None:
        stage_rows = [row for row in annotated if row.get("stage") == preferred_stage]
        if stage_rows:
            candidates = stage_rows
    eligible = [row for row in candidates if row.get("objective_eligible")]
    return max(eligible or candidates, key=lambda row: _objective_sort_key(row, search_space))


def _copy_if_different(source: Path, destination: Path) -> Path:
    if source.resolve() == destination.resolve():
        return destination
    destination.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(source, destination)
    return destination


def _grid_trial_count(search_space: Mapping[str, Any]) -> int | None:
    parameters = search_space.get("parameters")
    if not isinstance(parameters, Mapping) or not parameters:
        return None
    total = 1
    try:
        for name, spec in parameters.items():
            if not isinstance(spec, Mapping):
                return None
            total *= len(_parameter_values(str(name), spec))
    except (KeyError, TypeError, ValueError):
        return None
    return total


def _effective_trial_lookback_days(overrides: Mapping[str, Any], default_lookback_days: int) -> int:
    return _as_positive_int(overrides.get("pipeline.lookback_days", default_lookback_days), "lookback_days")


def _effective_trial_top_k(config: Mapping[str, Any], overrides: Mapping[str, Any], default_top_k: int | None) -> int | None:
    for key in ("topk.top_k", "pipeline.top_k"):
        if key in overrides:
            return _as_positive_int(overrides[key], key)
    if default_top_k is not None:
        return _as_positive_int(default_top_k, "top_k")
    for section_name in ("topk", "pipeline"):
        section = config.get(section_name)
        if isinstance(section, Mapping) and section.get("top_k") is not None:
            return _as_positive_int(section["top_k"], f"{section_name}.top_k")
    return None


def _with_trial_artifact_dirs(config: Mapping[str, Any], trial_dir: Path) -> dict[str, Any]:
    updated = deepcopy(dict(config))
    artifacts = dict(updated.get("artifacts", {})) if isinstance(updated.get("artifacts"), Mapping) else {}
    root = trial_dir / "artifacts"
    for key in (
        "events_clean_dir",
        "sessions_dir",
        "item_popularity_dir",
        "action_type_distribution_dir",
        "daily_pairs_dir",
        "pair_aggregates_dir",
    ):
        artifacts[key] = (root / key.removesuffix("_dir")).as_posix()
    updated["artifacts"] = artifacts
    return updated


def _write_yaml(path: Path, payload: Mapping[str, Any]) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(yaml.safe_dump(dict(payload), sort_keys=False, allow_unicode=True), encoding="utf-8")
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


def _strip_trial_scratch_artifacts(trial_dir: Path) -> list[Path]:
    """Remove heavyweight per-trial scratch outputs after metrics are persisted."""
    removed: list[Path] = []
    for relative_path in (
        Path("artifacts"),
        Path("recommendations"),
        Path("evaluation") / "debug",
    ):
        target = trial_dir / relative_path
        if target.exists():
            shutil.rmtree(target, ignore_errors=True)
            removed.append(target)
    return removed


def _trial_dir(sweep_dir: Path, trial_id: str, stage: int | None) -> Path:
    if stage is None:
        return sweep_dir / "trials" / trial_id
    return sweep_dir / "trials" / trial_id / f"stage_{stage}"


def _trial_run_id(trial_id: str, stage: int | None) -> str:
    return trial_id if stage is None else f"{trial_id}_stage{stage}"


def _run_trial(
    *,
    logger: logging.Logger,
    base_config: Mapping[str, Any],
    sweep_dir: Path,
    trial_id: str,
    overrides: Mapping[str, Any],
    strategy_columns: Mapping[str, Any],
    train_until_date: date,
    lookback_days: int,
    validation_days: int,
    default_top_k: int | None,
    fast_context: FastScoringContext | None = None,
) -> tuple[dict[str, Any], dict[str, Any]]:
    stage = strategy_columns.get("stage")
    stage_value = int(stage) if stage is not None else None
    trial_dir = _trial_dir(sweep_dir, trial_id, stage_value)
    started = time.perf_counter()
    trial_config = apply_overrides(base_config, overrides)
    trial_run_config = trial_config if fast_context is not None else _with_trial_artifact_dirs(trial_config, trial_dir)
    trial_top_k = _effective_trial_top_k(trial_config, overrides, default_top_k)
    logger.info(
        "[run_tune] trial_id=%s stage=%s overrides=%s resource_lookback_days=%s resource_validation_days=%s effective_top_k=%s output_dir=%s fast_scoring_only=%s",
        trial_id,
        stage_value,
        overrides,
        lookback_days,
        validation_days,
        trial_top_k,
        trial_dir,
        fast_context is not None,
    )
    trial_config_path = _write_yaml(trial_dir / "config.yaml", trial_run_config)
    if fast_context is None:
        result = execute_full_run(
            train_until_date=train_until_date,
            lookback_days=lookback_days,
            validation_days=validation_days,
            top_k=trial_top_k,
            config_path=trial_config_path,
            run_id=_trial_run_id(trial_id, stage_value),
            run_dir=trial_dir,
            keep_evaluation_artifacts=False,
            publish_latest=False,
        )
    else:
        result = execute_scoring_only_trial(
            context=fast_context,
            trial_config=trial_run_config,
            trial_config_path=trial_config_path,
            run_id=_trial_run_id(trial_id, stage_value),
            run_dir=trial_dir,
            top_k=trial_top_k,
        )
    metrics = metrics_to_flat_dict(result.metrics)
    _copy_if_different(result.metrics_path, trial_dir / "metrics.json")
    _copy_if_different(result.manifest_path, trial_dir / "manifest.json")
    removed_scratch_artifacts = _strip_trial_scratch_artifacts(trial_dir)
    if removed_scratch_artifacts:
        logger.info(
            "[run_tune] pruned_trial_scratch_artifacts trial_id=%s stage=%s removed=%s",
            trial_id,
            stage_value,
            [path.as_posix() for path in removed_scratch_artifacts],
        )
    row = {"trial_id": trial_id, "run_dir": trial_dir.as_posix(), **strategy_columns, **overrides, **metrics}
    if fast_context is not None:
        row["fast_scoring_only"] = True
    logger.info(
        "[run_tune] trial_finished trial_id=%s stage=%s elapsed_seconds=%.2f to_cart_hit_rate_at_k=%s ndcg_at_k=%s recall_at_k=%s coverage_at_k=%s",
        trial_id,
        stage_value,
        time.perf_counter() - started,
        metrics.get("to_cart_hit_rate_at_k"),
        metrics.get("ndcg_at_k"),
        metrics.get("recall_at_k"),
        metrics.get("coverage_at_k"),
    )
    return row, trial_config


def _current_best_log(rows: Sequence[Mapping[str, Any]], search_space: Mapping[str, Any], *, preferred_stage: int | None = None) -> tuple[str | None, float | None]:
    if not rows:
        return None, None
    best = best_trial_row(rows, search_space, preferred_stage=preferred_stage)
    return str(best.get("trial_id")), _metric_value(best, _primary_metric_name(search_space), default=float("-inf"))


def _annealing_temperature(index: int, total_trials: int, *, start: float, end: float) -> float:
    if total_trials <= 1:
        return end
    return start * ((end / start) ** (float(index) / float(total_trials - 1)))


def _preferred_checkpoint_stage(rows: Sequence[Mapping[str, Any]]) -> int | None:
    stages = [
        int(row["stage"])
        for row in rows
        if row.get("stage") is not None
    ]
    return max(stages) if stages else None


def _write_tuning_checkpoint(
    *,
    sweep_dir: Path,
    rows: Sequence[Mapping[str, Any]],
    search_space: Mapping[str, Any],
    trial_configs: Mapping[str, Mapping[str, Any]],
    parameter_names: Sequence[str],
    sweep_id: str,
    tuning_strategy: str,
    max_trials: int,
    train_until_date: date,
    lookback_days: int,
    validation_days: int,
    top_k: int | None,
    fast_scoring_only: bool,
    halving_reduction_factor: int,
    annealing_temperature_start: float,
    annealing_temperature_end: float,
    annealing_neighbor_mutations: int,
) -> None:
    """Persist sweep-level checkpoint files after each successful trial."""
    if not rows:
        return

    preferred_stage = _preferred_checkpoint_stage(rows)
    best_row = best_trial_row(rows, search_space, preferred_stage=preferred_stage)
    best_trial_id = str(best_row["trial_id"])
    best_config = trial_configs.get(best_trial_id)

    _write_results_csv(sweep_dir / "results.csv", rows)

    if best_config is not None:
        _write_yaml(sweep_dir / "best_config.yaml", best_config)

    best_metrics = {
        key: value
        for key, value in best_row.items()
        if key not in {"trial_id", "run_dir", *parameter_names}
    }
    write_json(sweep_dir / "best_metrics.json", best_metrics)
    write_json(
        sweep_dir / "summary.json",
        {
            "sweep_id": sweep_id,
            "created_at": datetime.now(UTC),
            "checkpoint": True,
            "strategy": tuning_strategy,
            "max_trials": max_trials,
            "trials_run": len(rows),
            "best_trial_id": best_trial_id,
            "best_metrics_path": "best_metrics.json",
            "best_config_path": "best_config.yaml" if best_config is not None else None,
            "results_path": "results.csv",
            "train_until_date": train_until_date.isoformat(),
            "lookback_days": lookback_days,
            "validation_days": validation_days,
            "top_k": top_k,
            "fast_scoring_only": fast_scoring_only,
            "halving_reduction_factor": halving_reduction_factor,
            "annealing_temperature_start": annealing_temperature_start,
            "annealing_temperature_end": annealing_temperature_end,
            "annealing_neighbor_mutations": annealing_neighbor_mutations,
        },
    )


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
    halving_reduction_factor: int = 3,
    annealing_temperature_start: float = 0.10,
    annealing_temperature_end: float = 0.01,
    annealing_neighbor_mutations: int = 2,
    fast_scoring_only: bool = False,
) -> Path:
    logger = logging.getLogger(__name__)
    validation_window(train_until_date, validation_days)
    if max_trials <= 0:
        raise ValueError("max_trials must be a positive integer")
    if halving_reduction_factor <= 1:
        raise ValueError("halving_reduction_factor must be > 1")
    if annealing_temperature_start <= 0.0 or annealing_temperature_end <= 0.0:
        raise ValueError("annealing temperatures must be > 0")
    if annealing_temperature_end > annealing_temperature_start:
        raise ValueError("annealing_temperature_end must be <= annealing_temperature_start")
    if annealing_neighbor_mutations <= 0:
        raise ValueError("annealing_neighbor_mutations must be a positive integer")
    if fast_scoring_only and tuning_strategy == "successive_halving":
        raise ValueError("fast scoring-only mode does not support successive_halving")

    base_config = load_yaml_config(config_path)
    search_space = load_yaml_config(search_space_path)
    if fast_scoring_only:
        validate_scoring_only_search_space(search_space)
    sweep_id = _safe_sweep_id(
        sweep_name,
        strategy=tuning_strategy,
        train_until_date=train_until_date,
        lookback_days=lookback_days,
        validation_days=validation_days,
        top_k=top_k,
    )
    parameter_section = search_space.get("parameters", {})
    parameter_names = [str(name) for name in parameter_section] if isinstance(parameter_section, Mapping) else []
    total_grid_combinations = _grid_trial_count(search_space)
    sweep_dir = _resolve_project_path(output_dir) / sweep_id
    sweep_dir.mkdir(parents=True, exist_ok=False)
    logger.info(
        "[run_tune] start sweep_id=%s tuning_strategy=%s max_trials=%s grid_combinations=%s output_dir=%s fast_scoring_only=%s",
        sweep_id,
        tuning_strategy,
        max_trials,
        total_grid_combinations,
        sweep_dir,
        fast_scoring_only,
    )
    _write_yaml(sweep_dir / "base_config.yaml", base_config)
    _write_yaml(sweep_dir / "search_space.yaml", search_space)

    fast_context: FastScoringContext | None = None
    if fast_scoring_only:
        fast_context = build_fast_scoring_context(
            base_config=base_config,
            sweep_dir=sweep_dir,
            train_until_date=train_until_date,
            lookback_days=lookback_days,
            validation_days=validation_days,
            top_k=top_k,
            logger=logger,
        )

    rows: list[dict[str, Any]] = []
    trial_configs: dict[str, dict[str, Any]] = {}
    trial_overrides_by_id: dict[str, dict[str, Any]] = {}
    rng = _new_rng()

    if tuning_strategy in {"grid", "random"}:
        trial_overrides = select_trials(search_space, strategy=tuning_strategy, max_trials=max_trials)
        for index, overrides in enumerate(trial_overrides, start=1):
            trial_id = f"trial_{index:04d}"
            trial_overrides_by_id[trial_id] = dict(overrides)
            trial_lookback_days = lookback_days if fast_context is not None else _effective_trial_lookback_days(overrides, lookback_days)
            row, trial_config = _run_trial(
                logger=logger,
                base_config=base_config,
                sweep_dir=sweep_dir,
                trial_id=trial_id,
                overrides=overrides,
                strategy_columns={"resource_lookback_days": trial_lookback_days, "resource_validation_days": validation_days},
                train_until_date=train_until_date,
                lookback_days=trial_lookback_days,
                validation_days=validation_days,
                default_top_k=top_k,
                fast_context=fast_context,
            )
            trial_configs[trial_id] = trial_config
            rows = _annotate_objective_rows([*rows, row], search_space)
            _write_tuning_checkpoint(
                sweep_dir=sweep_dir,
                rows=rows,
                search_space=search_space,
                trial_configs=trial_configs,
                parameter_names=parameter_names,
                sweep_id=sweep_id,
                tuning_strategy=tuning_strategy,
                max_trials=max_trials,
                train_until_date=train_until_date,
                lookback_days=lookback_days,
                validation_days=validation_days,
                top_k=top_k,
                fast_scoring_only=fast_scoring_only,
                halving_reduction_factor=halving_reduction_factor,
                annealing_temperature_start=annealing_temperature_start,
                annealing_temperature_end=annealing_temperature_end,
                annealing_neighbor_mutations=annealing_neighbor_mutations,
            )
            best_id, best_primary = _current_best_log(rows, search_space)
            logger.info(
                "[run_tune] trial_done trial_id=%s objective_score=%s current_best_trial_id=%s current_best_%s=%s",
                trial_id,
                rows[-1].get("objective_score"),
                best_id,
                _primary_metric_name(search_space),
                best_primary,
            )

    elif tuning_strategy == "successive_halving":
        stage_one_candidates = select_trials(search_space, strategy="successive_halving", max_trials=max_trials)
        for index, overrides in enumerate(stage_one_candidates, start=1):
            trial_id = f"trial_{index:04d}"
            trial_overrides_by_id[trial_id] = dict(overrides)
            row, trial_config = _run_trial(
                logger=logger,
                base_config=base_config,
                sweep_dir=sweep_dir,
                trial_id=trial_id,
                overrides=overrides,
                strategy_columns={"stage": 1, "resource_lookback_days": 1, "resource_validation_days": 1},
                train_until_date=train_until_date,
                lookback_days=1,
                validation_days=1,
                default_top_k=top_k,
            )
            trial_configs[trial_id] = trial_config
            rows = _annotate_objective_rows([*rows, row], search_space)
            _write_tuning_checkpoint(
                sweep_dir=sweep_dir,
                rows=rows,
                search_space=search_space,
                trial_configs=trial_configs,
                parameter_names=parameter_names,
                sweep_id=sweep_id,
                tuning_strategy=tuning_strategy,
                max_trials=max_trials,
                train_until_date=train_until_date,
                lookback_days=lookback_days,
                validation_days=validation_days,
                top_k=top_k,
                fast_scoring_only=fast_scoring_only,
                halving_reduction_factor=halving_reduction_factor,
                annealing_temperature_start=annealing_temperature_start,
                annealing_temperature_end=annealing_temperature_end,
                annealing_neighbor_mutations=annealing_neighbor_mutations,
            )
        stage_one_rows = [row for row in rows if row.get("stage") == 1]
        survivors_count = max(1, math.ceil(len(stage_one_rows) / halving_reduction_factor))
        survivor_ids = [
            str(row["trial_id"])
            for row in sorted(stage_one_rows, key=lambda r: _objective_sort_key(r, search_space), reverse=True)[:survivors_count]
        ]
        logger.info("[run_tune] successive_halving survivors=%s reduction_factor=%s", survivor_ids, halving_reduction_factor)
        for trial_id in survivor_ids:
            overrides = trial_overrides_by_id[trial_id]
            trial_lookback_days = _effective_trial_lookback_days(overrides, lookback_days)
            row, trial_config = _run_trial(
                logger=logger,
                base_config=base_config,
                sweep_dir=sweep_dir,
                trial_id=trial_id,
                overrides=overrides,
                strategy_columns={"stage": 2, "resource_lookback_days": trial_lookback_days, "resource_validation_days": validation_days},
                train_until_date=train_until_date,
                lookback_days=trial_lookback_days,
                validation_days=validation_days,
                default_top_k=top_k,
            )
            trial_configs[trial_id] = trial_config
            rows = _annotate_objective_rows([*rows, row], search_space)
            _write_tuning_checkpoint(
                sweep_dir=sweep_dir,
                rows=rows,
                search_space=search_space,
                trial_configs=trial_configs,
                parameter_names=parameter_names,
                sweep_id=sweep_id,
                tuning_strategy=tuning_strategy,
                max_trials=max_trials,
                train_until_date=train_until_date,
                lookback_days=lookback_days,
                validation_days=validation_days,
                top_k=top_k,
                fast_scoring_only=fast_scoring_only,
                halving_reduction_factor=halving_reduction_factor,
                annealing_temperature_start=annealing_temperature_start,
                annealing_temperature_end=annealing_temperature_end,
                annealing_neighbor_mutations=annealing_neighbor_mutations,
            )

    elif tuning_strategy == "simulated_annealing":
        current_trial = _sample_trial(search_space, rng=rng, discrete_only=True)
        current_score = 0.0
        for index in range(max_trials):
            trial_id = f"trial_{index + 1:04d}"
            temperature = _annealing_temperature(
                index,
                max_trials,
                start=annealing_temperature_start,
                end=annealing_temperature_end,
            )
            candidate = dict(current_trial) if index == 0 else _mutate_trial(
                current_trial,
                search_space,
                mutations=annealing_neighbor_mutations,
                rng=rng,
            )
            trial_overrides_by_id[trial_id] = dict(candidate)
            trial_lookback_days = lookback_days if fast_context is not None else _effective_trial_lookback_days(candidate, lookback_days)
            row, trial_config = _run_trial(
                logger=logger,
                base_config=base_config,
                sweep_dir=sweep_dir,
                trial_id=trial_id,
                overrides=candidate,
                strategy_columns={
                    "resource_lookback_days": trial_lookback_days,
                    "resource_validation_days": validation_days,
                    "accepted": False,
                    "temperature": _normalize_number(temperature),
                },
                train_until_date=train_until_date,
                lookback_days=trial_lookback_days,
                validation_days=validation_days,
                default_top_k=top_k,
                fast_context=fast_context,
            )
            trial_configs[trial_id] = trial_config
            candidate_score = _objective_fields(row, search_space)[1]
            if index == 0 or candidate_score >= current_score:
                accepted = True
            else:
                accepted = rng.random() < math.exp((candidate_score - current_score) / max(temperature, 1e-12))
            if accepted:
                current_trial, current_score = dict(candidate), candidate_score
            row["accepted"] = accepted
            rows = _annotate_objective_rows([*rows, row], search_space)
            _write_tuning_checkpoint(
                sweep_dir=sweep_dir,
                rows=rows,
                search_space=search_space,
                trial_configs=trial_configs,
                parameter_names=parameter_names,
                sweep_id=sweep_id,
                tuning_strategy=tuning_strategy,
                max_trials=max_trials,
                train_until_date=train_until_date,
                lookback_days=lookback_days,
                validation_days=validation_days,
                top_k=top_k,
                fast_scoring_only=fast_scoring_only,
                halving_reduction_factor=halving_reduction_factor,
                annealing_temperature_start=annealing_temperature_start,
                annealing_temperature_end=annealing_temperature_end,
                annealing_neighbor_mutations=annealing_neighbor_mutations,
            )
            logger.info(
                "[run_tune] annealing trial_id=%s accepted=%s temperature=%s objective_score=%s",
                trial_id,
                accepted,
                rows[-1].get("temperature"),
                rows[-1].get("objective_score"),
            )
    else:
        raise ValueError(f"Unsupported tuning strategy: {tuning_strategy}")

    rows = _annotate_objective_rows(rows, search_space)
    _write_results_csv(sweep_dir / "results.csv", rows)
    preferred_stage = 2 if tuning_strategy == "successive_halving" and any(row.get("stage") == 2 for row in rows) else None
    best_row = best_trial_row(rows, search_space, preferred_stage=preferred_stage)
    best_trial_id = str(best_row["trial_id"])
    best_config = trial_configs[best_trial_id]
    best_metrics = {key: value for key, value in best_row.items() if key not in {"trial_id", "run_dir", *parameter_names}}
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
            "fast_scoring_only": fast_scoring_only,
            "halving_reduction_factor": halving_reduction_factor,
            "annealing_temperature_start": annealing_temperature_start,
            "annealing_temperature_end": annealing_temperature_end,
            "annealing_neighbor_mutations": annealing_neighbor_mutations,
        },
    )
    logger.info(
        "[run_tune] done best_trial_id=%s objective_score=%s results_path=%s best_config_path=%s",
        best_trial_id,
        best_row.get("objective_score"),
        sweep_dir / "results.csv",
        sweep_dir / "best_config.yaml",
    )
    return sweep_dir


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Tune recommendation parameters.")
    parser.add_argument("train_until_date", type=_parse_iso_date)
    parser.add_argument("--lookback-days", type=int, default=30)
    parser.add_argument("--validation-days", type=int, default=1)
    parser.add_argument("--top-k", type=int, default=None)
    parser.add_argument("--config-path", type=Path, default=Path("configs/production.yaml"))
    parser.add_argument("--search-space-path", type=Path, default=Path("configs/tuning/search_space.yaml"))
    parser.add_argument("--max-trials", type=int, default=30)
    parser.add_argument(
        "--tuning-strategy",
        choices=["grid", "random", "successive_halving", "simulated_annealing"],
        default="random",
    )
    parser.add_argument("--output-dir", type=Path, default=Path("outputs/tuning"))
    parser.add_argument("--sweep-name", default=None)
    parser.add_argument("--halving-reduction-factor", type=int, default=3)
    parser.add_argument("--annealing-temperature-start", type=float, default=0.10)
    parser.add_argument("--annealing-temperature-end", type=float, default=0.01)
    parser.add_argument("--annealing-neighbor-mutations", type=int, default=2)
    parser.add_argument("--fast-scoring-only", action="store_true")
    return parser.parse_args()


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    logger = logging.getLogger(__name__)
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
            halving_reduction_factor=args.halving_reduction_factor,
            annealing_temperature_start=args.annealing_temperature_start,
            annealing_temperature_end=args.annealing_temperature_end,
            annealing_neighbor_mutations=args.annealing_neighbor_mutations,
            fast_scoring_only=args.fast_scoring_only,
        )
    except Exception:
        logger.exception("[run_tune] failed")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
