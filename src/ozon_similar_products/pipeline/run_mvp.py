"""MVP pipeline runner."""

from pathlib import Path


def run_mvp_pipeline(
    train_until_date: str,
    lookback_days: int,
    config_path: str | Path = "configs/baseline.yaml",
) -> None:
    """Run full MVP pipeline over a rolling window.

    Pipeline stages:
    1. load daily raw events;
    2. clean events by day;
    3. build sessions by day;
    4. build daily item pairs;
    5. aggregate pairs over window;
    6. score pairs;
    7. select top-K;
    8. save detailed recommendations;
    9. save widget output;
    10. update latest snapshot.
    """
    raise NotImplementedError
