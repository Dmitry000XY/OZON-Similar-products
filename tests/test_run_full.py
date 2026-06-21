"""Tests for full run orchestration helpers."""

import json
import logging
from datetime import date
from pathlib import Path

import polars as pl
import pytest

from ozon_similar_products.cli import run_full
from ozon_similar_products.data import schemas
from ozon_similar_products.data.frames import empty_contract_frame
from ozon_similar_products.evaluation.metrics import OfflineMetrics
from ozon_similar_products.evaluation.validation_cache import (
    load_or_build_validation_cache,
    validation_cache_metadata,
)
from ozon_similar_products.evaluation.validation_semantics import (
    validation_ground_truth_config,
)


def _validation_pair_counts_frame(pair_date: date) -> pl.DataFrame:
    return pl.DataFrame(
        {
            "pair_date": [pair_date],
            "item_id": [1],
            "similar_item_id": [2],
            "pair_count": [1],
            "view_count": [0],
            "click_count": [0],
            "favorite_count": [0],
            "to_cart_count": [1],
        }
    )


def _graph_tuning_config(
    *,
    distance_enabled: bool,
    distance_strategy: str,
    max_distance: int | None,
    time_enabled: bool,
    time_strategy: str,
) -> dict[str, object]:
    return {
        "pipeline": {"session_timeout_minutes": 20, "max_items_per_session": 50},
        "events": {"item_action_types": ["view", "click", "favorite", "to_cart"]},
        "item_pair_builder": {
            "signal_priority": {"view": 1, "click": 2, "favorite": 3, "to_cart": 4}
        },
        "graph": {
            "distance_decay": {
                "enabled": distance_enabled,
                "strategy": distance_strategy,
                "max_distance": max_distance,
                "exponential": {"alpha": 1.0, "min_weight": 0.05},
            },
            "time_decay": {
                "enabled": time_enabled,
                "strategy": time_strategy,
                "exponential": {"half_life_days": 7, "min_weight": 0.05},
            },
        },
    }


def test_validation_window_is_computed_from_validation_days() -> None:
    start, end = run_full.validation_window(date(2024, 4, 23), 7)

    assert start == date(2024, 4, 24)
    assert end == date(2024, 4, 30)


def test_validation_window_rejects_non_positive_days() -> None:
    with pytest.raises(ValueError, match="validation_days"):
        run_full.validation_window(date(2024, 4, 23), 0)


def test_validation_ground_truth_config_disables_graph_decay() -> None:
    config = _graph_tuning_config(
        distance_enabled=True,
        distance_strategy="exponential",
        max_distance=1,
        time_enabled=True,
        time_strategy="exponential",
    )

    validation_config = validation_ground_truth_config(config)

    assert validation_config["graph"]["distance_decay"]["enabled"] is False
    assert validation_config["graph"]["distance_decay"]["strategy"] == "none"
    assert validation_config["graph"]["distance_decay"]["max_distance"] is None
    assert validation_config["graph"]["time_decay"]["enabled"] is False
    assert validation_config["graph"]["time_decay"]["strategy"] == "none"


def test_validation_cache_metadata_ignores_train_graph_decay() -> None:
    config_a = _graph_tuning_config(
        distance_enabled=False,
        distance_strategy="none",
        max_distance=None,
        time_enabled=False,
        time_strategy="none",
    )
    config_b = _graph_tuning_config(
        distance_enabled=True,
        distance_strategy="weight_table",
        max_distance=1,
        time_enabled=True,
        time_strategy="exponential",
    )

    metadata_a = validation_cache_metadata(
        config=config_a,
        validation_start_date=date(2024, 4, 24),
        validation_end_date=date(2024, 4, 24),
        relevance_mode="binary",
        relevance_weights=None,
        item_action_types=["view", "click", "favorite", "to_cart"],
        git_sha="sha",
    )
    metadata_b = validation_cache_metadata(
        config=config_b,
        validation_start_date=date(2024, 4, 24),
        validation_end_date=date(2024, 4, 24),
        relevance_mode="binary",
        relevance_weights=None,
        item_action_types=["view", "click", "favorite", "to_cart"],
        git_sha="sha",
    )

    assert metadata_a["config_hash"] == metadata_b["config_hash"]
    assert metadata_a["validation_pair_semantics"] == metadata_b["validation_pair_semantics"]


def test_publish_latest_full_run_uses_flat_latest_layout(tmp_path: Path) -> None:
    run_dir = tmp_path / "outputs" / "runs" / "run_001"
    (run_dir / "recommendations").mkdir(parents=True)
    (run_dir / "evaluation").mkdir()

    pl.DataFrame(
        {"item_id": [1], "similar_item_id": [2], "score": [1.0], "rank": [1], "source": ["behavioral"]}
    ).write_parquet(run_dir / "recommendations" / "detailed.parquet")
    pl.DataFrame(
        {
            "item_id": [1],
            "item_name": ["Item 1"],
            "similar_item_id": [2],
            "similar_item_name": ["Item 2"],
            "rank": [1],
            "score": [1.0],
            "source": ["behavioral"],
        }
    ).write_parquet(run_dir / "recommendations" / "enriched.parquet")
    pl.DataFrame({"item_id": [1], "similar_items_sku_list": [[2]]}).write_parquet(
        run_dir / "recommendations" / "lookup.parquet"
    )
    for filename in ("metrics.json", "scorecard.json", "evaluation_manifest.json"):
        (run_dir / "evaluation" / filename).write_text("{}", encoding="utf-8")

    run_full._publish_latest_full_run(
        run_dir,
        tmp_path / "outputs" / "latest",
        {
            "run_id": "run_001",
            "paths": {
                "detailed_recommendations_path": "recommendations/detailed.parquet",
                "widget_recommendations_path": "recommendations/lookup.parquet",
            },
        },
    )

    latest = tmp_path / "outputs" / "latest"
    assert (latest / "manifest.json").exists()
    assert (latest / "recommendations" / "detailed.parquet").exists()
    assert (latest / "recommendations" / "enriched.parquet").exists()
    assert (latest / "recommendations" / "lookup.parquet").exists()
    assert (latest / "evaluation" / "metrics.json").exists()
    assert "recommendations/enriched.parquet" in (latest / "manifest.json").read_text(
        encoding="utf-8"
    )


def test_find_item_popularity_artifact_requires_exact_window(tmp_path: Path) -> None:
    artifacts_dir = tmp_path / "item_popularity"
    artifacts_dir.mkdir()
    stale_path = artifacts_dir / "window_start=2024-04-20_window_end=2024-04-20.parquet"
    stale_path.write_text("stale", encoding="utf-8")

    actual = run_full._find_item_popularity_artifact(
        {"artifacts": {"item_popularity_dir": artifacts_dir.as_posix()}},
        "2024-04-21",
        "2024-04-21",
    )

    assert actual is None


def test_validation_cache_creates_reuses_and_invalidates(tmp_path: Path) -> None:
    config = {
        "events": {"item_action_types": ["view", "to_cart"]},
        "pipeline": {"session_timeout_minutes": 15, "max_items_per_session": 50},
        "item_pair_builder": {"signal_priority": {"view": 1, "to_cart": 2}},
    }
    build_calls = 0

    def fake_pair_counts() -> pl.DataFrame:
        nonlocal build_calls
        build_calls += 1
        return _validation_pair_counts_frame(date(2024, 4, 24))

    def make_metadata(start_date: date) -> dict[str, object]:
        return validation_cache_metadata(
            config=config,
            validation_start_date=start_date,
            validation_end_date=start_date,
            relevance_mode="binary",
            relevance_weights=None,
            item_action_types=["view", "to_cart"],
            git_sha="sha",
        )

    first = load_or_build_validation_cache(
        cache_root=tmp_path / "validation_cache",
        metadata=make_metadata(date(2024, 4, 24)),
        relevance_mode="binary",
        relevance_weights=None,
        build_validation_pair_counts=fake_pair_counts,
        logger=logging.getLogger(__name__),
    )
    second = load_or_build_validation_cache(
        cache_root=tmp_path / "validation_cache",
        metadata=make_metadata(date(2024, 4, 24)),
        relevance_mode="binary",
        relevance_weights=None,
        build_validation_pair_counts=fake_pair_counts,
        logger=logging.getLogger(__name__),
    )
    changed_window = load_or_build_validation_cache(
        cache_root=tmp_path / "validation_cache",
        metadata=make_metadata(date(2024, 4, 25)),
        relevance_mode="binary",
        relevance_weights=None,
        build_validation_pair_counts=fake_pair_counts,
        logger=logging.getLogger(__name__),
    )

    assert build_calls == 2
    assert not first.cache_hit
    assert second.cache_hit
    assert not changed_window.cache_hit
    assert (first.cache_dir / "validation_pair_counts.parquet").exists()
    assert (first.cache_dir / "ground_truth.parquet").exists()
    assert (first.cache_dir / "metadata.json").exists()


def test_build_validation_pair_counts_uses_stable_validation_semantics(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    config = _graph_tuning_config(
        distance_enabled=True,
        distance_strategy="exponential",
        max_distance=1,
        time_enabled=True,
        time_strategy="exponential",
    )
    captured: dict[str, object] = {}

    monkeypatch.setattr(run_full, "PROJECT_ROOT", tmp_path)
    monkeypatch.setattr(run_full, "load_configs", lambda project_root: {"project_root": project_root})
    monkeypatch.setattr(
        run_full,
        "load_events",
        lambda **_: pl.DataFrame(
            {
                "user_id": [1],
                "event_date": [date(2024, 4, 24)],
                "timestamp": [date(2024, 4, 24)],
                "action_type": ["view"],
                "item_id": [1],
                "search_query": [None],
                "widget_name": [None],
            }
        ),
    )

    class FakeCleaner:
        def __init__(self, item_action_types: list[str]) -> None:
            self.item_action_types = item_action_types

        def transform_day(self, raw_day: pl.DataFrame) -> pl.DataFrame:
            return raw_day

    class FakeSessionBuilder:
        @classmethod
        def from_config(cls, cfg: dict[str, object]) -> "FakeSessionBuilder":
            captured["session_config"] = cfg
            return cls()

        def transform_day(self, clean_day: pl.DataFrame) -> pl.DataFrame:
            return pl.DataFrame(
                {
                    "user_id": [1],
                    "session_index": [1],
                    "session_start_date": [date(2024, 4, 24)],
                    "event_date": [date(2024, 4, 24)],
                    "timestamp": [date(2024, 4, 24)],
                    "action_type": ["view"],
                    "item_id": [1],
                }
            )

    class FakePairBuilder:
        @classmethod
        def from_config(cls, cfg: dict[str, object]) -> "FakePairBuilder":
            captured["pair_config"] = cfg
            return cls()

        def build_daily_pair_stats(self, sessions_day: pl.DataFrame) -> object:
            return type(
                "Stats",
                (),
                {"counts": empty_contract_frame(schemas.DAILY_PAIR_COUNTS_COLUMNS)},
            )()

    monkeypatch.setattr(run_full, "EventCleaner", FakeCleaner)
    monkeypatch.setattr(run_full, "SessionBuilder", FakeSessionBuilder)
    monkeypatch.setattr(run_full, "ItemPairBuilder", FakePairBuilder)

    run_full._build_validation_pair_counts(
        config=config,
        validation_start_date=date(2024, 4, 24),
        validation_end_date=date(2024, 4, 24),
        logger=logging.getLogger(__name__),
    )

    pair_config = captured["pair_config"]
    assert isinstance(pair_config, dict)
    assert pair_config["graph"]["distance_decay"]["enabled"] is False
    assert pair_config["graph"]["distance_decay"]["strategy"] == "none"
    assert pair_config["graph"]["distance_decay"]["max_distance"] is None
    assert pair_config["graph"]["time_decay"]["enabled"] is False
    assert pair_config["graph"]["time_decay"]["strategy"] == "none"


def test_execute_full_run_writes_debug_artifacts_only_when_requested(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    config_path = tmp_path / "config.yaml"
    config_path.write_text("pipeline:\n  top_k: 1\n", encoding="utf-8")

    recommendations = pl.DataFrame(
        {
            "item_id": [1],
            "similar_item_id": [2],
            "score": [1.0],
            "rank": [1],
            "source": ["behavioral"],
        }
    )

    class FakePipelineResult:
        run_id = "run_001"
        run_dir = tmp_path / "runs" / "run_001"
        manifest_path = run_dir / "manifest.json"
        detailed_recommendations_path = run_dir / "recommendations" / "detailed.parquet"
        lookup_recommendations_path = run_dir / "recommendations" / "lookup.parquet"
        manifest = {
            "paths": {
                "detailed_recommendations_path": "recommendations/detailed.parquet",
                "widget_recommendations_path": "recommendations/lookup.parquet",
            },
            "window_start": "2024-04-23",
            "window_end": "2024-04-23",
        }

    def fake_run_pipeline(**_: object) -> FakePipelineResult:
        result = FakePipelineResult()
        result.detailed_recommendations_path.parent.mkdir(parents=True, exist_ok=True)
        recommendations.write_parquet(result.detailed_recommendations_path)
        pl.DataFrame({"item_id": [1], "similar_items_sku_list": [[2]]}).write_parquet(
            result.lookup_recommendations_path
        )
        return result

    monkeypatch.setattr(run_full, "run_pipeline", fake_run_pipeline)
    monkeypatch.setattr(
        run_full,
        "_build_validation_pair_counts",
        lambda **_: _validation_pair_counts_frame(date(2024, 4, 24)),
    )
    monkeypatch.setattr(run_full, "_validation_cache_root", lambda _: tmp_path / "cache")
    monkeypatch.setattr(run_full, "_find_item_popularity_artifact", lambda *_: None)
    monkeypatch.setattr(
        run_full,
        "compute_offline_metrics",
        lambda **_: OfflineMetrics(to_cart_hit_rate_at_k=1.0, recall_at_k=1.0),
    )

    result = run_full.execute_full_run(
        train_until_date=date(2024, 4, 23),
        lookback_days=1,
        validation_days=1,
        top_k=1,
        config_path=config_path,
        run_id="run_001",
        run_dir=tmp_path / "runs" / "run_001",
        keep_evaluation_artifacts=False,
        publish_latest=False,
    )

    assert result.metrics_path.exists()
    assert not (result.run_dir / "evaluation" / "debug").exists()
    saved_metrics = json.loads(result.metrics_path.read_text(encoding="utf-8"))
    saved_scorecard = json.loads(result.scorecard_path.read_text(encoding="utf-8"))
    assert saved_scorecard["metrics"] == saved_metrics
    manifest = json.loads((result.run_dir / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["used_validation_cache"] is True
    assert manifest["validation_cache_hit"] is False
    assert "recommendations/detailed.parquet" in json.dumps(manifest)
    assert "recommendations/lookup.parquet" in json.dumps(manifest)
    assert "evaluation/metrics.json" in json.dumps(manifest)

    debug_result = run_full.execute_full_run(
        train_until_date=date(2024, 4, 23),
        lookback_days=1,
        validation_days=1,
        top_k=1,
        config_path=config_path,
        run_id="run_002",
        run_dir=tmp_path / "runs" / "run_002",
        keep_evaluation_artifacts=True,
        publish_latest=False,
    )

    assert (debug_result.run_dir / "evaluation" / "debug" / "validation_pair_counts.parquet").exists()
    assert (debug_result.run_dir / "evaluation" / "debug" / "ground_truth.parquet").exists()
    debug_manifest = json.loads((debug_result.run_dir / "manifest.json").read_text(encoding="utf-8"))
    assert debug_manifest["validation_cache_hit"] is True
