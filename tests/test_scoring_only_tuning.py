"""Tests for fast scoring-only tuning helpers."""

from __future__ import annotations

import csv
import json
from datetime import date
from pathlib import Path
from typing import Any

import pytest
import yaml

from ozon_similar_products.cli import run_tune
from ozon_similar_products.cli.scoring_only_tuning import validate_scoring_only_search_space
from ozon_similar_products.evaluation.metrics import OfflineMetrics


def test_validate_scoring_only_search_space_rejects_train_artifact_parameters() -> None:
    with pytest.raises(ValueError, match="pipeline.lookback_days"):
        validate_scoring_only_search_space(
            {
                "parameters": {
                    "pipeline.lookback_days": {"type": "choice", "values": [7]},
                    "scoring.beta": {"type": "choice", "values": [0.5]},
                }
            }
        )


def test_run_tuning_fast_scoring_only_uses_prebuilt_context(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    base_config: dict[str, Any] = {"pipeline": {"top_k": 20}}
    search_space: dict[str, Any] = {
        "objective": {"primary_metric": "to_cart_hit_rate_at_k"},
        "parameters": {"scoring.beta": {"type": "choice", "values": [0.5]}},
    }
    captured: dict[str, Any] = {"full_runs": 0, "fast_runs": 0}

    def fake_load_yaml_config(path: Path) -> dict[str, Any]:
        return search_space if path.name == "search_space.yaml" else base_config

    class FakeFullRunResult:
        def __init__(self, trial_dir: Path) -> None:
            self.metrics = OfflineMetrics(
                to_cart_hit_rate_at_k=0.9,
                ndcg_at_k=0.8,
                recall_at_k=0.7,
                mrr_at_k=0.6,
                coverage_at_k=1.0,
                to_cart_recall_at_k=0.5,
            )
            self.metrics_path = trial_dir / "evaluation" / "metrics.json"
            self.manifest_path = trial_dir / "manifest.json"
            self.metrics_path.parent.mkdir(parents=True)
            self.metrics_path.write_text("{}", encoding="utf-8")
            self.manifest_path.write_text("{}", encoding="utf-8")

    def fail_execute_full_run(**_: object) -> None:
        captured["full_runs"] += 1
        raise AssertionError("execute_full_run should not run in fast scoring-only mode")

    def fake_build_fast_context(**kwargs: object) -> object:
        captured["context_kwargs"] = kwargs
        return object()

    def fake_execute_scoring_only_trial(**kwargs: object) -> FakeFullRunResult:
        captured["fast_runs"] += 1
        trial_config_path = kwargs["trial_config_path"]
        assert isinstance(trial_config_path, Path)
        captured["trial_config"] = yaml.safe_load(trial_config_path.read_text(encoding="utf-8"))
        run_dir = kwargs["run_dir"]
        assert isinstance(run_dir, Path)
        return FakeFullRunResult(run_dir)

    monkeypatch.setattr(run_tune, "load_yaml_config", fake_load_yaml_config)
    monkeypatch.setattr(run_tune, "execute_full_run", fail_execute_full_run)
    monkeypatch.setattr(run_tune, "build_fast_scoring_context", fake_build_fast_context)
    monkeypatch.setattr(run_tune, "execute_scoring_only_trial", fake_execute_scoring_only_trial)

    sweep_dir = run_tune.run_tuning(
        train_until_date=date(2024, 3, 23),
        lookback_days=7,
        validation_days=1,
        top_k=20,
        config_path=tmp_path / "base.yaml",
        search_space_path=tmp_path / "search_space.yaml",
        max_trials=1,
        tuning_strategy="grid",
        output_dir=tmp_path / "tuning",
        sweep_name="fast",
        fast_scoring_only=True,
    )

    assert captured["full_runs"] == 0
    assert captured["fast_runs"] == 1
    assert captured["context_kwargs"]["lookback_days"] == 7
    assert captured["trial_config"] == {"pipeline": {"top_k": 20}, "scoring": {"beta": 0.5}}
    summary = json.loads((sweep_dir / "summary.json").read_text(encoding="utf-8"))
    assert summary["fast_scoring_only"] is True
    rows = list(csv.DictReader((sweep_dir / "results.csv").open(encoding="utf-8")))
    assert rows[0]["fast_scoring_only"] == "True"
