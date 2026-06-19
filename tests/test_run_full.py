"""Tests for full run orchestration helpers."""

import json
import logging
from datetime import date
from pathlib import Path

import polars as pl
import pytest

from ozon_similar_products.cli import run_full
from ozon_similar_products.evaluation.metrics import OfflineMetrics


def test_validation_window_is_computed_from_validation_days() -> None:
    start, end = run_full.validation_window(date(2024, 4, 23), 7)

    assert start == date(2024, 4, 24)
    assert end == date(2024, 4, 30)


def test_validation_window_rejects_non_positive_days() -> None:
    with pytest.raises(ValueError, match="validation_days"):
        run_full.validation_window(date(2024, 4, 23), 0)


def test_publish_latest_full_run_uses_flat_latest_layout(tmp_path: Path) -> None:
    run_dir = tmp_path / "outputs" / "runs" / "run_001"
    (run_dir / "recommendations").mkdir(parents=True)
    (run_dir / "evaluation").mkdir()

    pl.DataFrame({"item_id": [1], "similar_item_id": [2], "score": [1.0], "rank": [1], "source": ["behavioral"]}).write_parquet(
        run_dir / "recommendations" / "detailed.parquet"
    )
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


def test_validation_cache_creates_reuses_and_invalidates(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    config = {
        "outputs": {"root_dir": (tmp_path / "outputs").as_posix()},
        "events": {"item_action_types": ["view", "to_cart"]},
        "pipeline": {"session_timeout_minutes": 15, "max_items_per_session": 50},
        "item_pair_builder": {"signal_priority": {"view": 1, "to_cart": 2}},
    }
    build_calls = 0

    def fake_pair_counts(**_: object) -> pl.DataFrame:
        nonlocal build_calls
        build_calls += 1
        return pl.DataFrame(
            {
                "pair_date": [date(2024, 4, 24)],
                "item_id": [1],
                "similar_item_id": [2],
                "pair_count": [1],
                "view_count": [0],
                "click_count": [0],
                "favorite_count": [0],
                "to_cart_count": [1],
            }
        )

    monkeypatch.setattr(run_full, "_git_sha", lambda: "sha")
    monkeypatch.setattr(run_full, "_build_validation_pair_counts", fake_pair_counts)

    first = run_full.load_or_build_validation_cache(
        config=config,
        validation_start_date=date(2024, 4, 24),
        validation_end_date=date(2024, 4, 24),
        relevance_mode="binary",
        relevance_weights=None,
        logger=logging.getLogger(__name__),
    )
    second = run_full.load_or_build_validation_cache(
        config=config,
        validation_start_date=date(2024, 4, 24),
        validation_end_date=date(2024, 4, 24),
        relevance_mode="binary",
        relevance_weights=None,
        logger=logging.getLogger(__name__),
    )
    changed_window = run_full.load_or_build_validation_cache(
        config=config,
        validation_start_date=date(2024, 4, 25),
        validation_end_date=date(2024, 4, 25),
        relevance_mode="binary",
        relevance_weights=None,
        logger=logging.getLogger(__name__),
    )
    changed_config = run_full.load_or_build_validation_cache(
        config={**config, "pipeline": {"session_timeout_minutes": 30, "max_items_per_session": 50}},
        validation_start_date=date(2024, 4, 24),
        validation_end_date=date(2024, 4, 24),
        relevance_mode="binary",
        relevance_weights=None,
        logger=logging.getLogger(__name__),
    )

    assert build_calls == 3
    assert not first.cache_hit
    assert second.cache_hit
    assert not changed_window.cache_hit
    assert not changed_config.cache_hit
    assert (first.cache_dir / "validation_pair_counts.parquet").exists()
    assert (first.cache_dir / "ground_truth.parquet").exists()
    assert (first.cache_dir / "metadata.json").exists()


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
        lambda **_: pl.DataFrame(
            {
                "pair_date": [date(2024, 4, 24)],
                "item_id": [1],
                "similar_item_id": [2],
                "pair_count": [1],
                "view_count": [0],
                "click_count": [0],
                "favorite_count": [0],
                "to_cart_count": [1],
            }
        ),
    )
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
    manifest = (result.run_dir / "manifest.json").read_text(encoding="utf-8")
    assert "recommendations/detailed.parquet" in manifest
    assert "recommendations/lookup.parquet" in manifest
    assert "evaluation/metrics.json" in manifest

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


def test_execute_full_run_skips_validation_rebuild_when_ground_truth_is_provided(
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
        run_id = "run_prebuilt"
        run_dir = tmp_path / "runs" / "run_prebuilt"
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
    monkeypatch.setattr(run_full, "_build_validation_pair_counts", lambda **_: pytest.fail("validation rebuilt"))
    monkeypatch.setattr(run_full, "_find_item_popularity_artifact", lambda *_: None)
    monkeypatch.setattr(
        run_full,
        "compute_offline_metrics",
        lambda **_: OfflineMetrics(to_cart_hit_rate_at_k=1.0),
    )

    result = run_full.execute_full_run(
        train_until_date=date(2024, 4, 23),
        lookback_days=1,
        validation_days=1,
        top_k=1,
        config_path=config_path,
        run_id="run_prebuilt",
        run_dir=tmp_path / "runs" / "run_prebuilt",
        publish_latest=False,
        ground_truth=pl.DataFrame(
            {
                "item_id": [1],
                "relevant_item_id": [2],
                "relevance": [1.0],
                "target_action_type": ["to_cart"],
                "evidence_count": [1],
                "view_count": [0],
                "click_count": [0],
                "favorite_count": [0],
                "to_cart_count": [1],
            }
        ),
        used_validation_cache=True,
        validation_cache_key="cache-key",
        used_scoring_only_mode=True,
    )

    manifest = json.loads(result.evaluation_manifest_path.read_text(encoding="utf-8"))
    assert manifest["used_validation_cache"] is True
    assert manifest["used_scoring_only_mode"] is True
    assert manifest["validation_pair_counts_seconds"] == 0.0
