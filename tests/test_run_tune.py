"""Tests for tuning search-space helpers."""

import csv
import random
from pathlib import Path
from typing import Any, cast

import pytest
import yaml

from ozon_similar_products.cli import run_tune
from ozon_similar_products.evaluation import metrics_to_flat_dict
from ozon_similar_products.evaluation.metrics import OfflineMetrics


def test_set_by_dot_path_updates_nested_config_without_mutation() -> None:
    config = {"scoring": {"business_weights": {"click": 3.0}}, "topk": {"top_k": 20}}

    updated = run_tune.set_by_dot_path(config, "scoring.business_weights.click", 5.0)

    assert updated["scoring"]["business_weights"]["click"] == 5.0
    assert config["scoring"]["business_weights"]["click"] == 3.0


def test_generate_grid_trials_supports_range_search_space() -> None:
    search_space = {
        "parameters": {
            "topk.top_k": {"type": "choice", "values": [10, 20]},
            "scoring.min_unique_users": {"type": "int_range", "min": 1, "max": 2, "step": 1},
            "scoring.beta": {"type": "float_range", "min": 0.0, "max": 0.5, "step": 0.5},
            "scoring.popularity_normalization.smoothing": {
                "type": "log_float_range",
                "min": 0.1,
                "max": 10.0,
                "num": 3,
            },
        }
    }

    trials = run_tune.generate_grid_trials(search_space)

    assert len(trials) == 24
    assert trials[0] == {
        "topk.top_k": 10,
        "scoring.min_unique_users": 1,
        "scoring.beta": 0.0,
        "scoring.popularity_normalization.smoothing": 0.1,
    }
    assert trials[-1] == {
        "topk.top_k": 20,
        "scoring.min_unique_users": 2,
        "scoring.beta": 0.5,
        "scoring.popularity_normalization.smoothing": 10.0,
    }


def test_select_trials_random_limits_without_replacement(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    search_space = {
        "parameters": {
            "a": {"type": "choice", "values": [1, 2, 3]},
        }
    }
    monkeypatch.setattr(run_tune, "_new_rng", lambda: random.Random(1))

    trials = run_tune.select_trials(search_space, strategy="random", max_trials=2)

    assert len(trials) == 2
    assert len({trial["a"] for trial in trials}) == 2


def test_best_trial_row_prefers_eligible_balanced_objective() -> None:
    search_space = {
        "objective": {
            "primary_metric": "to_cart_hit_rate_at_k",
            "supporting_metrics": ["ndcg_at_k", "recall_at_k"],
            "penalty_metrics": ["popularity_bias_at_k"],
            "constraints": {"min_coverage_at_k": 0.5},
        }
    }
    rows = [
        {
            "trial_id": "trial_0001",
            "to_cart_hit_rate_at_k": 0.5,
            "ndcg_at_k": 1.0,
            "recall_at_k": 1.0,
            "coverage_at_k": 1.0,
            "popularity_bias_at_k": 0.1,
        },
        {
            "trial_id": "trial_0002",
            "to_cart_hit_rate_at_k": 0.6,
            "ndcg_at_k": 0.2,
            "recall_at_k": 0.2,
            "coverage_at_k": 1.0,
            "popularity_bias_at_k": 0.1,
        },
        {
            "trial_id": "trial_0003",
            "to_cart_hit_rate_at_k": 0.9,
            "ndcg_at_k": 0.9,
            "recall_at_k": 0.9,
            "coverage_at_k": 0.1,
            "popularity_bias_at_k": 0.1,
        },
    ]

    best = run_tune.best_trial_row(rows, search_space)

    assert best["trial_id"] == "trial_0001"


def test_metrics_flat_dict_and_tuning_csv_use_expected_metric_names(tmp_path: Path) -> None:
    metrics = metrics_to_flat_dict(
        OfflineMetrics(
            hit_rate_at_k=1.0,
            recall_at_k=0.8,
            ndcg_at_k=0.7,
            mrr_at_k=0.5,
            coverage_at_k=1.0,
            popularity_bias_at_k=0.2,
            fallback_share_at_k=0.1,
            view_hit_rate_at_k=1.0,
            view_recall_at_k=0.8,
            click_hit_rate_at_k=0.7,
            click_recall_at_k=0.6,
            favorite_hit_rate_at_k=0.4,
            favorite_recall_at_k=0.3,
            to_cart_hit_rate_at_k=1.0,
            to_cart_recall_at_k=0.75,
            evaluated_items=2,
            recommended_items=2,
            ground_truth_pairs=3,
        )
    )
    expected_metric_names = [
        "hit_rate_at_k",
        "recall_at_k",
        "ndcg_at_k",
        "mrr_at_k",
        "coverage_at_k",
        "popularity_bias_at_k",
        "fallback_share_at_k",
        "view_hit_rate_at_k",
        "view_recall_at_k",
        "click_hit_rate_at_k",
        "click_recall_at_k",
        "favorite_hit_rate_at_k",
        "favorite_recall_at_k",
        "to_cart_hit_rate_at_k",
        "to_cart_recall_at_k",
        "evaluated_items",
        "recommended_items",
        "ground_truth_pairs",
    ]

    assert list(metrics) == expected_metric_names

    results_path = run_tune._write_results_csv(
        tmp_path / "results.csv",
        [{"trial_id": "trial_0001", "run_dir": "runs/trial_0001", **metrics}],
    )

    with results_path.open(encoding="utf-8", newline="") as file:
        reader = csv.DictReader(file)
        assert reader.fieldnames == ["trial_id", "run_dir", *expected_metric_names]


def test_run_tuning_uses_trial_overrides_and_best_config_without_scratch_artifacts(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    base_config: dict[str, Any] = {
        "pipeline": {"lookback_days": 30, "top_k": 20},
        "artifacts": {
            "events_clean_dir": "data/processed/events_clean",
            "daily_pairs_dir": "data/processed/item_pairs",
        },
    }
    search_space: dict[str, Any] = {
        "objective": {"primary_metric": "to_cart_hit_rate_at_k"},
        "parameters": {
            "pipeline.lookback_days": {"type": "choice", "values": [7]},
            "topk.top_k": {"type": "choice", "values": [10]},
        },
    }
    captured: dict[str, object] = {}

    def fake_load_yaml_config(path: Path) -> dict[str, Any]:
        return search_space if path.name == "search_space.yaml" else base_config

    class FakeFullRunResult:
        def __init__(self, trial_dir: Path) -> None:
            self.metrics = OfflineMetrics(
                to_cart_hit_rate_at_k=0.5,
                ndcg_at_k=0.5,
                recall_at_k=0.5,
                mrr_at_k=0.5,
                coverage_at_k=0.5,
                to_cart_recall_at_k=0.5,
            )
            self.metrics_path = trial_dir / "evaluation" / "metrics.json"
            self.manifest_path = trial_dir / "manifest.json"
            self.metrics_path.parent.mkdir(parents=True)
            self.metrics_path.write_text("{}", encoding="utf-8")
            self.manifest_path.write_text("{}", encoding="utf-8")

    def fake_execute_full_run(**kwargs: object) -> FakeFullRunResult:
        trial_dir = kwargs["run_dir"]
        assert isinstance(trial_dir, Path)
        captured["lookback_days"] = kwargs["lookback_days"]
        captured["top_k"] = kwargs["top_k"]
        captured["trial_dir"] = trial_dir
        config_path = cast(Path, kwargs["config_path"])
        captured["trial_config"] = yaml.safe_load(
            config_path.read_text(encoding="utf-8")
        )
        return FakeFullRunResult(trial_dir)

    monkeypatch.setattr(run_tune, "load_yaml_config", fake_load_yaml_config)
    monkeypatch.setattr(run_tune, "execute_full_run", fake_execute_full_run)

    sweep_dir = run_tune.run_tuning(
        train_until_date=run_tune._parse_iso_date("2024-03-23"),
        lookback_days=1,
        validation_days=1,
        top_k=5,
        config_path=tmp_path / "base.yaml",
        search_space_path=tmp_path / "search_space.yaml",
        max_trials=1,
        tuning_strategy="grid",
        output_dir=tmp_path / "tuning",
        sweep_name="unit",
    )

    assert captured["lookback_days"] == 7
    assert captured["top_k"] == 10
    trial_dir = captured["trial_dir"]
    assert isinstance(trial_dir, Path)
    trial_config = captured["trial_config"]
    assert isinstance(trial_config, dict)
    artifacts = trial_config["artifacts"]
    assert isinstance(artifacts, dict)
    assert artifacts["events_clean_dir"] == (
        trial_dir / "artifacts" / "events_clean"
    ).as_posix()
    assert artifacts["daily_pairs_dir"] == (
        trial_dir / "artifacts" / "daily_pairs"
    ).as_posix()
    assert (trial_dir / "manifest.json").exists()
    assert (trial_dir / "metrics.json").exists()
    assert (sweep_dir / "results.csv").exists()

    best_config = yaml.safe_load((sweep_dir / "best_config.yaml").read_text(encoding="utf-8"))
    assert best_config["artifacts"]["events_clean_dir"] == "data/processed/events_clean"
    assert best_config["artifacts"]["daily_pairs_dir"] == "data/processed/item_pairs"

    best_metrics = yaml.safe_load((sweep_dir / "best_metrics.json").read_text(encoding="utf-8"))
    assert "objective_score" in best_metrics
    assert best_metrics["objective_primary_metric"] == "to_cart_hit_rate_at_k"


def test_run_tuning_successive_halving_writes_stage_columns(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    base_config = {"pipeline": {"top_k": 20}}
    search_space = {
        "objective": {"primary_metric": "to_cart_hit_rate_at_k"},
        "parameters": {"topk.top_k": {"type": "choice", "values": [10, 20, 30]}},
    }

    def fake_load_yaml_config(path: Path) -> dict[str, Any]:
        return search_space if path.name == "search_space.yaml" else base_config

    class FakeFullRunResult:
        def __init__(self, trial_dir: Path, score: float) -> None:
            self.metrics = OfflineMetrics(
                to_cart_hit_rate_at_k=score,
                ndcg_at_k=score,
                recall_at_k=score,
                mrr_at_k=score,
                coverage_at_k=1.0,
                to_cart_recall_at_k=score,
            )
            self.metrics_path = trial_dir / "evaluation" / "metrics.json"
            self.manifest_path = trial_dir / "manifest.json"
            self.metrics_path.parent.mkdir(parents=True)
            self.metrics_path.write_text("{}", encoding="utf-8")
            self.manifest_path.write_text("{}", encoding="utf-8")

    def fake_execute_full_run(**kwargs: object) -> FakeFullRunResult:
        config_path = cast(Path, kwargs["config_path"])
        config = yaml.safe_load(config_path.read_text(encoding="utf-8"))
        top_k = config["topk"]["top_k"]
        trial_dir = cast(Path, kwargs["run_dir"])
        score = float(top_k) / 100.0
        return FakeFullRunResult(trial_dir, score=score)

    monkeypatch.setattr(run_tune, "load_yaml_config", fake_load_yaml_config)
    monkeypatch.setattr(run_tune, "execute_full_run", fake_execute_full_run)
    monkeypatch.setattr(
        run_tune,
        "select_trials",
        lambda *_, **__: [
            {"topk.top_k": 10},
            {"topk.top_k": 20},
            {"topk.top_k": 30},
        ],
    )

    sweep_dir = run_tune.run_tuning(
        train_until_date=run_tune._parse_iso_date("2024-03-23"),
        lookback_days=1,
        validation_days=2,
        top_k=5,
        config_path=tmp_path / "base.yaml",
        search_space_path=tmp_path / "search_space.yaml",
        max_trials=3,
        tuning_strategy="successive_halving",
        output_dir=tmp_path / "tuning",
        sweep_name="halving",
        halving_reduction_factor=2,
    )

    rows = list(csv.DictReader((sweep_dir / "results.csv").open(encoding="utf-8")))
    assert len(rows) == 5
    assert sum(1 for row in rows if row["stage"] == "1") == 3
    assert sum(1 for row in rows if row["stage"] == "2") == 2
    assert all(row["resource_lookback_days"] for row in rows)
    assert all(row["resource_validation_days"] for row in rows)


def test_run_tuning_simulated_annealing_writes_acceptance_columns(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    base_config = {"pipeline": {"top_k": 20}}
    search_space = {
        "objective": {
            "primary_metric": "to_cart_hit_rate_at_k",
            "supporting_metrics": ["ndcg_at_k"],
        },
        "parameters": {
            "topk.top_k": {"type": "choice", "values": [10, 20]},
            "scoring.min_unique_users": {"type": "int_range", "min": 1, "max": 2, "step": 1},
        },
    }

    def fake_load_yaml_config(path: Path) -> dict[str, Any]:
        return search_space if path.name == "search_space.yaml" else base_config

    class FakeFullRunResult:
        def __init__(self, trial_dir: Path, score: float) -> None:
            self.metrics = OfflineMetrics(
                to_cart_hit_rate_at_k=score,
                ndcg_at_k=score,
                recall_at_k=score,
                mrr_at_k=score,
                coverage_at_k=1.0,
                to_cart_recall_at_k=score,
            )
            self.metrics_path = trial_dir / "evaluation" / "metrics.json"
            self.manifest_path = trial_dir / "manifest.json"
            self.metrics_path.parent.mkdir(parents=True)
            self.metrics_path.write_text("{}", encoding="utf-8")
            self.manifest_path.write_text("{}", encoding="utf-8")

    def fake_execute_full_run(**kwargs: object) -> FakeFullRunResult:
        config_path = cast(Path, kwargs["config_path"])
        config = yaml.safe_load(config_path.read_text(encoding="utf-8"))
        top_k = config["topk"]["top_k"]
        min_unique_users = config["scoring"]["min_unique_users"]
        score = 0.4 if (top_k, min_unique_users) == (10, 1) else 0.8
        trial_dir = cast(Path, kwargs["run_dir"])
        return FakeFullRunResult(trial_dir, score=score)

    monkeypatch.setattr(run_tune, "load_yaml_config", fake_load_yaml_config)
    monkeypatch.setattr(run_tune, "execute_full_run", fake_execute_full_run)
    monkeypatch.setattr(run_tune, "_new_rng", lambda: random.Random(0))

    sweep_dir = run_tune.run_tuning(
        train_until_date=run_tune._parse_iso_date("2024-03-23"),
        lookback_days=1,
        validation_days=1,
        top_k=5,
        config_path=tmp_path / "base.yaml",
        search_space_path=tmp_path / "search_space.yaml",
        max_trials=3,
        tuning_strategy="simulated_annealing",
        output_dir=tmp_path / "tuning",
        sweep_name="annealing",
    )

    rows = list(csv.DictReader((sweep_dir / "results.csv").open(encoding="utf-8")))
    assert len(rows) == 3
    assert all(row["temperature"] for row in rows)
    assert rows[0]["accepted"] == "True"
    assert {row["accepted"] for row in rows}.issubset({"True", "False"})
