"""Tests for fast scoring-only tuning helpers."""

from __future__ import annotations

import csv
import json
import logging
from datetime import date
from pathlib import Path
from typing import Any

import polars as pl
import pytest
import yaml

from ozon_similar_products.cli import run_tune, scoring_only_tuning
from ozon_similar_products.cli.scoring_only_tuning import validate_scoring_only_search_space
from ozon_similar_products.config import PROJECT_ROOT, load_yaml_config
from ozon_similar_products.data import schemas
from ozon_similar_products.evaluation.metrics import OfflineMetrics
from ozon_similar_products.pipeline.run_pipeline import (
    PipelineRunResult,
    _daily_pair_stats_paths_for_date,
)


def _daily_pair_counts() -> pl.DataFrame:
    return pl.DataFrame(
        [
            {
                "pair_date": "2024-04-23",
                "item_id": 1,
                "similar_item_id": 10,
                "pair_count": 3,
                "view_count": 3,
                "click_count": 0,
                "favorite_count": 0,
                "to_cart_count": 0,
                "weighted_pair_count": 3.0,
                "weighted_view_count": 3.0,
                "weighted_click_count": 0.0,
                "weighted_favorite_count": 0.0,
                "weighted_to_cart_count": 0.0,
            },
            {
                "pair_date": "2024-04-23",
                "item_id": 2,
                "similar_item_id": 20,
                "pair_count": 5,
                "view_count": 0,
                "click_count": 0,
                "favorite_count": 0,
                "to_cart_count": 5,
                "weighted_pair_count": 5.0,
                "weighted_view_count": 0.0,
                "weighted_click_count": 0.0,
                "weighted_favorite_count": 0.0,
                "weighted_to_cart_count": 5.0,
            },
        ]
    ).select(schemas.DAILY_PAIR_COUNTS_COLUMNS)


def _daily_pair_user_keys() -> pl.DataFrame:
    return pl.DataFrame(
        [
            {"pair_date": "2024-04-23", "item_id": 1, "similar_item_id": 10, "user_id": 100},
            {"pair_date": "2024-04-23", "item_id": 2, "similar_item_id": 20, "user_id": 200},
        ]
    ).select(schemas.DAILY_PAIR_USER_KEYS_COLUMNS)


def _daily_pair_session_keys() -> pl.DataFrame:
    return pl.DataFrame(
        [
            {
                "pair_date": "2024-04-23",
                "item_id": 1,
                "similar_item_id": 10,
                "user_id": 100,
                "session_index": 1,
            },
            {
                "pair_date": "2024-04-23",
                "item_id": 2,
                "similar_item_id": 20,
                "user_id": 200,
                "session_index": 1,
            },
        ]
    ).select(schemas.DAILY_PAIR_SESSION_KEYS_COLUMNS)


def _empty_action_distribution() -> pl.DataFrame:
    return pl.DataFrame({column: [] for column in schemas.ACTION_TYPE_DISTRIBUTION_COLUMNS})


def _item_popularity() -> pl.DataFrame:
    return pl.DataFrame(
        {
            "item_id": [1, 2, 10, 20],
            "events_count": [10, 10, 5, 5],
            "unique_users": [2, 2, 1, 1],
            "views_count": [10, 5, 5, 0],
            "clicks_count": [0, 0, 0, 0],
            "favorites_count": [0, 0, 0, 0],
            "to_cart_count": [0, 5, 0, 5],
        }
    ).select(schemas.ITEM_POPULARITY_COLUMNS)


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


def test_fast_scoring_only_search_space_files_are_valid() -> None:
    for relative_path in (
        "configs/tuning/search_space_scoring_core.yaml",
        "configs/tuning/search_space_scoring_fallback.yaml",
    ):
        search_space = load_yaml_config(PROJECT_ROOT / relative_path)
        validate_scoring_only_search_space(search_space)
        assert search_space["objective"]["primary_metric"] == "to_cart_hit_rate_at_k"
        assert search_space["parameters"]


def test_build_fast_scoring_context_builds_bucketed_pair_aggregate_parts(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    base_config: dict[str, Any] = {
        "pipeline": {"aggregation_item_buckets": 2},
        "business": {"fallback": {"enabled": False}},
    }

    class FakeValidationCache:
        ground_truth = pl.DataFrame()
        validation_pair_counts = pl.DataFrame()
        cache_key = "validation-cache-key"
        cache_hit = True
        cache_dir = tmp_path / "validation_cache" / "validation-cache-key"

    def fake_run_pipeline(
        *,
        train_until_date: str,
        lookback_days: int,
        config_path: Path,
        output_dir: Path,
        run_id: str,
        update_latest: bool,
    ) -> PipelineRunResult:
        assert train_until_date == "2024-04-23"
        assert lookback_days == 1
        assert run_id == "fast_scoring_base"
        assert update_latest is False

        written_config = yaml.safe_load(config_path.read_text(encoding="utf-8"))
        window_start = "2024-04-23"
        window_end = "2024-04-23"
        daily_pairs_dir = Path(written_config["artifacts"]["daily_pairs_dir"])
        count_path, widget_count_path, user_key_path, session_key_path = (
            _daily_pair_stats_paths_for_date(daily_pairs_dir, window_start)
        )
        for path in (count_path, widget_count_path, user_key_path, session_key_path):
            path.parent.mkdir(parents=True, exist_ok=True)
        _daily_pair_counts().write_parquet(count_path)
        _daily_pair_counts().with_columns(
            pl.lit("main").alias("target_widget_name")
        ).select(schemas.DAILY_PAIR_WIDGET_COUNTS_COLUMNS).write_parquet(widget_count_path)
        _daily_pair_user_keys().write_parquet(user_key_path)
        _daily_pair_session_keys().write_parquet(session_key_path)

        item_popularity_path = scoring_only_tuning._window_artifact_path(
            written_config,
            "item_popularity_dir",
            "data/processed/item_popularity",
            window_start,
            window_end,
        )
        action_distribution_path = scoring_only_tuning._window_artifact_path(
            written_config,
            "action_type_distribution_dir",
            "data/processed/action_type_distribution",
            window_start,
            window_end,
        )
        item_popularity_path.parent.mkdir(parents=True, exist_ok=True)
        action_distribution_path.parent.mkdir(parents=True, exist_ok=True)
        _item_popularity().write_parquet(item_popularity_path)
        _empty_action_distribution().write_parquet(action_distribution_path)

        single_pair_aggregate_path = scoring_only_tuning._window_artifact_path(
            written_config,
            "pair_aggregates_dir",
            "data/processed/pair_aggregates",
            window_start,
            window_end,
        )
        assert not single_pair_aggregate_path.exists()

        run_dir = Path(output_dir)
        manifest_path = run_dir / "manifest.json"
        manifest_path.parent.mkdir(parents=True, exist_ok=True)
        manifest = {
            "window_start": window_start,
            "window_end": window_end,
            "rows": {"pair_aggregates": 2, "daily_pairs": 8},
            "paths": {},
        }
        manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
        return PipelineRunResult(
            run_id=run_id,
            run_dir=run_dir,
            manifest_path=manifest_path,
            detailed_recommendations_path=run_dir / "recommendations" / "detailed.parquet",
            enriched_recommendations_path=run_dir / "recommendations" / "enriched.parquet",
            lookup_recommendations_path=run_dir / "recommendations" / "lookup.parquet",
            manifest=manifest,
        )

    monkeypatch.setattr(scoring_only_tuning, "run_pipeline", fake_run_pipeline)
    monkeypatch.setattr(scoring_only_tuning, "_git_sha", lambda: "test-sha")
    monkeypatch.setattr(
        scoring_only_tuning,
        "load_or_build_validation_cache",
        lambda **_: FakeValidationCache(),
    )

    context = scoring_only_tuning.build_fast_scoring_context(
        base_config=base_config,
        sweep_dir=tmp_path,
        train_until_date=date(2024, 4, 23),
        lookback_days=1,
        validation_days=1,
        top_k=20,
        logger=logging.getLogger(__name__),
    )

    assert len(context.pair_aggregate_parts) == 2
    pair_aggregate_parts = [
        part.collect() if isinstance(part, pl.LazyFrame) else part
        for part in context.pair_aggregate_parts
    ]
    assert [part["item_id"].to_list() for part in pair_aggregate_parts] == [[2], [1]]
    assert sorted((tmp_path / "fast_scoring_base" / "artifacts" / "pair_aggregate_parts").glob("*.parquet"))
    assert not (
        tmp_path
        / "fast_scoring_base"
        / "artifacts"
        / "pair_aggregates"
        / "window_start=2024-04-23_window_end=2024-04-23.parquet"
    ).exists()


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
