"""Tests for tuning search-space helpers."""

import csv
from pathlib import Path

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


def test_generate_grid_trials_from_choice_search_space() -> None:
    search_space = {
        "parameters": {
            "topk.top_k": {"type": "choice", "values": [10, 20]},
            "scoring.min_unique_users": {"type": "choice", "values": [1, 2]},
        }
    }

    trials = run_tune.generate_grid_trials(search_space)

    assert trials == [
        {"topk.top_k": 10, "scoring.min_unique_users": 1},
        {"topk.top_k": 10, "scoring.min_unique_users": 2},
        {"topk.top_k": 20, "scoring.min_unique_users": 1},
        {"topk.top_k": 20, "scoring.min_unique_users": 2},
    ]


def test_select_trials_random_limits_without_replacement() -> None:
    search_space = {
        "parameters": {
            "a": {"type": "choice", "values": [1, 2, 3]},
        }
    }

    trials = run_tune.select_trials(search_space, strategy="random", max_trials=2, seed=1)

    assert len(trials) == 2
    assert len({trial["a"] for trial in trials}) == 2


def test_successive_halving_is_clear_not_implemented() -> None:
    with pytest.raises(NotImplementedError, match="successive_halving"):
        run_tune.select_trials(
            {"parameters": {"a": {"type": "choice", "values": [1]}}},
            strategy="successive_halving",
            max_trials=1,
        )


def test_best_trial_row_uses_primary_metric_tie_breakers_and_popularity_penalty() -> None:
    search_space = {
        "objective": {
            "primary_metric": "to_cart_hit_rate_at_k",
            "tie_breakers": ["ndcg_at_k", "coverage_at_k"],
        }
    }
    rows = [
        {
            "trial_id": "trial_0001",
            "to_cart_hit_rate_at_k": 0.5,
            "ndcg_at_k": 0.8,
            "coverage_at_k": 0.1,
            "popularity_bias_at_k": 0.2,
        },
        {
            "trial_id": "trial_0002",
            "to_cart_hit_rate_at_k": 0.5,
            "ndcg_at_k": 0.8,
            "coverage_at_k": 0.2,
            "popularity_bias_at_k": 0.9,
        },
    ]

    best = run_tune.best_trial_row(rows, search_space)

    assert best["trial_id"] == "trial_0002"


def test_copy_if_different_skips_same_file(tmp_path: Path) -> None:
    manifest_path = tmp_path / "trial_0001" / "manifest.json"
    manifest_path.parent.mkdir(parents=True)
    manifest_path.write_text('{"run_id": "trial_0001"}', encoding="utf-8")

    copied_path = run_tune._copy_if_different(manifest_path, manifest_path)

    assert copied_path == manifest_path
    assert manifest_path.read_text(encoding="utf-8") == '{"run_id": "trial_0001"}'


def test_metrics_flat_dict_and_tuning_csv_use_expected_metric_names(tmp_path: Path) -> None:
    metrics = metrics_to_flat_dict(
        OfflineMetrics(
            hit_rate_at_k=1.0,
            weighted_recall_at_k=0.8,
            ndcg_at_k=0.7,
            mrr_at_k=0.5,
            coverage_at_k=1.0,
            popularity_bias_at_k=0.2,
            fallback_share_at_k=0.1,
            metadata_gap_share_at_k=None,
            to_cart_hit_rate_at_k=1.0,
            to_cart_recall_at_k=0.75,
            evaluated_items=2,
            recommended_items=2,
            ground_truth_pairs=3,
        )
    )
    expected_metric_names = [
        "hit_rate_at_k",
        "weighted_recall_at_k",
        "ndcg_at_k",
        "mrr_at_k",
        "coverage_at_k",
        "popularity_bias_at_k",
        "fallback_share_at_k",
        "metadata_gap_share_at_k",
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


def test_run_tuning_uses_trial_overrides_and_isolated_artifacts(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    base_config = {
        "pipeline": {"lookback_days": 30, "top_k": 20},
        "artifacts": {
            "events_clean_dir": "data/processed/events_clean",
            "daily_pairs_dir": "data/processed/item_pairs",
        },
    }
    search_space = {
        "objective": {"primary_metric": "to_cart_hit_rate_at_k"},
        "parameters": {
            "pipeline.lookback_days": {"type": "choice", "values": [7]},
            "topk.top_k": {"type": "choice", "values": [10]},
        },
    }
    captured: dict[str, object] = {}

    def fake_load_yaml_config(path: Path) -> dict[str, object]:
        return search_space if path.name == "search_space.yaml" else base_config

    class FakeFullRunResult:
        def __init__(self, trial_dir: Path) -> None:
            self.metrics = OfflineMetrics(to_cart_hit_rate_at_k=0.5)
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
        captured["trial_config"] = yaml.safe_load(
            Path(kwargs["config_path"]).read_text(encoding="utf-8")
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
