from datetime import date, datetime
from pathlib import Path

import polars as pl
import pytest

from ozon_similar_products.data import schemas
from ozon_similar_products.retrieval.aggregate_pairs import PairAggregator
from ozon_similar_products.retrieval.build_pairs import ItemPairBuilder
from ozon_similar_products.retrieval.decay import DistanceDecayConfig, TimeDecayConfig


def _sessions_for_distance() -> pl.DataFrame:
    return pl.DataFrame(
        [
            {
                "user_id": 1,
                "session_index": 1,
                "session_start_date": date(2026, 5, 1),
                "event_date": date(2026, 5, 1),
                "timestamp": datetime(2026, 5, 1, 10, 0),
                "action_type": "view",
                "item_id": 10,
            },
            {
                "user_id": 1,
                "session_index": 1,
                "session_start_date": date(2026, 5, 1),
                "event_date": date(2026, 5, 1),
                "timestamp": datetime(2026, 5, 1, 10, 1),
                "action_type": "view",
                "item_id": 20,
            },
            {
                "user_id": 1,
                "session_index": 1,
                "session_start_date": date(2026, 5, 1),
                "event_date": date(2026, 5, 1),
                "timestamp": datetime(2026, 5, 1, 10, 2),
                "action_type": "view",
                "item_id": 30,
            },
        ]
    )


def _row(frame: pl.DataFrame, item_id: int, similar_item_id: int) -> dict:
    return frame.filter(
        (pl.col("item_id") == item_id)
        & (pl.col("similar_item_id") == similar_item_id)
    ).row(0, named=True)


def test_distance_decay_disabled_keeps_weighted_counts_equal_to_raw_counts() -> None:
    stats = ItemPairBuilder().build_daily_pair_stats(_sessions_for_distance())

    assert stats.counts["pair_count"].to_list() == stats.counts["weighted_pair_count"].to_list()
    assert stats.counts["view_count"].to_list() == stats.counts["weighted_view_count"].to_list()


def test_distance_decay_weight_table_uses_bucket_floor_semantics() -> None:
    stats = ItemPairBuilder(
        distance_decay=DistanceDecayConfig(
            enabled=True,
            strategy="weight_table",
            weight_by_distance={1: 1.0, 2: 0.5},
            default_weight=0.1,
        )
    ).build_daily_pair_stats(_sessions_for_distance())

    near = _row(stats.counts, 10, 20)
    far = _row(stats.counts, 10, 30)

    assert near["pair_count"] == 1
    assert near["weighted_pair_count"] == pytest.approx(1.0)
    assert far["pair_count"] == 1
    assert far["weighted_pair_count"] == pytest.approx(0.5)
    assert near["weighted_pair_count"] > far["weighted_pair_count"]


def test_distance_decay_exponential_and_min_weight() -> None:
    pairs = ItemPairBuilder(
        distance_decay=DistanceDecayConfig(
            enabled=True,
            strategy="exponential",
            alpha=10.0,
            min_weight=0.4,
        )
    ).transform_day(_sessions_for_distance())

    near = _row(pairs, 10, 20)
    far = _row(pairs, 10, 30)

    assert near["position_distance"] == 1
    assert near["distance_weight"] == pytest.approx(1.0)
    assert far["position_distance"] == 2
    assert far["distance_weight"] == pytest.approx(0.4)


def test_distance_decay_max_distance_filters_distant_pairs() -> None:
    stats = ItemPairBuilder(
        distance_decay=DistanceDecayConfig(
            enabled=True,
            strategy="none",
            max_distance=1,
        )
    ).build_daily_pair_stats(_sessions_for_distance())

    assert stats.raw_pair_rows == 4
    assert stats.counts.filter(
        (pl.col("item_id") == 10)
        & (pl.col("similar_item_id") == 30)
    ).is_empty()


def _daily_stats_frame(partition_date: date, weighted_pair_count: float = 1.0) -> pl.DataFrame:
    return pl.DataFrame(
        {
            "pair_date": [partition_date],
            "item_id": [10],
            "similar_item_id": [20],
            "pair_count": [1],
            "view_count": [1],
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


def _user_keys_frame(partition_date: date) -> pl.DataFrame:
    return pl.DataFrame(
        {
            "pair_date": [partition_date],
            "item_id": [10],
            "similar_item_id": [20],
            "user_id": [1],
        }
    )


def _session_keys_frame(partition_date: date) -> pl.DataFrame:
    return pl.DataFrame(
        {
            "pair_date": [partition_date],
            "item_id": [10],
            "similar_item_id": [20],
            "user_id": [1],
            "session_index": [1],
        }
    )


def _write_daily_stats(
    tmp_path: Path,
    *daily_counts: pl.DataFrame,
) -> tuple[list[Path], list[Path], list[Path]]:
    counts_dir = tmp_path / "counts"
    user_keys_dir = tmp_path / "user_keys"
    session_keys_dir = tmp_path / "session_keys"
    counts_dir.mkdir()
    user_keys_dir.mkdir()
    session_keys_dir.mkdir()

    count_paths: list[Path] = []
    user_key_paths: list[Path] = []
    session_key_paths: list[Path] = []
    for counts in daily_counts:
        partition_date = str(counts[0, "pair_date"])
        count_path = counts_dir / f"date={partition_date}.parquet"
        user_key_path = user_keys_dir / f"date={partition_date}.parquet"
        session_key_path = session_keys_dir / f"date={partition_date}.parquet"

        counts.write_parquet(count_path)
        _user_keys_frame(counts[0, "pair_date"]).write_parquet(user_key_path)
        _session_keys_frame(counts[0, "pair_date"]).write_parquet(session_key_path)
        count_paths.append(count_path)
        user_key_paths.append(user_key_path)
        session_key_paths.append(session_key_path)

    return count_paths, user_key_paths, session_key_paths


def test_time_decay_disabled_keeps_weighted_sum_without_time_decay(tmp_path: Path) -> None:
    count_paths, user_key_paths, session_key_paths = _write_daily_stats(
        tmp_path,
        _daily_stats_frame(date(2026, 5, 1), weighted_pair_count=0.5),
        _daily_stats_frame(date(2026, 5, 2), weighted_pair_count=1.0),
    )

    result = PairAggregator().aggregate_window_from_daily_stats_paths(
        count_paths=count_paths,
        user_key_paths=user_key_paths,
        session_key_paths=session_key_paths,
        window_start="2026-05-01",
        window_end="2026-05-02",
    )

    assert result[0, "pair_count"] == 2
    assert result[0, "weighted_pair_count"] == pytest.approx(1.5)


def test_time_decay_weight_table_downweights_older_days(tmp_path: Path) -> None:
    count_paths, user_key_paths, session_key_paths = _write_daily_stats(
        tmp_path,
        _daily_stats_frame(date(2026, 5, 1)),
        _daily_stats_frame(date(2026, 5, 2)),
    )

    result = PairAggregator(
        time_decay=TimeDecayConfig(
            enabled=True,
            strategy="weight_table",
            weight_by_age_days={0: 1.0, 1: 0.5},
            default_weight=0.1,
        )
    ).aggregate_window_from_daily_stats_paths(
        count_paths=count_paths,
        user_key_paths=user_key_paths,
        session_key_paths=session_key_paths,
        window_start="2026-05-01",
        window_end="2026-05-02",
    )

    assert result[0, "pair_count"] == 2
    assert result[0, "weighted_pair_count"] == pytest.approx(1.5)


def test_time_decay_exponential_and_min_weight(tmp_path: Path) -> None:
    count_paths, user_key_paths, session_key_paths = _write_daily_stats(
        tmp_path,
        _daily_stats_frame(date(2026, 5, 1)),
        _daily_stats_frame(date(2026, 5, 2)),
    )

    result = PairAggregator(
        time_decay=TimeDecayConfig(
            enabled=True,
            strategy="exponential",
            half_life_days=1,
            min_weight=0.75,
        )
    ).aggregate_window_from_daily_stats_paths(
        count_paths=count_paths,
        user_key_paths=user_key_paths,
        session_key_paths=session_key_paths,
        window_start="2026-05-01",
        window_end="2026-05-02",
    )

    assert result[0, "weighted_pair_count"] == pytest.approx(1.75)


def test_aggregator_fills_weighted_columns_for_legacy_daily_counts(tmp_path: Path) -> None:
    legacy_counts = _daily_stats_frame(date(2026, 5, 1)).select(
        [
            "pair_date",
            "item_id",
            "similar_item_id",
            "pair_count",
            "view_count",
            "click_count",
            "favorite_count",
            "to_cart_count",
        ]
    )
    count_paths, user_key_paths, session_key_paths = _write_daily_stats(
        tmp_path,
        legacy_counts,
    )

    result = PairAggregator().aggregate_window_from_daily_stats_paths(
        count_paths=count_paths,
        user_key_paths=user_key_paths,
        session_key_paths=session_key_paths,
        window_start="2026-05-01",
        window_end="2026-05-01",
    )

    assert result[0, "pair_count"] == 1
    assert result[0, "weighted_pair_count"] == pytest.approx(1.0)
