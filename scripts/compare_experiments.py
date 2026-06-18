"""Compare offline recommendation experiments."""

from __future__ import annotations

import argparse
from pathlib import Path

import polars as pl


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Compare saved experiment metrics.",
    )
    parser.add_argument(
        "--index-path",
        type=Path,
        default=Path("outputs/experiments/index.csv"),
        help="Path to experiment index CSV.",
    )
    parser.add_argument(
        "--sort-by",
        default="to_cart_hit_rate_at_k",
        help="Metric column used for sorting.",
    )
    parser.add_argument(
        "--descending",
        action="store_true",
        default=True,
        help="Sort from best to worst.",
    )
    parser.add_argument(
        "--top-n",
        type=int,
        default=20,
        help="Number of rows to print.",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()

    if not args.index_path.exists():
        raise FileNotFoundError(f"Experiment index not found: {args.index_path}")

    experiments = pl.read_csv(args.index_path)

    if args.sort_by not in experiments.columns:
        raise ValueError(
            f"Unknown sort column: {args.sort_by}. "
            f"Available columns: {experiments.columns}"
        )

    columns = [
        column for column in [
            "experiment_id",
            "experiment_name",
            "train_until_date",
            "lookback_days",
            "validation_start_date",
            "validation_end_date",
            "top_k",
            "hit_rate_at_k",
            "weighted_recall_at_k",
            "to_cart_hit_rate_at_k",
            "to_cart_recall_at_k",
            "ndcg_at_k",
            "mrr_at_k",
            "coverage_at_k",
            "fallback_share_at_k",
            "popularity_bias_at_k",
            "elapsed_seconds",
        ]
        if column in experiments.columns
    ]

    print(
        experiments
        .sort(args.sort_by, descending=args.descending)
        .select(columns)
        .head(args.top_n)
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
