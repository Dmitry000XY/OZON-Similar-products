"""CLI entrypoint for the MVP recommendation pipeline."""

from __future__ import annotations

import argparse
import logging
from pathlib import Path

from ozon_similar_products.pipeline.run_mvp import run_mvp_pipeline


def parse_args() -> argparse.Namespace:
    """Parse command-line arguments for the MVP pipeline run."""
    parser = argparse.ArgumentParser(
        description="Run the MVP similar-products pipeline over a rolling window.",
    )
    parser.add_argument(
        "train_until_date",
        help="Inclusive window end date in ISO format (YYYY-MM-DD).",
    )
    parser.add_argument(
        "--lookback-days",
        type=int,
        default=30,
        help="Rolling window size in days (default: 30).",
    )
    parser.add_argument(
        "--config-path",
        type=Path,
        default=Path("configs/baseline.yaml"),
        help="Path to baseline config YAML (default: configs/baseline.yaml).",
    )
    return parser.parse_args()


def main() -> int:
    """Run the MVP pipeline from CLI arguments."""
    logging.basicConfig(level=logging.INFO, format="%(message)s")

    args = parse_args()
    logger = logging.getLogger(__name__)
    logger.info(
        "[run_mvp_pipeline] start train_until_date=%s lookback_days=%s config=%s",
        args.train_until_date,
        args.lookback_days,
        args.config_path,
    )

    try:
        run_mvp_pipeline(
            train_until_date=args.train_until_date,
            lookback_days=args.lookback_days,
            config_path=args.config_path,
        )
    except Exception:
        logger.exception(
            "[run_mvp_pipeline] failed train_until_date=%s lookback_days=%s config=%s",
            args.train_until_date,
            args.lookback_days,
            args.config_path,
        )
        return 1

    logger.info("[run_mvp_pipeline] done")
    return 0
