"""Tests for incremental daily artifact reuse and idempotent pair stats."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import polars as pl
import pytest
import yaml

import ozon_similar_products.pipeline.run_pipeline as run_pipeline
from ozon_similar_products.data import schemas
from ozon_similar_products.retrieval.build_pairs import DailyPairStats


def _pair_stats(pair_count: int, weighted_pair_count: float) -> DailyPairStats:
    counts = pl.DataFrame(
        {
            "pair_date": ["2026-05-10"],
            "item_id": [1],
            "similar_item_id": [2],
            "pair_count": [pair_count],
            "view_count": [pair_count],
            "click_count": [0],
            "favorite_count": [0],
            "to_cart_count": [0],
            "weighted_pair_count": [weighted_pair_count],
            "weighted_view_count": [weighted_pair_count],
            "weighted_click_count": [0.0],
            "weighted_favorite_count": [0.0],
            "weighted_to_cart_count": [0.0],
        }
    ).select(schemas.DAILY_PAIR_COUNTS_COLUMNS)
    user_keys = pl.DataFrame(
        {
            "pair_date": ["2026-05-10"],
            "item_id": [1],
            "similar_item_id": [2],
            "user_id": [pair_count],
        }
    ).select(schemas.DAILY_PAIR_USER_KEYS_COLUMNS)
    session_keys = pl.DataFrame(
        {
            "pair_date": ["2026-05-10"],
            "item_id": [1],
            "similar_item_id": [2],
            "user_id": [pair_count],
            "session_index": [1],
        }
    ).select(schemas.DAILY_PAIR_SESSION_KEYS_COLUMNS)
    return DailyPairStats(
        counts=counts,
        user_keys=user_keys,
        session_keys=session_keys,
        raw_pair_rows=pair_count,
    )


def _write_text(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def _write_action_partition(
        events_root: Path,
        partition_date: str,
        action_type: str,
        rows: list[dict[str, Any]],
) -> None:
    partition_dir = events_root / f"date={partition_date}" / f"action_type={action_type}"
    partition_dir.mkdir(parents=True, exist_ok=True)
    pl.DataFrame(rows).write_parquet(partition_dir / "part-0.parquet")


def _write_project_configs(project_root: Path, *, update_strategy: str = "full_retrain") -> Path:
    _write_text(
        project_root / "configs" / "paths.yaml",
        """project:
  package_name: ozon_similar_products

configs:
  root_dir: configs
  paths: configs/paths.yaml
  data: configs/data.yaml
  baseline: configs/baseline.yaml

data:
  raw_dir: data/raw
  raw_archives_dir: data/raw/archives
  product_information_dir: data/raw/product_information
  user_actions_dir: data/raw/user_actions
  interim_dir: data/interim
  processed_dir: data/processed
  samples_dir: data/samples

outputs:
  root_dir: outputs
  recommendations_dir: outputs
  reports_dir: outputs/reports
  figures_dir: outputs/figures

source:
  package_dir: src/ozon_similar_products
  required_layer_dirs: []
  optional_future_layer_dirs: []
  required_modules: []

project_dirs:
  - configs
  - data/raw/product_information
  - data/raw/user_actions
  - data/processed
  - outputs
""",
    )
    _write_text(
        project_root / "configs" / "data.yaml",
        """product_information:
  archive_name: product_information.tar.gz
  parquet_glob: "**/*.parquet"
  payload_root_names:
    - product_information
  id_column: item_id
  expected_columns:
    - item_id
    - name
    - brand
    - type
    - category_id
    - category_name

user_actions:
  archive_name: user_actions.tar.gz
  parquet_glob: "**/*.parquet"
  payload_root_names:
    - user_actions
  expected_columns:
    - user_id
    - date
    - timestamp
    - action_type
    - widget_name
    - search_query
    - item_id
  known_action_types:
    - search
    - view
    - click
    - to_cart
    - favorite

raw_data:
  success_marker_name: _SUCCESS
""",
    )
    baseline_path = project_root / "configs" / "baseline.yaml"
    _write_text(
        baseline_path,
        f"""pipeline:
  session_timeout_minutes: 30
  max_items_per_session: 50
  top_k: 5
  lookback_days: 1
  update_strategy: {update_strategy}
  session_user_buckets: 2
  session_batch_size: 10
  aggregation_item_buckets: 1

events:
  item_action_types:
    - view
    - click
    - favorite
    - to_cart

item_pair_builder:
  signal_priority:
    view: 1
    click: 2
    favorite: 3
    to_cart: 4

scoring:
  method: calibrated_multichannel
  count_source: raw
  count_transform:
    method: log
    smoothing: 1.0
  business_weights:
    view: 1.0
    click: 3.0
    favorite: 6.0
    to_cart: 8.0
  beta: 0.5
  reference_action_type: view
  max_frequency_boost:
    view: 1.0
    click: 10.0
    favorite: 15.0
    to_cart: 30.0
  min_pair_count: 1
  min_unique_users: 1
  min_unique_sessions: 1
  min_weighted_pair_count: null
  min_score: null
  calibration:
    action_shares_used_for_calibration: null
    calibration_start: null
    calibration_end: null
  normalize_by_item_popularity: false
  popularity_normalization:
    popularity_column: unique_users
    smoothing: 1.0
    power: 0.5

artifacts:
  events_clean_dir: data/processed/events_clean
  sessions_dir: null
  session_state_dir: data/processed/session_state
  item_popularity_dir: data/processed/item_popularity
  action_type_distribution_dir: data/processed/action_type_distribution
  daily_pairs_dir: data/processed/item_pairs
  pair_aggregates_dir: data/processed/pair_aggregates

outputs:
  root_dir: outputs
  latest_dir: outputs/latest
""",
    )
    return baseline_path


def _write_raw_events(project_root: Path) -> None:
    events_root = project_root / "data" / "raw" / "user_actions" / "user_actions"
    _write_action_partition(
        events_root,
        "2026-05-10",
        "view",
        [
            {
                "user_id": 1,
                "timestamp": "2026-05-10 10:00:00",
                "widget_name": "catalog",
                "search_query": None,
                "item_id": 1,
            },
            {
                "user_id": 2,
                "timestamp": "2026-05-10 11:00:00",
                "widget_name": "catalog",
                "search_query": None,
                "item_id": 1,
            },
        ],
    )
    _write_action_partition(
        events_root,
        "2026-05-10",
        "click",
        [
            {
                "user_id": 1,
                "timestamp": "2026-05-10 10:05:00",
                "widget_name": "catalog",
                "search_query": None,
                "item_id": 10,
            },
            {
                "user_id": 2,
                "timestamp": "2026-05-10 11:05:00",
                "widget_name": "catalog",
                "search_query": None,
                "item_id": 10,
            },
        ],
    )


def _write_products(project_root: Path) -> None:
    products_dir = project_root / "data" / "raw" / "product_information" / "product_information"
    products_dir.mkdir(parents=True, exist_ok=True)
    pl.DataFrame(
        {
            "item_id": [1, 10],
            "name": ["Item 1", "Item 10"],
            "brand": ["brand", "brand"],
            "type": ["type", "type"],
            "category_id": [100, 100],
            "category_name": ["category", "category"],
        }
    ).write_parquet(products_dir / "part-0.parquet")


def _run_pipeline_fixture(
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: Path,
        *,
        update_strategy: str,
        run_id: str,
) -> run_pipeline.PipelineRunResult:
    config_path = _write_project_configs(tmp_path, update_strategy=update_strategy)
    monkeypatch.setattr(run_pipeline, "PROJECT_ROOT", tmp_path)
    return run_pipeline.run_pipeline(
        train_until_date="2026-05-10",
        lookback_days=1,
        config_path=config_path,
        run_id=run_id,
        update_latest=False,
    )


def test_daily_pair_stats_first_write_overwrites_then_internal_merge(tmp_path: Path) -> None:
    output_dir = tmp_path / "item_pairs"
    run_pipeline._write_daily_pair_stats(
        stats=_pair_stats(pair_count=100, weighted_pair_count=100.0),
        partition_date="2026-05-10",
        output_dir=output_dir,
        merge_existing=False,
    )

    run_pipeline._write_daily_pair_stats(
        stats=_pair_stats(pair_count=1, weighted_pair_count=1.0),
        partition_date="2026-05-10",
        output_dir=output_dir,
        merge_existing=False,
    )
    counts = pl.read_parquet(output_dir / "counts" / "date=2026-05-10.parquet")
    assert counts["pair_count"].sum() == 1
    assert counts["weighted_pair_count"].sum() == pytest.approx(1.0)

    run_pipeline._write_daily_pair_stats(
        stats=_pair_stats(pair_count=2, weighted_pair_count=2.0),
        partition_date="2026-05-10",
        output_dir=output_dir,
        merge_existing=True,
    )
    counts = pl.read_parquet(output_dir / "counts" / "date=2026-05-10.parquet")
    assert counts["pair_count"].sum() == 3
    assert counts["weighted_pair_count"].sum() == pytest.approx(3.0)


def test_repeated_full_retrain_does_not_double_count(
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: Path,
) -> None:
    _write_raw_events(tmp_path)
    _write_products(tmp_path)

    first = _run_pipeline_fixture(
        monkeypatch,
        tmp_path,
        update_strategy="full_retrain",
        run_id="full-first",
    )
    first_counts = pl.read_parquet(
        tmp_path / "data" / "processed" / "item_pairs" / "counts" / "date=2026-05-10.parquet"
    )
    first_recommendations = pl.read_parquet(first.detailed_recommendations_path)

    second = _run_pipeline_fixture(
        monkeypatch,
        tmp_path,
        update_strategy="full_retrain",
        run_id="full-second",
    )
    second_counts = pl.read_parquet(
        tmp_path / "data" / "processed" / "item_pairs" / "counts" / "date=2026-05-10.parquet"
    )
    second_recommendations = pl.read_parquet(second.detailed_recommendations_path)

    assert second_counts["pair_count"].sum() == first_counts["pair_count"].sum()
    assert second_counts["weighted_pair_count"].sum() == pytest.approx(
        first_counts["weighted_pair_count"].sum()
    )
    assert second.manifest["rows"] == first.manifest["rows"]
    assert second_recommendations.sort(["item_id", "rank"]).equals(
        first_recommendations.sort(["item_id", "rank"])
    )


def test_incremental_reuses_valid_artifacts_and_matches_full_retrain(
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: Path,
) -> None:
    _write_raw_events(tmp_path)
    _write_products(tmp_path)

    full = _run_pipeline_fixture(
        monkeypatch,
        tmp_path,
        update_strategy="full_retrain",
        run_id="full",
    )
    incremental = _run_pipeline_fixture(
        monkeypatch,
        tmp_path,
        update_strategy="incremental",
        run_id="incremental",
    )

    full_recommendations = pl.read_parquet(full.detailed_recommendations_path)
    incremental_recommendations = pl.read_parquet(incremental.detailed_recommendations_path)
    assert incremental_recommendations.sort(["item_id", "rank"]).equals(
        full_recommendations.sort(["item_id", "rank"])
    )
    assert incremental.manifest["incremental"]["reused_clean_event_days"] == ["2026-05-10"]
    assert incremental.manifest["incremental"]["reused_pair_stat_days"] == ["2026-05-10"]
    assert incremental.manifest["incremental"]["rebuilt_pair_stat_days"] == []


def test_missing_pair_artifact_rebuilds_conservatively(
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: Path,
) -> None:
    _write_raw_events(tmp_path)
    _write_products(tmp_path)
    _run_pipeline_fixture(monkeypatch, tmp_path, update_strategy="full_retrain", run_id="full")

    missing_path = (
        tmp_path / "data" / "processed" / "item_pairs" / "user_keys" / "date=2026-05-10.parquet"
    )
    missing_path.unlink()

    incremental = _run_pipeline_fixture(
        monkeypatch,
        tmp_path,
        update_strategy="incremental",
        run_id="missing-rebuild",
    )

    assert missing_path.exists()
    assert incremental.manifest["incremental"]["earliest_affected_date"] == "2026-05-10"
    assert incremental.manifest["incremental"]["rebuilt_pair_stat_days"] == ["2026-05-10"]


def test_relevant_config_change_invalidates_pair_artifacts(
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: Path,
) -> None:
    _write_raw_events(tmp_path)
    _write_products(tmp_path)
    _run_pipeline_fixture(monkeypatch, tmp_path, update_strategy="full_retrain", run_id="full")

    config_path = _write_project_configs(tmp_path, update_strategy="incremental")
    config = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    config["pipeline"]["session_timeout_minutes"] = 5
    config_path.write_text(yaml.safe_dump(config, sort_keys=False), encoding="utf-8")
    monkeypatch.setattr(run_pipeline, "PROJECT_ROOT", tmp_path)

    incremental = run_pipeline.run_pipeline(
        train_until_date="2026-05-10",
        lookback_days=1,
        config_path=config_path,
        run_id="invalidated",
        update_latest=False,
    )

    assert incremental.manifest["incremental"]["reused_pair_stat_days"] == []
    assert incremental.manifest["incremental"]["rebuilt_pair_stat_days"] == ["2026-05-10"]


def test_scoring_only_config_change_reuses_daily_artifacts(
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: Path,
) -> None:
    _write_raw_events(tmp_path)
    _write_products(tmp_path)
    _run_pipeline_fixture(monkeypatch, tmp_path, update_strategy="full_retrain", run_id="full")

    config_path = _write_project_configs(tmp_path, update_strategy="incremental")
    config = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    config["scoring"]["business_weights"]["view"] = 2.0
    config["scoring"]["count_transform"]["method"] = "sqrt"
    config_path.write_text(yaml.safe_dump(config, sort_keys=False), encoding="utf-8")
    monkeypatch.setattr(run_pipeline, "PROJECT_ROOT", tmp_path)

    incremental = run_pipeline.run_pipeline(
        train_until_date="2026-05-10",
        lookback_days=1,
        config_path=config_path,
        run_id="scoring-only",
        update_latest=False,
    )

    assert incremental.manifest["incremental"]["reused_clean_event_days"] == ["2026-05-10"]
    assert incremental.manifest["incremental"]["reused_pair_stat_days"] == ["2026-05-10"]
    assert incremental.manifest["incremental"]["rebuilt_pair_stat_days"] == []


def test_pair_artifact_with_future_processed_through_date_rebuilds(
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: Path,
) -> None:
    _write_raw_events(tmp_path)
    _write_products(tmp_path)
    config_path = _write_project_configs(tmp_path, update_strategy="full_retrain")
    monkeypatch.setattr(run_pipeline, "PROJECT_ROOT", tmp_path)
    run_pipeline.run_pipeline(
        train_until_date="2026-05-10",
        lookback_days=1,
        config_path=config_path,
        run_id="full",
        update_latest=False,
    )

    manifest_path = (
        tmp_path
        / "data"
        / "processed"
        / "item_pairs"
        / "manifests"
        / "date=2026-05-10.json"
    )
    manifest = run_pipeline.read_manifest(manifest_path)
    assert manifest is not None
    future_cutoff_manifest = run_pipeline.ArtifactManifest(
        artifact_type=manifest.artifact_type,
        date=manifest.date,
        fingerprint=run_pipeline._daily_pair_stats_fingerprint(
            config=yaml.safe_load(config_path.read_text(encoding="utf-8")),
            action_types=["view", "click", "favorite", "to_cart"],
            partition_date="2026-05-10",
            processed_through_date="2026-05-11",
        ),
        paths=manifest.paths,
        rows=manifest.rows,
        metadata={"processed_through_date": "2026-05-11"},
    )
    run_pipeline.write_manifest(manifest_path, future_cutoff_manifest)

    incremental_config_path = _write_project_configs(tmp_path, update_strategy="incremental")
    incremental = run_pipeline.run_pipeline(
        train_until_date="2026-05-10",
        lookback_days=1,
        config_path=incremental_config_path,
        run_id="future-cutoff-rebuild",
        update_latest=False,
    )

    assert incremental.manifest["incremental"]["reused_pair_stat_days"] == []
    assert incremental.manifest["incremental"]["rebuilt_pair_stat_days"] == ["2026-05-10"]
    assert incremental.manifest["incremental"]["earliest_affected_date"] == "2026-05-10"
    rebuilt_manifest = run_pipeline.read_manifest(manifest_path)
    assert rebuilt_manifest is not None
    assert rebuilt_manifest.metadata["processed_through_date"] == "2026-05-10"


def test_pair_artifact_with_matching_processed_through_date_is_reused(
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: Path,
) -> None:
    _write_raw_events(tmp_path)
    _write_products(tmp_path)
    _run_pipeline_fixture(monkeypatch, tmp_path, update_strategy="full_retrain", run_id="full")

    manifest_path = (
        tmp_path
        / "data"
        / "processed"
        / "item_pairs"
        / "manifests"
        / "date=2026-05-10.json"
    )
    manifest = run_pipeline.read_manifest(manifest_path)
    assert manifest is not None
    assert manifest.metadata["processed_through_date"] == "2026-05-10"

    incremental = _run_pipeline_fixture(
        monkeypatch,
        tmp_path,
        update_strategy="incremental",
        run_id="matching-cutoff",
    )

    assert incremental.manifest["incremental"]["reused_pair_stat_days"] == ["2026-05-10"]
    assert incremental.manifest["incremental"]["rebuilt_pair_stat_days"] == []


def test_update_strategy_is_not_tuned() -> None:
    for path in Path("configs/tuning").glob("*.yaml"):
        payload = yaml.safe_load(path.read_text(encoding="utf-8"))
        assert "update_strategy" not in yaml.safe_dump(payload)
