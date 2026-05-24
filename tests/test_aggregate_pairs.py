from datetime import date

import polars as pl
import pytest

from ozon_similar_products.data import schemas
from ozon_similar_products.retrieval.aggregate_pairs import PairAggregator


def _pairs(rows: list[dict]) -> pl.DataFrame:
    return pl.DataFrame(rows)


def _daily_stats_from_pairs(
        pairs: pl.DataFrame,
) -> tuple[pl.DataFrame, pl.DataFrame, pl.DataFrame]:
    counts = (
        pairs.group_by(["pair_date", "item_id", "similar_item_id"])
        .agg(
            pl.len().alias("pair_count"),
            (pl.col("signal_type") == "view").sum().alias("view_count"),
            (pl.col("signal_type") == "click").sum().alias("click_count"),
            (pl.col("signal_type") == "favorite").sum().alias("favorite_count"),
            (pl.col("signal_type") == "to_cart").sum().alias("to_cart_count"),
        )
        .select(schemas.DAILY_PAIR_COUNTS_COLUMNS)
        .sort(["pair_date", "item_id", "similar_item_id"])
    )

    user_keys = (
        pairs.select(schemas.DAILY_PAIR_USER_KEYS_COLUMNS)
        .unique()
        .sort(["pair_date", "item_id", "similar_item_id", "user_id"])
    )

    session_keys = (
        pairs.select(schemas.DAILY_PAIR_SESSION_KEYS_COLUMNS)
        .unique()
        .sort(["pair_date", "item_id", "similar_item_id", "user_id", "session_index"])
    )

    return counts, user_keys, session_keys


def _sorted_aggregates(frame: pl.DataFrame) -> pl.DataFrame:
    return frame.sort(["item_id", "similar_item_id"])


def test_aggregate_window_counts_signal_channels_and_uniques() -> None:
    day1 = _pairs(
        [
            {
                "pair_date": date(2026, 5, 1),
                "item_id": 10,
                "similar_item_id": 20,
                "session_index": 1,
                "user_id": 1,
                "source_action_type": "view",
                "target_action_type": "to_cart",
                "signal_type": "to_cart",
            },
            {
                "pair_date": date(2026, 5, 1),
                "item_id": 10,
                "similar_item_id": 20,
                "session_index": 2,
                "user_id": 1,
                "source_action_type": "view",
                "target_action_type": "click",
                "signal_type": "click",
            },
            {
                "pair_date": date(2026, 5, 1),
                "item_id": 10,
                "similar_item_id": 20,
                "session_index": 1,
                "user_id": 2,
                "source_action_type": "view",
                "target_action_type": "view",
                "signal_type": "view",
            },
        ]
    )
    day2 = _pairs(
        [
            {
                "pair_date": date(2026, 5, 2),
                "item_id": 10,
                "similar_item_id": 20,
                "session_index": 1,
                "user_id": 3,
                "source_action_type": "view",
                "target_action_type": "favorite",
                "signal_type": "favorite",
            },
            {
                "pair_date": date(2026, 5, 2),
                "item_id": 20,
                "similar_item_id": 10,
                "session_index": 1,
                "user_id": 3,
                "source_action_type": "favorite",
                "target_action_type": "view",
                "signal_type": "view",
            },
        ]
    )

    result = PairAggregator().aggregate_window([day1, day2.lazy()], "2026-05-01", "2026-05-02")

    row = result.filter((pl.col("item_id") == 10) & (pl.col("similar_item_id") == 20)).row(0, named=True)
    assert row["pair_count"] == 4
    assert row["view_count"] == 1
    assert row["click_count"] == 1
    assert row["favorite_count"] == 1
    assert row["to_cart_count"] == 1
    assert row["unique_users"] == 3
    assert row["unique_sessions"] == 4


def test_aggregate_window_filters_dates_inclusively() -> None:
    pairs = _pairs(
        [
            {
                "pair_date": date(2026, 5, 1),
                "item_id": 10,
                "similar_item_id": 20,
                "session_index": 1,
                "user_id": 1,
                "source_action_type": "view",
                "target_action_type": "view",
                "signal_type": "view",
            },
            {
                "pair_date": date(2026, 5, 2),
                "item_id": 10,
                "similar_item_id": 20,
                "session_index": 2,
                "user_id": 1,
                "source_action_type": "view",
                "target_action_type": "to_cart",
                "signal_type": "to_cart",
            },
        ]
    )

    result = PairAggregator().aggregate_window([pairs], "2026-05-02", "2026-05-02")

    assert result.height == 1
    assert result[0, "pair_count"] == 1
    assert result[0, "to_cart_count"] == 1


def test_aggregate_window_returns_empty_for_no_pairs() -> None:
    result = PairAggregator().aggregate_window([], "2026-05-01", "2026-05-02")

    assert result.is_empty()
    assert "view_count" in result.columns
    assert "to_cart_count" in result.columns


def test_aggregate_window_rejects_invalid_window() -> None:
    with pytest.raises(ValueError, match="window_start"):
        PairAggregator().aggregate_window([], "bad", "2026-05-02")

    with pytest.raises(ValueError, match="less than or equal"):
        PairAggregator().aggregate_window([], "2026-05-03", "2026-05-02")


def test_aggregate_window_from_paths_scans_pair_parquet_files(tmp_path) -> None:
    day1 = _pairs(
        [
            {
                "pair_date": date(2026, 5, 1),
                "item_id": 10,
                "similar_item_id": 20,
                "session_index": 1,
                "user_id": 1,
                "source_action_type": "view",
                "target_action_type": "view",
                "signal_type": "view",
            },
            {
                "pair_date": date(2026, 5, 1),
                "item_id": 10,
                "similar_item_id": 20,
                "session_index": 1,
                "user_id": 2,
                "source_action_type": "view",
                "target_action_type": "to_cart",
                "signal_type": "to_cart",
            },
        ]
    )
    day2 = _pairs(
        [
            {
                "pair_date": date(2026, 5, 2),
                "item_id": 10,
                "similar_item_id": 20,
                "session_index": 1,
                "user_id": 3,
                "source_action_type": "view",
                "target_action_type": "click",
                "signal_type": "click",
            },
            {
                "pair_date": date(2026, 5, 2),
                "item_id": 20,
                "similar_item_id": 10,
                "session_index": 1,
                "user_id": 3,
                "source_action_type": "click",
                "target_action_type": "view",
                "signal_type": "view",
            },
        ]
    )

    day1_path = tmp_path / "date=2026-05-01.parquet"
    day2_path = tmp_path / "date=2026-05-02.parquet"
    day1.write_parquet(day1_path)
    day2.write_parquet(day2_path)

    result = PairAggregator().aggregate_window_from_paths(
        [day1_path, day2_path],
        window_start="2026-05-01",
        window_end="2026-05-02",
    )

    row = result.filter(
        (pl.col("item_id") == 10)
        & (pl.col("similar_item_id") == 20)
    ).row(0, named=True)

    assert row["pair_count"] == 3
    assert row["view_count"] == 1
    assert row["click_count"] == 1
    assert row["favorite_count"] == 0
    assert row["to_cart_count"] == 1
    assert row["unique_users"] == 3
    assert row["unique_sessions"] == 3


def test_aggregate_window_from_daily_stats_paths_matches_raw_pair_paths(tmp_path) -> None:
    day1 = _pairs(
        [
            {
                "pair_date": date(2026, 5, 1),
                "item_id": 10,
                "similar_item_id": 20,
                "user_id": 1,
                "session_index": 1,
                "source_action_type": "view",
                "target_action_type": "view",
                "signal_type": "view",
            },
            {
                "pair_date": date(2026, 5, 1),
                "item_id": 10,
                "similar_item_id": 20,
                "user_id": 1,
                "session_index": 2,
                "source_action_type": "view",
                "target_action_type": "to_cart",
                "signal_type": "to_cart",
            },
            {
                "pair_date": date(2026, 5, 1),
                "item_id": 20,
                "similar_item_id": 10,
                "user_id": 1,
                "session_index": 2,
                "source_action_type": "to_cart",
                "target_action_type": "view",
                "signal_type": "view",
            },
        ]
    )
    day2 = _pairs(
        [
            {
                "pair_date": date(2026, 5, 2),
                "item_id": 10,
                "similar_item_id": 20,
                "user_id": 2,
                "session_index": 1,
                "source_action_type": "click",
                "target_action_type": "click",
                "signal_type": "click",
            },
            {
                "pair_date": date(2026, 5, 2),
                "item_id": 10,
                "similar_item_id": 20,
                "user_id": 2,
                "session_index": 1,
                "source_action_type": "click",
                "target_action_type": "favorite",
                "signal_type": "favorite",
            },
        ]
    )

    raw_day1_path = tmp_path / "raw_date=2026-05-01.parquet"
    raw_day2_path = tmp_path / "raw_date=2026-05-02.parquet"
    day1.write_parquet(raw_day1_path)
    day2.write_parquet(raw_day2_path)

    expected = PairAggregator().aggregate_window_from_paths(
        [raw_day1_path, raw_day2_path],
        window_start="2026-05-01",
        window_end="2026-05-02",
    )

    counts_dir = tmp_path / "counts"
    user_keys_dir = tmp_path / "user_keys"
    session_keys_dir = tmp_path / "session_keys"
    counts_dir.mkdir()
    user_keys_dir.mkdir()
    session_keys_dir.mkdir()

    count_paths = []
    user_key_paths = []
    session_key_paths = []

    for partition_date, pairs in [
        ("2026-05-01", day1),
        ("2026-05-02", day2),
    ]:
        counts, user_keys, session_keys = _daily_stats_from_pairs(pairs)

        count_path = counts_dir / f"date={partition_date}.parquet"
        user_key_path = user_keys_dir / f"date={partition_date}.parquet"
        session_key_path = session_keys_dir / f"date={partition_date}.parquet"

        counts.write_parquet(count_path)
        user_keys.write_parquet(user_key_path)
        session_keys.write_parquet(session_key_path)

        count_paths.append(count_path)
        user_key_paths.append(user_key_path)
        session_key_paths.append(session_key_path)

    result = PairAggregator().aggregate_window_from_daily_stats_paths(
        count_paths=count_paths,
        user_key_paths=user_key_paths,
        session_key_paths=session_key_paths,
        window_start="2026-05-01",
        window_end="2026-05-02",
    )

    assert _sorted_aggregates(result).equals(_sorted_aggregates(expected))


def test_aggregate_window_from_daily_stats_paths_returns_empty_for_no_paths() -> None:
    result = PairAggregator().aggregate_window_from_daily_stats_paths(
        count_paths=[],
        user_key_paths=[],
        session_key_paths=[],
        window_start="2026-05-01",
        window_end="2026-05-02",
    )

    assert result.is_empty()
    assert result.columns == schemas.PAIR_AGGREGATES_COLUMNS


def test_aggregate_window_from_daily_stats_paths_rejects_partial_paths(tmp_path) -> None:
    counts = pl.DataFrame(
        {
            "pair_date": [date(2026, 5, 1)],
            "item_id": [10],
            "similar_item_id": [20],
            "pair_count": [1],
            "view_count": [1],
            "click_count": [0],
            "favorite_count": [0],
            "to_cart_count": [0],
        }
    )
    count_path = tmp_path / "counts.parquet"
    counts.write_parquet(count_path)

    with pytest.raises(ValueError, match="must all be empty or all be provided"):
        PairAggregator().aggregate_window_from_daily_stats_paths(
            count_paths=[count_path],
            user_key_paths=[],
            session_key_paths=[],
            window_start="2026-05-01",
            window_end="2026-05-02",
        )


def test_aggregate_window_from_daily_stats_paths_returns_empty_when_no_stats_in_window(
        tmp_path,
) -> None:
    pairs = _pairs(
        [
            {
                "pair_date": date(2026, 5, 1),
                "item_id": 10,
                "similar_item_id": 20,
                "user_id": 1,
                "session_index": 1,
                "source_action_type": "view",
                "target_action_type": "view",
                "signal_type": "view",
            }
        ]
    )
    counts, user_keys, session_keys = _daily_stats_from_pairs(pairs)

    count_path = tmp_path / "counts.parquet"
    user_key_path = tmp_path / "user_keys.parquet"
    session_key_path = tmp_path / "session_keys.parquet"

    counts.write_parquet(count_path)
    user_keys.write_parquet(user_key_path)
    session_keys.write_parquet(session_key_path)

    result = PairAggregator().aggregate_window_from_daily_stats_paths(
        count_paths=[count_path],
        user_key_paths=[user_key_path],
        session_key_paths=[session_key_path],
        window_start="2026-05-02",
        window_end="2026-05-02",
    )

    assert result.is_empty()
    assert result.columns == schemas.PAIR_AGGREGATES_COLUMNS


def test_aggregate_window_from_paths_returns_empty_for_no_paths() -> None:
    result = PairAggregator().aggregate_window_from_paths(
        [],
        window_start="2026-05-01",
        window_end="2026-05-02",
    )

    assert result.is_empty()
    assert result.columns == [
        "item_id",
        "similar_item_id",
        "pair_count",
        "view_count",
        "click_count",
        "favorite_count",
        "to_cart_count",
        "unique_users",
        "unique_sessions",
        "window_start",
        "window_end",
    ]


def test_aggregate_window_from_paths_returns_empty_when_no_pairs_in_window(tmp_path) -> None:
    pairs = _pairs(
        [
            {
                "pair_date": date(2026, 5, 1),
                "item_id": 10,
                "similar_item_id": 20,
                "session_index": 1,
                "user_id": 1,
                "source_action_type": "view",
                "target_action_type": "view",
                "signal_type": "view",
            }
        ]
    )

    pairs_path = tmp_path / "date=2026-05-01.parquet"
    pairs.write_parquet(pairs_path)

    result = PairAggregator().aggregate_window_from_paths(
        [pairs_path],
        window_start="2026-05-02",
        window_end="2026-05-02",
    )

    assert result.is_empty()
    assert result.columns == [
        "item_id",
        "similar_item_id",
        "pair_count",
        "view_count",
        "click_count",
        "favorite_count",
        "to_cart_count",
        "unique_users",
        "unique_sessions",
        "window_start",
        "window_end",
    ]
