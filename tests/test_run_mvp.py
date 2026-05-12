"""Tests for MVP pipeline orchestration."""

from dataclasses import dataclass
from pathlib import Path
from typing import cast

import polars as pl
import pytest

import ozon_similar_products.pipeline.run_mvp as run_mvp
from ozon_similar_products.data import schemas
from ozon_similar_products.data.frames import empty_contract_frame
from ozon_similar_products.output.lookup import SimilarItemsLookup


def test_window_bounds_returns_inclusive_range() -> None:
    """Window bounds should include the train_until_date day."""
    window_start, window_end = run_mvp._window_bounds("2026-05-10", 3)

    assert window_start == "2026-05-08"
    assert window_end == "2026-05-10"


@pytest.mark.parametrize("lookback_days", [0, -1, True])
def test_window_bounds_rejects_invalid_lookback_days(lookback_days: int) -> None:
    """lookback_days should be a positive integer, not bool/zero/negative."""
    with pytest.raises(ValueError, match="lookback_days"):
        run_mvp._window_bounds("2026-05-10", lookback_days)


def test_partition_raw_events_by_date_returns_sorted_partitions() -> None:
    """Raw events should be split into date partitions in ascending date order."""
    raw_events = pl.DataFrame(
        {
            "user_id": [1, 2, 3],
            "date": ["2026-05-03", "2026-05-01", "2026-05-03"],
            "timestamp": ["2026-05-03 10:00:00", "2026-05-01 10:00:00", "2026-05-03 11:00:00"],
            "action_type": ["view", "click", "favorite"],
            "widget_name": ["catalog", "catalog", "catalog"],
            "search_query": [None, None, None],
            "item_id": [10, 20, 30],
        }
    ).with_columns(
        pl.col("date").str.to_date(),
        pl.col("timestamp").str.to_datetime(),
    )

    partitions = run_mvp._partition_raw_events_by_date(raw_events)

    assert [partition_date for partition_date, _ in partitions] == ["2026-05-01", "2026-05-03"]
    assert partitions[0][1].height == 1
    assert partitions[1][1].height == 2


def test_run_mvp_pipeline_handles_missing_raw_events_and_uses_calibration_shares(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Pipeline should run on missing raw data and still derive calibration shares."""
    captured: dict[str, object] = {}

    config = {
        "pipeline": {"top_k": 20},
        "events": {"item_action_types": ["view", "click", "favorite", "to_cart"]},
        "topk": {
            "top_k": 7,
            "source": "behavioral",
            "min_pair_count": 2,
            "min_unique_users": 3,
            "min_unique_sessions": 4,
        },
        "outputs": {
            "detailed_recommendations_dir": "outputs/recommendations/detailed",
            "widget_recommendations_dir": "outputs/recommendations/widget",
            "latest_dir": "outputs/recommendations/latest",
        },
    }

    def fake_load_events(**_: object) -> pl.DataFrame:
        raise FileNotFoundError("No files for selected date range")

    class FakeEventCleaner:
        def __init__(self, item_action_types: list[str]) -> None:
            self.item_action_types = item_action_types

        def transform_day(self, events: pl.DataFrame) -> pl.DataFrame:
            return events.select(schemas.CLEAN_EVENTS_COLUMNS)

    class FakeSessionBuilder:
        @classmethod
        def from_config(cls, _: dict[str, object]) -> "FakeSessionBuilder":
            return cls()

        def transform_window(self, daily_events_clean: list[pl.DataFrame]) -> pl.DataFrame:
            captured["session_window_input_count"] = len(daily_events_clean)
            return empty_contract_frame(schemas.SESSIONS_COLUMNS)

    class FakeItemPairBuilder:
        @classmethod
        def from_config(cls, _: dict[str, object]) -> "FakeItemPairBuilder":
            return cls()

        def transform_day(self, sessions: pl.DataFrame) -> pl.DataFrame:
            captured["pair_builder_sessions_height"] = sessions.height
            return empty_contract_frame(schemas.DAILY_ITEM_PAIRS_COLUMNS)

    class FakePairAggregator:
        def aggregate_window(
            self,
            daily_pairs: list[pl.DataFrame],
            window_start: str,
            window_end: str,
        ) -> pl.DataFrame:
            captured["daily_pairs_count"] = len(daily_pairs)
            captured["window"] = (window_start, window_end)
            return empty_contract_frame(schemas.PAIR_AGGREGATES_COLUMNS)

    class FakePopularityBuilder:
        def __init__(self, item_action_types: list[str]) -> None:
            self.item_action_types = item_action_types

        def build_item_popularity(self, _: pl.DataFrame) -> pl.DataFrame:
            return empty_contract_frame(schemas.ITEM_POPULARITY_COLUMNS)

        def build_action_type_calibration_stats(
            self,
            _: pl.DataFrame,
            calibration_start: str,
            calibration_end: str,
        ) -> pl.DataFrame:
            return pl.DataFrame(
                {
                    "action_type": ["view", "click"],
                    "events_count": [70, 30],
                    "event_share": [0.7, 0.3],
                    "unique_users": [10, 9],
                    "unique_items": [20, 15],
                    "calibration_start": [calibration_start, calibration_start],
                    "calibration_end": [calibration_end, calibration_end],
                }
            ).select(schemas.ACTION_TYPE_DISTRIBUTION_COLUMNS)

    @dataclass(frozen=True)
    class FakeScorer:
        action_shares: dict[str, float] | None = None
        normalize_by_item_popularity: bool = False
        method: str = "pair_count"

        def score(
            self,
            pair_aggregates: pl.DataFrame,
            item_popularity: pl.DataFrame | None = None,
        ) -> pl.DataFrame:
            captured["score_action_shares"] = self.action_shares
            captured["score_item_popularity_used"] = item_popularity is not None
            captured["pair_aggregates_height"] = pair_aggregates.height
            return empty_contract_frame(schemas.PAIR_SCORES_COLUMNS)

    class FakeScorerFactory:
        @staticmethod
        def from_config(_: dict[str, object]) -> FakeScorer:
            return FakeScorer()

    class FakeTopKSelector:
        def __init__(
            self,
            top_k: int,
            source: str,
            min_pair_count: int | None,
            min_unique_users: int | None,
            min_unique_sessions: int | None,
        ) -> None:
            captured["selector_kwargs"] = {
                "top_k": top_k,
                "source": source,
                "min_pair_count": min_pair_count,
                "min_unique_users": min_unique_users,
                "min_unique_sessions": min_unique_sessions,
            }

        def select(self, pair_scores: pl.DataFrame) -> pl.DataFrame:
            captured["pair_scores_height"] = pair_scores.height
            return empty_contract_frame(schemas.RECOMMENDATIONS_COLUMNS)

    class FakeRecommendationWriter:
        def save_detailed(self, _: pl.DataFrame, output_path: str | Path) -> Path:
            captured["detailed_output_dir"] = Path(output_path)
            return Path(output_path) / "recommendations.parquet"

        def save_widget_format(self, _: pl.DataFrame, output_path: str | Path) -> Path:
            captured["widget_output_dir"] = Path(output_path)
            return Path(output_path) / "similar_items.parquet"

        def save_manifest(self, manifest: dict[str, object], output_path: str | Path) -> Path:
            captured["manifest"] = manifest
            return Path(output_path) / "manifest.json"

        def update_latest_manifest(self, run_manifest_path: str | Path, latest_dir: str | Path) -> Path:
            captured["run_manifest_path"] = Path(run_manifest_path)
            captured["latest_dir"] = Path(latest_dir)
            return Path(latest_dir) / "manifest.json"

    monkeypatch.setattr(run_mvp, "PROJECT_ROOT", tmp_path)
    monkeypatch.setattr(run_mvp, "load_yaml_config", lambda _: config)
    monkeypatch.setattr(run_mvp, "load_configs", lambda project_root: {"project_root": project_root})
    monkeypatch.setattr(run_mvp, "load_events", fake_load_events)
    monkeypatch.setattr(run_mvp, "EventCleaner", FakeEventCleaner)
    monkeypatch.setattr(run_mvp, "SessionBuilder", FakeSessionBuilder)
    monkeypatch.setattr(run_mvp, "ItemPairBuilder", FakeItemPairBuilder)
    monkeypatch.setattr(run_mvp, "PairAggregator", FakePairAggregator)
    monkeypatch.setattr(run_mvp, "ItemPopularityBuilder", FakePopularityBuilder)
    monkeypatch.setattr(run_mvp, "CoVisitationScorer", FakeScorerFactory)
    monkeypatch.setattr(run_mvp, "TopKSelector", FakeTopKSelector)
    monkeypatch.setattr(run_mvp, "RecommendationWriter", FakeRecommendationWriter)

    run_mvp.run_mvp_pipeline(train_until_date="2026-05-10", lookback_days=7)

    assert captured["session_window_input_count"] == 0
    assert captured["pair_builder_sessions_height"] == 0
    assert captured["daily_pairs_count"] == 0
    assert captured["window"] == ("2026-05-04", "2026-05-10")
    assert captured["score_action_shares"] == {"view": 0.7, "click": 0.3}
    assert captured["score_item_popularity_used"] is False
    assert captured["selector_kwargs"] == {
        "top_k": 7,
        "source": "behavioral",
        "min_pair_count": 2,
        "min_unique_users": 3,
        "min_unique_sessions": 4,
    }

    manifest = cast(dict[str, object], captured["manifest"])
    assert manifest["run_id"] == "run_2026-05-10_lb7"
    assert manifest["calibration_used"] is True
    assert cast(dict[str, int], manifest["rows"])["raw_events"] == 0
    assert cast(dict[str, str], manifest["paths"])["detailed_recommendations_path"] == "detailed/recommendations.parquet"
    assert cast(dict[str, str], manifest["paths"])["widget_recommendations_path"] == "widget/similar_items.parquet"

    assert cast(Path, captured["run_manifest_path"]).name == "manifest.json"
    assert cast(Path, captured["latest_dir"]).as_posix().endswith("outputs/recommendations/latest")


def _write_text(path: Path, content: str) -> None:
    """Write a UTF-8 text file and create its parent directory."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def _write_synthetic_action_partition(
    events_root: Path,
    partition_date: str,
    action_type: str,
    rows: list[dict[str, object]],
) -> None:
    """Write one Hive-style raw-events action partition for the smoke test."""
    partition_dir = events_root / f"date={partition_date}" / f"action_type={action_type}"
    partition_dir.mkdir(parents=True, exist_ok=True)
    pl.DataFrame(rows).write_parquet(partition_dir / "part-0.parquet")


def _write_smoke_project_configs(project_root: Path) -> Path:
    """Create minimal configs needed by run_mvp_pipeline inside tmp_path."""
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
  recommendations_dir: outputs/recommendations
  reports_dir: outputs/reports
  figures_dir: outputs/figures

source:
  package_dir: src/ozon_similar_products
  future_layer_dirs: []
  required_modules: []

project_dirs:
  - configs
  - data/raw/product_information
  - data/raw/user_actions
  - data/processed
  - outputs/recommendations
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
        """pipeline:
  session_timeout_minutes: 30
  max_items_per_session: 50
  top_k: 5
  lookback_days: 1

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
  sessions_dir: data/processed/sessions
  item_popularity_dir: data/processed/item_popularity
  action_type_distribution_dir: data/processed/action_type_distribution
  daily_pairs_dir: data/processed/item_pairs
  pair_aggregates_dir: data/processed/pair_aggregates

outputs:
  detailed_recommendations_dir: outputs/recommendations/detailed
  widget_recommendations_dir: outputs/recommendations/widget
  latest_dir: outputs/recommendations/latest
""",
    )
    return baseline_path


def _write_smoke_raw_events(project_root: Path) -> None:
    """Create a tiny raw-events parquet dataset that can produce recommendations."""
    events_root = project_root / "data" / "raw" / "user_actions" / "user_actions"
    partition_date = "2026-05-10"

    _write_synthetic_action_partition(
        events_root,
        partition_date,
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
            {
                "user_id": 3,
                "timestamp": "2026-05-10 12:00:00",
                "widget_name": "catalog",
                "search_query": None,
                "item_id": 2,
            },
        ],
    )
    _write_synthetic_action_partition(
        events_root,
        partition_date,
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
                "user_id": 3,
                "timestamp": "2026-05-10 12:05:00",
                "widget_name": "catalog",
                "search_query": None,
                "item_id": 20,
            },
        ],
    )
    _write_synthetic_action_partition(
        events_root,
        partition_date,
        "favorite",
        [
            {
                "user_id": 2,
                "timestamp": "2026-05-10 11:05:00",
                "widget_name": "catalog",
                "search_query": None,
                "item_id": 10,
            },
        ],
    )
    _write_synthetic_action_partition(
        events_root,
        partition_date,
        "to_cart",
        [
            {
                "user_id": 1,
                "timestamp": "2026-05-10 10:10:00",
                "widget_name": "catalog",
                "search_query": None,
                "item_id": 11,
            },
        ],
    )


def test_run_mvp_pipeline_smoke_with_synthetic_parquet_data(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Run the real MVP pipeline on a tiny synthetic parquet dataset."""
    baseline_path = _write_smoke_project_configs(tmp_path)
    _write_smoke_raw_events(tmp_path)
    monkeypatch.setattr(run_mvp, "PROJECT_ROOT", tmp_path)

    run_mvp.run_mvp_pipeline(
        train_until_date="2026-05-10",
        lookback_days=1,
        config_path=baseline_path,
    )

    latest_manifest_path = tmp_path / "outputs" / "recommendations" / "latest" / "manifest.json"
    assert latest_manifest_path.exists()

    detailed_outputs = list((tmp_path / "outputs" / "recommendations" / "runs").rglob("recommendations.parquet"))
    widget_outputs = list((tmp_path / "outputs" / "recommendations" / "runs").rglob("similar_items.parquet"))
    assert detailed_outputs
    assert widget_outputs

    detailed = pl.read_parquet(detailed_outputs[0])
    widget = pl.read_parquet(widget_outputs[0])
    assert detailed.height > 0
    assert widget.height > 0
    assert "weight_sum" not in detailed.columns
    assert "to_cart_count" in detailed.columns

    lookup = SimilarItemsLookup(latest_manifest_path)
    similar_items = lookup.get_similar_items(1, top_k=5)
    assert similar_items
    assert 10 in similar_items

    for artifact_dir in [
        "events_clean",
        "sessions",
        "item_pairs",
        "pair_aggregates",
        "item_popularity",
        "action_type_distribution",
    ]:
        assert list((tmp_path / "data" / "processed" / artifact_dir).rglob("*.parquet"))
