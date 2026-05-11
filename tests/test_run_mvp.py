"""Tests for MVP pipeline orchestration."""

from dataclasses import dataclass
from pathlib import Path
from typing import cast

import polars as pl
import pytest

import ozon_similar_products.pipeline.run_mvp as run_mvp
from ozon_similar_products.data import schemas
from ozon_similar_products.data.frames import empty_contract_frame


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
