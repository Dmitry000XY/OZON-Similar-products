"""Tests for pipeline pipeline orchestration."""

import logging
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import cast

import polars as pl
import pytest

import ozon_similar_products.pipeline.run_pipeline as run_pipeline
from ozon_similar_products.data import schemas
from ozon_similar_products.data.frames import empty_contract_frame
from ozon_similar_products.output.lookup import SimilarItemsLookup


def test_window_bounds_returns_inclusive_range() -> None:
    """Window bounds should include the train_until_date day."""
    window_start, window_end = run_pipeline._window_bounds("2026-05-10", 3)

    assert window_start == "2026-05-08"
    assert window_end == "2026-05-10"


def test_date_range_strings_returns_inclusive_dates() -> None:
    assert run_pipeline._date_range_strings("2026-05-08", "2026-05-10") == [
        "2026-05-08",
        "2026-05-09",
        "2026-05-10",
    ]


def test_date_range_strings_rejects_reversed_window() -> None:
    with pytest.raises(ValueError, match="less than or equal"):
        run_pipeline._date_range_strings("2026-05-10", "2026-05-08")


def test_scan_parquet_paths_or_empty_frame_returns_empty_contract_for_no_paths() -> None:
    frame = run_pipeline._scan_parquet_paths_or_empty_frame(
        [],
        schemas.CLEAN_EVENTS_COLUMNS,
    )

    assert isinstance(frame, pl.DataFrame)
    assert frame.is_empty()
    assert frame.columns == schemas.CLEAN_EVENTS_COLUMNS


@pytest.mark.parametrize("lookback_days", [0, -1, True])
def test_window_bounds_rejects_invalid_lookback_days(lookback_days: int) -> None:
    """lookback_days should be a positive integer, not bool/zero/negative."""
    with pytest.raises(ValueError, match="lookback_days"):
        run_pipeline._window_bounds("2026-05-10", lookback_days)


def test_item_action_types_accepts_string_value() -> None:
    """Single string action type should be normalized into one-item list."""
    config = {"events": {"item_action_types": "view"}}

    assert run_pipeline._item_action_types(config) == ["view"]


def test_item_action_types_accepts_list_value() -> None:
    """List of action types should pass through unchanged."""
    config = {"events": {"item_action_types": ["view", "click"]}}

    assert run_pipeline._item_action_types(config) == ["view", "click"]


def test_partition_sessions_by_session_start_date_keeps_cross_midnight_session_together() -> None:
    sessions = pl.DataFrame(
        {
            "user_id": [1, 1],
            "session_index": [1, 1],
            "session_start_date": [date(2026, 5, 10), date(2026, 5, 10)],
            "event_date": [date(2026, 5, 10), date(2026, 5, 11)],
            "timestamp": [
                "2026-05-10 23:55:00",
                "2026-05-11 00:05:00",
            ],
            "action_type": ["view", "click"],
            "item_id": [10, 20],
        }
    ).with_columns(
        pl.col("timestamp").str.to_datetime(),
    )

    partitions = run_pipeline._partition_sessions_by_session_start_date(sessions)

    assert len(partitions) == 1
    assert partitions[0][0] == "2026-05-10"
    assert partitions[0][1]["item_id"].to_list() == [10, 20]
    assert partitions[0][1].columns == schemas.SESSIONS_COLUMNS


def test_item_action_types_rejects_unknown_or_invalid_values() -> None:
    """Action types must be non-empty known strings."""
    with pytest.raises(ValueError, match="Unknown action type"):
        run_pipeline._item_action_types({"events": {"item_action_types": ["view", "unknown"]}})

    with pytest.raises(ValueError, match="non-empty strings"):
        run_pipeline._item_action_types({"events": {"item_action_types": ["view", ""]}})

    with pytest.raises(ValueError, match="non-empty strings"):
        run_pipeline._item_action_types({"events": {"item_action_types": ["view", 1]}})


def test_run_pipeline_raises_on_missing_raw_events_by_default(
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: Path,
) -> None:
    """Pipeline should fail loudly when input files are missing and empty input is disallowed."""
    config = {
        "pipeline": {"top_k": 20},
        "events": {"item_action_types": ["view", "click", "favorite", "to_cart"]},
    }

    def fake_load_events(**_: object) -> pl.DataFrame:
        raise FileNotFoundError("No files for selected date range")

    monkeypatch.setattr(run_pipeline, "PROJECT_ROOT", tmp_path)
    monkeypatch.setattr(run_pipeline, "load_yaml_config", lambda _: config)
    monkeypatch.setattr(run_pipeline, "load_configs", lambda project_root: {"project_root": project_root})
    monkeypatch.setattr(run_pipeline, "load_events", fake_load_events)

    with pytest.raises(FileNotFoundError, match=r"date_window=\[2026-05-04\.\.2026-05-10\]"):
        run_pipeline.run_pipeline(train_until_date="2026-05-10", lookback_days=7)


# TODO: fix it
# def test_run_pipeline_allows_missing_raw_events_when_configured_and_uses_calibration_shares(
#     monkeypatch: pytest.MonkeyPatch,
#     tmp_path: Path,
# ) -> None:
#     """Pipeline should run on missing raw data and still derive calibration shares."""
#     captured: dict[str, object] = {}
#
#     config = {
#         "pipeline": {"top_k": 20, "allow_empty_input": True},
#         "events": {"item_action_types": ["view", "click", "favorite", "to_cart"]},
#         "topk": {
#             "top_k": 7,
#             "source": "behavioral",
#             "min_pair_count": 2,
#             "min_unique_users": 3,
#             "min_unique_sessions": 4,
#         },
#         "outputs": {
#             "detailed_recommendations_dir": "outputs/detailed",
#             "widget_recommendations_dir": "outputs/widget",
#             "latest_dir": "outputs/latest",
#         },
#     }
#
#     def fake_load_events(**_: object) -> pl.DataFrame:
#         raise FileNotFoundError("No files for selected date range")
#
#     class FakeEventCleaner:
#         def __init__(self, item_action_types: list[str]) -> None:
#             self.item_action_types = item_action_types
#
#         def transform_day(self, events: pl.DataFrame) -> pl.DataFrame:
#             return events.select(schemas.CLEAN_EVENTS_COLUMNS)
#
#     class FakeSessionBuilder:
#         @classmethod
#         def from_config(cls, _: dict[str, object]) -> "FakeSessionBuilder":
#             return cls()
#
#         def transform_window(self, daily_events_clean: list[pl.DataFrame]) -> pl.DataFrame:
#             captured["session_window_input_count"] = len(daily_events_clean)
#             return empty_contract_frame(schemas.SESSIONS_COLUMNS)
#
#     class FakeItemPairBuilder:
#         @classmethod
#         def from_config(cls, _: dict[str, object]) -> "FakeItemPairBuilder":
#             return cls()
#
#         def transform_day(self, sessions: pl.DataFrame) -> pl.DataFrame:
#             captured["pair_builder_sessions_height"] = sessions.height
#             return empty_contract_frame(schemas.DAILY_ITEM_PAIRS_COLUMNS)
#
#     class FakePairAggregator:
#         def aggregate_window(
#             self,
#             daily_pairs: list[pl.DataFrame],
#             window_start: str,
#             window_end: str,
#         ) -> pl.DataFrame:
#             captured["daily_pairs_count"] = len(daily_pairs)
#             captured["window"] = (window_start, window_end)
#             return empty_contract_frame(schemas.PAIR_AGGREGATES_COLUMNS)
#
#     class FakePopularityBuilder:
#         def __init__(self, item_action_types: list[str]) -> None:
#             self.item_action_types = item_action_types
#
#         def build_item_popularity(self, _: pl.DataFrame) -> pl.DataFrame:
#             return empty_contract_frame(schemas.ITEM_POPULARITY_COLUMNS)
#
#         def build_action_type_calibration_stats(
#             self,
#             _: pl.DataFrame,
#             calibration_start: str,
#             calibration_end: str,
#         ) -> pl.DataFrame:
#             return pl.DataFrame(
#                 {
#                     "action_type": ["view", "click"],
#                     "events_count": [70, 30],
#                     "event_share": [0.7, 0.3],
#                     "unique_users": [10, 9],
#                     "unique_items": [20, 15],
#                     "calibration_start": [calibration_start, calibration_start],
#                     "calibration_end": [calibration_end, calibration_end],
#                 }
#             ).select(schemas.ACTION_TYPE_DISTRIBUTION_COLUMNS)
#
#     @dataclass(frozen=True)
#     class FakeScorer:
#         action_shares: dict[str, float] | None = None
#         normalize_by_item_popularity: bool = False
#         method: str = "pair_count"
#
#         def score(
#             self,
#             pair_aggregates: pl.DataFrame,
#             item_popularity: pl.DataFrame | None = None,
#         ) -> pl.DataFrame:
#             captured["score_action_shares"] = self.action_shares
#             captured["score_item_popularity_used"] = item_popularity is not None
#             captured["pair_aggregates_height"] = pair_aggregates.height
#             return empty_contract_frame(schemas.PAIR_SCORES_COLUMNS)
#
#     class FakeScorerFactory:
#         @staticmethod
#         def from_config(_: dict[str, object]) -> FakeScorer:
#             return FakeScorer()
#
#     class FakeTopKSelector:
#         def __init__(
#             self,
#             top_k: int,
#             source: str,
#             min_pair_count: int | None,
#             min_unique_users: int | None,
#             min_unique_sessions: int | None,
#         ) -> None:
#             captured["selector_kwargs"] = {
#                 "top_k": top_k,
#                 "source": source,
#                 "min_pair_count": min_pair_count,
#                 "min_unique_users": min_unique_users,
#                 "min_unique_sessions": min_unique_sessions,
#             }
#
#         def select(self, pair_scores: pl.DataFrame) -> pl.DataFrame:
#             captured["pair_scores_height"] = pair_scores.height
#             return empty_contract_frame(schemas.RECOMMENDATIONS_COLUMNS)
#
#     class FakeRecommendationWriter:
#         def save_detailed(self, _: pl.DataFrame, output_path: str | Path) -> Path:
#             captured["detailed_output_dir"] = Path(output_path)
#             return Path(output_path) / "detailed.parquet"
#
#         def save_widget_format(self, _: pl.DataFrame, output_path: str | Path) -> Path:
#             captured["widget_output_dir"] = Path(output_path)
#             return Path(output_path) / "lookup.parquet"
#
#         def save_manifest(self, manifest: dict[str, object], output_path: str | Path) -> Path:
#             captured["manifest"] = manifest
#             return Path(output_path) / "manifest.json"
#
#         def update_latest_manifest(self, run_manifest_path: str | Path, latest_dir: str | Path) -> Path:
#             captured["run_manifest_path"] = Path(run_manifest_path)
#             captured["latest_dir"] = Path(latest_dir)
#             return Path(latest_dir) / "manifest.json"
#
#     monkeypatch.setattr(run_pipeline, "PROJECT_ROOT", tmp_path)
#     monkeypatch.setattr(run_pipeline, "load_yaml_config", lambda _: config)
#     monkeypatch.setattr(run_pipeline, "load_configs", lambda project_root: {"project_root": project_root})
#     monkeypatch.setattr(run_pipeline, "load_events", fake_load_events)
#     monkeypatch.setattr(run_pipeline, "EventCleaner", FakeEventCleaner)
#     monkeypatch.setattr(run_pipeline, "SessionBuilder", FakeSessionBuilder)
#     monkeypatch.setattr(run_pipeline, "ItemPairBuilder", FakeItemPairBuilder)
#     monkeypatch.setattr(run_pipeline, "PairAggregator", FakePairAggregator)
#     monkeypatch.setattr(run_pipeline, "ItemPopularityBuilder", FakePopularityBuilder)
#     monkeypatch.setattr(run_pipeline, "CoVisitationScorer", FakeScorerFactory)
#     monkeypatch.setattr(run_pipeline, "TopKSelector", FakeTopKSelector)
#     monkeypatch.setattr(run_pipeline, "RecommendationWriter", FakeRecommendationWriter)
#
#     run_pipeline.run_pipeline(train_until_date="2026-05-10", lookback_days=7)
#
#     assert captured["session_window_input_count"] == 0
#     assert captured["pair_builder_sessions_height"] == 0
#     assert captured["daily_pairs_count"] == 0
#     assert captured["window"] == ("2026-05-04", "2026-05-10")
#     assert captured["score_action_shares"] == {"view": 0.7, "click": 0.3}
#     assert captured["score_item_popularity_used"] is False
#     assert captured["selector_kwargs"] == {
#         "top_k": 7,
#         "source": "behavioral",
#         "min_pair_count": 2,
#         "min_unique_users": 3,
#         "min_unique_sessions": 4,
#     }
#
#     manifest = cast(dict[str, object], captured["manifest"])
#     assert manifest["run_id"] == "run_2026-05-10_lb7"
#     assert manifest["calibration_used"] is True
#     assert cast(dict[str, int], manifest["rows"])["raw_events"] == 0
#     assert cast(dict[str, str], manifest["paths"])["detailed_recommendations_path"] == "detailed/detailed.parquet"
#     assert cast(dict[str, str], manifest["paths"])["widget_recommendations_path"] == "widget/lookup.parquet"
#
#     assert cast(Path, captured["run_manifest_path"]).name == "manifest.json"
#     assert "latest_dir" not in captured


def test_run_pipeline_updates_latest_with_empty_recommendations_only_when_allowed(
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: Path,
) -> None:
    """Latest should update for empty recommendations only with explicit opt-in flag."""
    captured: dict[str, object] = {}
    config = {
        "pipeline": {"allow_empty_input": True, "allow_empty_latest_update": True},
        "events": {"item_action_types": ["view"]},
    }

    def fake_load_events(**_: object) -> pl.DataFrame:
        raise FileNotFoundError("No files for selected date range")

    class EmptyTransform:
        @classmethod
        def from_config(cls, _: dict[str, object]) -> "EmptyTransform":
            return cls()

        def transform_window(self, _: list[pl.DataFrame]) -> pl.DataFrame:
            return empty_contract_frame(schemas.SESSIONS_COLUMNS)

        def transform_day(self, _: pl.DataFrame) -> pl.DataFrame:
            return empty_contract_frame(schemas.SESSIONS_COLUMNS)

        def build_daily_pair_stats(self, _: pl.DataFrame) -> run_pipeline.DailyPairStats:
            return run_pipeline.DailyPairStats(
                counts=empty_contract_frame(schemas.DAILY_PAIR_COUNTS_COLUMNS),
                user_keys=empty_contract_frame(schemas.DAILY_PAIR_USER_KEYS_COLUMNS),
                session_keys=empty_contract_frame(schemas.DAILY_PAIR_SESSION_KEYS_COLUMNS),
                raw_pair_rows=0,
            )

    class FakeEventCleaner:
        def __init__(self, item_action_types: list[str]) -> None:
            self.item_action_types = item_action_types

        def transform_day(self, events: pl.DataFrame) -> pl.DataFrame:
            return events.select(schemas.CLEAN_EVENTS_COLUMNS)

    class FakeScorer:
        action_shares: dict[str, float] | None = None
        normalize_by_item_popularity = False
        method = "pair_count"

        @staticmethod
        def from_config(_: dict[str, object]) -> "FakeScorer":
            return FakeScorer()

        def score(
                self,
                pair_aggregates: pl.DataFrame,
                item_popularity: pl.DataFrame | None = None,
        ) -> pl.DataFrame:
            return empty_contract_frame(schemas.PAIR_SCORES_COLUMNS)

        def score_lazy(
                self,
                pair_aggregates: pl.DataFrame,
                item_popularity: pl.DataFrame | None = None,
        ) -> pl.LazyFrame:
            return self.score(pair_aggregates, item_popularity=item_popularity).lazy()

    class FakeWriter:
        def save_detailed(self, _: pl.DataFrame, output_path: str | Path) -> Path:
            path = Path(output_path) / "detailed.parquet"
            path.parent.mkdir(parents=True, exist_ok=True)
            _.write_parquet(path)
            return path

        def save_enriched(
                self,
                _: pl.DataFrame,
                products: pl.DataFrame,
                output_path: str | Path,
        ) -> Path:
            path = Path(output_path) / "enriched.parquet"
            path.parent.mkdir(parents=True, exist_ok=True)
            products.write_parquet(path)
            return path

        def save_widget_format(self, _: pl.DataFrame, output_path: str | Path) -> Path:
            path = Path(output_path) / "lookup.parquet"
            path.parent.mkdir(parents=True, exist_ok=True)
            pl.DataFrame({"item_id": [], "similar_items_sku_list": []}).write_parquet(path)
            return path

        def save_manifest(self, manifest: dict[str, object], output_path: str | Path) -> Path:
            captured["manifest_rows"] = cast(dict[str, int], manifest["rows"])
            captured["latest_dir"] = Path(output_path)
            path = Path(output_path) / "manifest.json"
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text("{}", encoding="utf-8")
            return path

    monkeypatch.setattr(run_pipeline, "PROJECT_ROOT", tmp_path)
    monkeypatch.setattr(run_pipeline, "load_yaml_config", lambda _: config)
    monkeypatch.setattr(run_pipeline, "load_configs", lambda project_root: {"project_root": project_root})
    monkeypatch.setattr(run_pipeline, "load_events", fake_load_events)
    monkeypatch.setattr(
        run_pipeline,
        "load_products",
        lambda *_a, **_k: pl.DataFrame({"item_id": [], "name": []}),
    )
    monkeypatch.setattr(run_pipeline, "EventCleaner", FakeEventCleaner)
    monkeypatch.setattr(run_pipeline, "SessionBuilder", EmptyTransform)
    monkeypatch.setattr(run_pipeline, "ItemPairBuilder", EmptyTransform)
    monkeypatch.setattr(
        run_pipeline,
        "PairAggregator",
        lambda: type(
            "A",
            (),
            {
                "aggregate_window_from_daily_stats_paths": (
                    lambda *_a, **_k: empty_contract_frame(schemas.PAIR_AGGREGATES_COLUMNS)
                )
            },
        )(),
    )
    monkeypatch.setattr(run_pipeline, "ItemPopularityBuilder", lambda item_action_types: type("P", (), {
        "build_item_popularity": lambda *_a, **_k: empty_contract_frame(schemas.ITEM_POPULARITY_COLUMNS),
        "build_action_type_calibration_stats": lambda *_a, **_k: empty_contract_frame(
            schemas.ACTION_TYPE_DISTRIBUTION_COLUMNS),
    })())
    monkeypatch.setattr(run_pipeline, "CoVisitationScorer", FakeScorer)
    monkeypatch.setattr(run_pipeline, "TopKSelector", lambda **_: type("S", (), {
        "select": lambda *_a, **_k: empty_contract_frame(schemas.RECOMMENDATIONS_COLUMNS)})())
    monkeypatch.setattr(run_pipeline, "RecommendationWriter", FakeWriter)

    run_pipeline.run_pipeline(train_until_date="2026-05-10", lookback_days=7)

    assert cast(dict[str, int], captured["manifest_rows"])["recommendations"] == 0
    assert cast(Path, captured["latest_dir"]).as_posix().endswith("outputs/latest")


def test_run_pipeline_logs_warnings_on_empty_input_and_output(
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: Path,
        caplog: pytest.LogCaptureFixture,
) -> None:
    """Pipeline should warn when input/output windows are empty."""
    config = {
        "pipeline": {"allow_empty_input": True},
        "events": {"item_action_types": ["view"]},
    }

    def fake_load_events(**_: object) -> pl.DataFrame:
        raise FileNotFoundError("No files for selected date range")

    class FakeEventCleaner:
        def __init__(self, item_action_types: list[str]) -> None:
            self.item_action_types = item_action_types

        def transform_day(self, events: pl.DataFrame) -> pl.DataFrame:
            return empty_contract_frame(schemas.CLEAN_EVENTS_COLUMNS)

    class FakeSessionBuilder:
        @classmethod
        def from_config(cls, _: dict[str, object]) -> "FakeSessionBuilder":
            return cls()

        def transform_window(self, _: list[pl.DataFrame]) -> pl.DataFrame:
            return empty_contract_frame(schemas.SESSIONS_COLUMNS)

        def transform_day(self, _: pl.DataFrame) -> pl.DataFrame:
            return empty_contract_frame(schemas.SESSIONS_COLUMNS)

    class FakeItemPairBuilder:
        @classmethod
        def from_config(cls, _: dict[str, object]) -> "FakeItemPairBuilder":
            return cls()

        def build_daily_pair_stats(self, _: pl.DataFrame) -> run_pipeline.DailyPairStats:
            return run_pipeline.DailyPairStats(
                counts=empty_contract_frame(schemas.DAILY_PAIR_COUNTS_COLUMNS),
                user_keys=empty_contract_frame(schemas.DAILY_PAIR_USER_KEYS_COLUMNS),
                session_keys=empty_contract_frame(schemas.DAILY_PAIR_SESSION_KEYS_COLUMNS),
                raw_pair_rows=0,
            )

    class FakePairAggregator:
        def aggregate_window_from_daily_stats_paths(
                self,
                count_paths: list[Path],
                user_key_paths: list[Path],
                session_key_paths: list[Path],
                window_start: str,
                window_end: str,
        ) -> pl.DataFrame:
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
            return empty_contract_frame(schemas.ACTION_TYPE_DISTRIBUTION_COLUMNS)

    class FakeScorer:
        action_shares: dict[str, float] | None = None
        normalize_by_item_popularity: bool = False
        method: str = "pair_count"

        @staticmethod
        def from_config(_: dict[str, object]) -> "FakeScorer":
            return FakeScorer()

        def score(
                self,
                pair_aggregates: pl.DataFrame,
                item_popularity: pl.DataFrame | None = None,
        ) -> pl.DataFrame:
            return empty_contract_frame(schemas.PAIR_SCORES_COLUMNS)

        def score_lazy(
                self,
                pair_aggregates: pl.DataFrame,
                item_popularity: pl.DataFrame | None = None,
        ) -> pl.LazyFrame:
            return self.score(pair_aggregates, item_popularity=item_popularity).lazy()

    class FakeTopKSelector:
        def __init__(self, **_: object) -> None:
            return None

        def select(self, pair_scores: pl.DataFrame) -> pl.DataFrame:
            return empty_contract_frame(schemas.RECOMMENDATIONS_COLUMNS)

    class FakeWriter:
        def save_detailed(self, _: pl.DataFrame, output_path: str | Path) -> Path:
            return Path(output_path) / "detailed.parquet"

        def save_enriched(self, _: pl.DataFrame, products: pl.DataFrame, output_path: str | Path) -> Path:
            return Path(output_path) / "enriched.parquet"

        def save_widget_format(self, _: pl.DataFrame, output_path: str | Path) -> Path:
            return Path(output_path) / "lookup.parquet"

        def save_manifest(self, manifest: dict[str, object], output_path: str | Path) -> Path:
            return Path(output_path) / "manifest.json"

        def update_latest_manifest(self, run_manifest_path: str | Path, latest_dir: str | Path) -> Path:
            raise AssertionError("Latest manifest should not update for empty recommendations")

    monkeypatch.setattr(run_pipeline, "PROJECT_ROOT", tmp_path)
    monkeypatch.setattr(run_pipeline, "load_yaml_config", lambda _: config)
    monkeypatch.setattr(run_pipeline, "load_configs", lambda project_root: {"project_root": project_root})
    monkeypatch.setattr(run_pipeline, "load_events", fake_load_events)
    monkeypatch.setattr(
        run_pipeline,
        "load_products",
        lambda *_a, **_k: pl.DataFrame({"item_id": [], "name": []}),
    )
    monkeypatch.setattr(run_pipeline, "EventCleaner", FakeEventCleaner)
    monkeypatch.setattr(run_pipeline, "SessionBuilder", FakeSessionBuilder)
    monkeypatch.setattr(run_pipeline, "ItemPairBuilder", FakeItemPairBuilder)
    monkeypatch.setattr(run_pipeline, "PairAggregator", FakePairAggregator)
    monkeypatch.setattr(run_pipeline, "ItemPopularityBuilder", FakePopularityBuilder)
    monkeypatch.setattr(run_pipeline, "CoVisitationScorer", FakeScorer)
    monkeypatch.setattr(run_pipeline, "TopKSelector", FakeTopKSelector)
    monkeypatch.setattr(run_pipeline, "RecommendationWriter", FakeWriter)

    caplog.set_level(logging.WARNING, logger="ozon_similar_products.pipeline.run_pipeline")

    run_pipeline.run_pipeline(train_until_date="2026-05-10", lookback_days=7)

    assert "missing raw events" in caplog.text
    assert "raw events empty" in caplog.text
    assert "recommendations empty" in caplog.text
    assert "latest manifest not updated" in caplog.text


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
    """Create minimal configs needed by run_pipeline inside tmp_path."""
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
  detailed_recommendations_dir: outputs/detailed
  widget_recommendations_dir: outputs/widget
  latest_dir: outputs/latest
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


def _write_smoke_products(project_root: Path) -> None:
    """Create product_information parquet with names for enriched recommendations."""
    products_dir = project_root / "data" / "raw" / "product_information" / "product_information"
    products_dir.mkdir(parents=True, exist_ok=True)
    pl.DataFrame(
        {
            "item_id": [1, 2, 10, 11, 20],
            "name": ["Item 1", "Item 2", "Item 10", "Item 11", "Item 20"],
            "brand": ["brand"] * 5,
            "type": ["type"] * 5,
            "category_id": [100] * 5,
            "category_name": ["category"] * 5,
        }
    ).write_parquet(products_dir / "part-0.parquet")


def test_run_pipeline_smoke_with_synthetic_parquet_data(
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: Path,
) -> None:
    """Run the real pipeline pipeline on a tiny synthetic parquet dataset."""
    baseline_path = _write_smoke_project_configs(tmp_path)
    _write_smoke_raw_events(tmp_path)
    _write_smoke_products(tmp_path)
    monkeypatch.setattr(run_pipeline, "PROJECT_ROOT", tmp_path)

    run_pipeline.run_pipeline(
        train_until_date="2026-05-10",
        lookback_days=1,
        config_path=baseline_path,
    )

    latest_manifest_path = tmp_path / "outputs" / "latest" / "manifest.json"
    assert latest_manifest_path.exists()

    detailed_outputs = list((tmp_path / "outputs" / "runs").rglob("detailed.parquet"))
    enriched_outputs = list((tmp_path / "outputs" / "runs").rglob("enriched.parquet"))
    widget_outputs = list((tmp_path / "outputs" / "runs").rglob("lookup.parquet"))
    assert detailed_outputs
    assert enriched_outputs
    assert widget_outputs

    detailed = pl.read_parquet(detailed_outputs[0])
    enriched = pl.read_parquet(enriched_outputs[0])
    widget = pl.read_parquet(widget_outputs[0])
    assert detailed.height > 0
    assert enriched.height == detailed.height
    assert widget.height > 0
    assert "weight_sum" not in detailed.columns
    assert "to_cart_count" in detailed.columns
    assert enriched.columns == [
        "item_id",
        "item_name",
        "similar_item_id",
        "similar_item_name",
        "rank",
        "score",
        "source",
    ]
    assert (tmp_path / "outputs" / "latest" / "recommendations" / "enriched.parquet").exists()

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


def test_run_pipeline_loads_raw_events_day_by_day(
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: Path,
) -> None:
    """Pipeline should load raw events one day at a time instead of full window."""
    requested_dates: list[str] = []

    config = {
        "pipeline": {"allow_empty_latest_update": False},
        "events": {"item_action_types": ["view"]},
    }

    def fake_load_events(**kwargs: object) -> pl.DataFrame:
        dates = kwargs.get("dates")
        assert isinstance(dates, list)
        assert len(dates) == 1
        requested_dates.append(str(dates[0]))

        return pl.DataFrame(
            {
                "user_id": [1, 1],
                "date": [dates[0], dates[0]],
                "timestamp": [
                    f"{dates[0]} 10:00:00",
                    f"{dates[0]} 10:05:00",
                ],
                "action_type": ["view", "view"],
                "widget_name": ["catalog", "catalog"],
                "search_query": [None, None],
                "item_id": [10, 20],
            }
        ).with_columns(
            pl.col("date").str.to_date(),
            pl.col("timestamp").str.to_datetime(),
        )

    @dataclass(frozen=True)
    class FakeScorer:
        action_shares: dict[str, float] | None = None
        normalize_by_item_popularity = False
        method = "pair_count"

        @staticmethod
        def from_config(_: dict[str, object]) -> "FakeScorer":
            return FakeScorer()

        def score(
                self,
                pair_aggregates: pl.DataFrame,
                item_popularity: pl.DataFrame | None = None,
        ) -> pl.DataFrame:
            return empty_contract_frame(schemas.PAIR_SCORES_COLUMNS)

        def score_lazy(
                self,
                pair_aggregates: pl.DataFrame,
                item_popularity: pl.DataFrame | None = None,
        ) -> pl.LazyFrame:
            return self.score(pair_aggregates, item_popularity=item_popularity).lazy()

    class FakeWriter:
        def save_detailed(self, _: pl.DataFrame, output_path: str | Path) -> Path:
            return Path(output_path) / "detailed.parquet"

        def save_enriched(self, _: pl.DataFrame, products: pl.DataFrame, output_path: str | Path) -> Path:
            return Path(output_path) / "enriched.parquet"

        def save_widget_format(self, _: pl.DataFrame, output_path: str | Path) -> Path:
            return Path(output_path) / "lookup.parquet"

        def save_manifest(self, manifest: dict[str, object], output_path: str | Path) -> Path:
            rows = cast(dict[str, int], manifest["rows"])
            assert rows["raw_events"] == 4
            assert rows["clean_events"] == 4
            return Path(output_path) / "manifest.json"

        def update_latest_manifest(self, run_manifest_path: str | Path, latest_dir: str | Path) -> Path:
            return Path(latest_dir) / "manifest.json"

    monkeypatch.setattr(run_pipeline, "PROJECT_ROOT", tmp_path)
    monkeypatch.setattr(run_pipeline, "load_yaml_config", lambda _: config)
    monkeypatch.setattr(run_pipeline, "load_configs", lambda project_root: {"project_root": project_root})
    monkeypatch.setattr(run_pipeline, "load_events", fake_load_events)
    monkeypatch.setattr(
        run_pipeline,
        "load_products",
        lambda *_a, **_k: pl.DataFrame({"item_id": [], "name": []}),
    )
    monkeypatch.setattr(run_pipeline, "CoVisitationScorer", FakeScorer)
    monkeypatch.setattr(
        run_pipeline,
        "TopKSelector",
        lambda **_: type(
            "S",
            (),
            {
                "select": (
                    lambda *_a, **_k: empty_contract_frame(
                        schemas.RECOMMENDATIONS_COLUMNS
                    )
                )
            },
        )(),
    )
    monkeypatch.setattr(run_pipeline, "RecommendationWriter", FakeWriter)

    run_pipeline.run_pipeline(
        train_until_date="2026-05-10",
        lookback_days=2,
    )

    assert requested_dates == ["2026-05-09", "2026-05-10"]

    clean_files = sorted((tmp_path / "data" / "processed" / "events_clean").glob("*.parquet"))
    item_pairs_dir = tmp_path / "data" / "processed" / "item_pairs"

    count_files = sorted((item_pairs_dir / "counts").glob("*.parquet"))
    user_key_files = sorted((item_pairs_dir / "user_keys").glob("*.parquet"))
    session_key_files = sorted((item_pairs_dir / "session_keys").glob("*.parquet"))

    assert [path.name for path in clean_files] == [
        "date=2026-05-09.parquet",
        "date=2026-05-10.parquet",
    ]
    assert [path.name for path in count_files] == [
        "date=2026-05-09.parquet",
        "date=2026-05-10.parquet",
    ]
    assert [path.name for path in user_key_files] == [
        "date=2026-05-09.parquet",
        "date=2026-05-10.parquet",
    ]
    assert [path.name for path in session_key_files] == [
        "date=2026-05-09.parquet",
        "date=2026-05-10.parquet",
    ]


def test_scan_parquet_paths_or_empty_frame_scans_existing_paths(tmp_path: Path) -> None:
    events_clean = pl.DataFrame(
        {
            "user_id": [1],
            "event_date": [date(2026, 5, 10)],
            "timestamp": ["2026-05-10 10:00:00"],
            "action_type": ["view"],
            "item_id": [10],
            "search_query": [None],
            "widget_name": ["catalog"],
        }
    ).with_columns(
        pl.col("timestamp").str.to_datetime(),
    ).select(schemas.CLEAN_EVENTS_COLUMNS)

    path = tmp_path / "date=2026-05-10.parquet"
    events_clean.write_parquet(path)

    frame = run_pipeline._scan_parquet_paths_or_empty_frame(
        [path],
        schemas.CLEAN_EVENTS_COLUMNS,
    )

    assert isinstance(frame, pl.LazyFrame)
    collected = frame.collect()
    assert collected.columns == schemas.CLEAN_EVENTS_COLUMNS
    assert collected.height == 1


def test_load_clean_and_write_daily_events_skips_missing_dates(
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: Path,
) -> None:
    requested_dates: list[str] = []

    def fake_load_events(**kwargs: object) -> pl.DataFrame:
        dates = kwargs.get("dates")
        assert isinstance(dates, list)
        assert len(dates) == 1

        partition_date = str(dates[0])
        requested_dates.append(partition_date)

        if partition_date == "2026-05-09":
            raise FileNotFoundError("No files for selected date")

        return pl.DataFrame(
            {
                "user_id": [1],
                "date": [partition_date],
                "timestamp": [f"{partition_date} 10:00:00"],
                "action_type": ["view"],
                "widget_name": ["catalog"],
                "search_query": [None],
                "item_id": [10],
            }
        ).with_columns(
            pl.col("date").str.to_date(),
            pl.col("timestamp").str.to_datetime(),
        )

    class FakeCleaner:
        def transform_day(self, events: pl.DataFrame) -> pl.DataFrame:
            return (
                events.rename({"date": "event_date"})
                .select(schemas.CLEAN_EVENTS_COLUMNS)
            )

    monkeypatch.setattr(run_pipeline, "load_events", fake_load_events)

    paths, raw_rows, clean_rows = run_pipeline._load_clean_and_write_daily_events(
        data_config={},
        cleaner=cast(run_pipeline.EventCleaner, FakeCleaner()),
        action_types=["view"],
        window_start="2026-05-09",
        window_end="2026-05-10",
        output_dir=tmp_path / "events_clean",
        allow_empty_input=False,
        logger=logging.getLogger("test"),
    )

    assert requested_dates == ["2026-05-09", "2026-05-10"]
    assert raw_rows == 1
    assert clean_rows == 1
    assert [path.name for path in paths] == ["date=2026-05-10.parquet"]
    assert paths[0].exists()


def test_build_and_write_daily_pair_stats_writes_compact_artifacts(tmp_path: Path) -> None:
    sessions = pl.DataFrame(
        {
            "user_id": [1, 1],
            "session_index": [1, 1],
            "session_start_date": [date(2026, 5, 10), date(2026, 5, 10)],
            "event_date": [date(2026, 5, 10), date(2026, 5, 10)],
            "timestamp": [
                "2026-05-10 10:00:00",
                "2026-05-10 10:01:00",
            ],
            "action_type": ["view", "to_cart"],
            "item_id": [10, 20],
        }
    ).with_columns(
        pl.col("timestamp").str.to_datetime(),
    )

    paths = run_pipeline._build_and_write_daily_pair_stats(
        daily_sessions=[("2026-05-10", sessions)],
        pair_builder=run_pipeline.ItemPairBuilder(),
        output_dir=tmp_path / "item_pairs",
    )

    assert paths.raw_pair_rows == 2
    assert [path.name for path in paths.count_paths] == ["date=2026-05-10.parquet"]
    assert [path.name for path in paths.user_key_paths] == ["date=2026-05-10.parquet"]
    assert [path.name for path in paths.session_key_paths] == ["date=2026-05-10.parquet"]

    counts = pl.read_parquet(paths.count_paths[0])
    user_keys = pl.read_parquet(paths.user_key_paths[0])
    session_keys = pl.read_parquet(paths.session_key_paths[0])

    assert counts.columns == schemas.DAILY_PAIR_COUNTS_COLUMNS
    assert user_keys.columns == schemas.DAILY_PAIR_USER_KEYS_COLUMNS
    assert session_keys.columns == schemas.DAILY_PAIR_SESSION_KEYS_COLUMNS


def test_build_streaming_sessions_and_pair_stats_keeps_cross_midnight_session(
        tmp_path: Path,
) -> None:
    day1 = pl.DataFrame(
        {
            "user_id": [1],
            "event_date": [date(2026, 5, 10)],
            "timestamp": ["2026-05-10 23:55:00"],
            "action_type": ["view"],
            "item_id": [10],
            "search_query": [None],
            "widget_name": ["catalog"],
        }
    ).with_columns(
        pl.col("timestamp").str.to_datetime(),
    ).select(schemas.CLEAN_EVENTS_COLUMNS)

    day2 = pl.DataFrame(
        {
            "user_id": [1],
            "event_date": [date(2026, 5, 11)],
            "timestamp": ["2026-05-11 00:05:00"],
            "action_type": ["click"],
            "item_id": [20],
            "search_query": [None],
            "widget_name": ["catalog"],
        }
    ).with_columns(
        pl.col("timestamp").str.to_datetime(),
    ).select(schemas.CLEAN_EVENTS_COLUMNS)

    clean_dir = tmp_path / "events_clean"
    clean_dir.mkdir()
    day1_path = clean_dir / "date=2026-05-10.parquet"
    day2_path = clean_dir / "date=2026-05-11.parquet"
    day1.write_parquet(day1_path)
    day2.write_parquet(day2_path)

    sessions_rows, stats_paths = run_pipeline._build_streaming_sessions_and_pair_stats(
        clean_event_paths=[day1_path, day2_path],
        session_builder=run_pipeline.SessionBuilder(timeout_minutes=30),
        pair_builder=run_pipeline.ItemPairBuilder(),
        daily_pairs_output_dir=tmp_path / "item_pairs",
        sessions_output_dir=tmp_path / "sessions",
    )

    assert sessions_rows == 2
    assert stats_paths.raw_pair_rows == 2
    assert [path.name for path in stats_paths.count_paths] == [
        "date=2026-05-10.parquet"
    ]

    sessions_day1 = pl.read_parquet(tmp_path / "sessions" / "date=2026-05-10.parquet")
    sessions_day2 = pl.read_parquet(tmp_path / "sessions" / "date=2026-05-11.parquet")

    assert sessions_day1["session_start_date"].cast(pl.String).to_list() == [
        "2026-05-10"
    ]
    assert sessions_day2["session_start_date"].cast(pl.String).to_list() == [
        "2026-05-10"
    ]

    counts = pl.read_parquet(stats_paths.count_paths[0])
    assert counts["pair_count"].sum() == 2


def test_build_streaming_sessions_and_pair_stats_splits_after_timeout(
        tmp_path: Path,
) -> None:
    day1 = pl.DataFrame(
        {
            "user_id": [1],
            "event_date": [date(2026, 5, 10)],
            "timestamp": ["2026-05-10 23:00:00"],
            "action_type": ["view"],
            "item_id": [10],
            "search_query": [None],
            "widget_name": ["catalog"],
        }
    ).with_columns(
        pl.col("timestamp").str.to_datetime(),
    ).select(schemas.CLEAN_EVENTS_COLUMNS)

    day2 = pl.DataFrame(
        {
            "user_id": [1],
            "event_date": [date(2026, 5, 11)],
            "timestamp": ["2026-05-11 00:00:00"],
            "action_type": ["click"],
            "item_id": [20],
            "search_query": [None],
            "widget_name": ["catalog"],
        }
    ).with_columns(
        pl.col("timestamp").str.to_datetime(),
    ).select(schemas.CLEAN_EVENTS_COLUMNS)

    clean_dir = tmp_path / "events_clean"
    clean_dir.mkdir()
    day1_path = clean_dir / "date=2026-05-10.parquet"
    day2_path = clean_dir / "date=2026-05-11.parquet"
    day1.write_parquet(day1_path)
    day2.write_parquet(day2_path)

    sessions_rows, stats_paths = run_pipeline._build_streaming_sessions_and_pair_stats(
        clean_event_paths=[day1_path, day2_path],
        session_builder=run_pipeline.SessionBuilder(timeout_minutes=30),
        pair_builder=run_pipeline.ItemPairBuilder(),
        daily_pairs_output_dir=tmp_path / "item_pairs",
        sessions_output_dir=tmp_path / "sessions",
    )

    assert sessions_rows == 2
    assert stats_paths.raw_pair_rows == 0

    sessions_day1 = pl.read_parquet(tmp_path / "sessions" / "date=2026-05-10.parquet")
    sessions_day2 = pl.read_parquet(tmp_path / "sessions" / "date=2026-05-11.parquet")

    assert sessions_day1["session_index"].to_list() == [1]
    assert sessions_day2["session_index"].to_list() == [2]
