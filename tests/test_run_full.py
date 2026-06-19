"""Tests for full run orchestration helpers."""

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
    assert (latest / "recommendations" / "lookup.parquet").exists()
    assert (latest / "evaluation" / "metrics.json").exists()


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
        lambda **_: OfflineMetrics(to_cart_hit_rate_at_k=1.0),
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
