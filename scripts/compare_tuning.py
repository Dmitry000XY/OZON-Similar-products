"""Compare tuning trial results."""

from __future__ import annotations

import argparse
from pathlib import Path

import polars as pl


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Compare saved tuning trial metrics.",
    )
    parser.add_argument(
        "--results-path",
        type=Path,
        default=None,
        help="Path to a tuning results.csv. Defaults to the newest outputs/tuning/*/results.csv.",
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

    results_path = args.results_path
    if results_path is None:
        candidates = sorted(Path("outputs/tuning").glob("*/results.csv"))
        if not candidates:
            raise FileNotFoundError("No tuning results found under outputs/tuning")
        results_path = candidates[-1]

    if not results_path.exists():
        raise FileNotFoundError(f"Tuning results not found: {results_path}")

    trials = pl.read_csv(results_path)

    if args.sort_by not in trials.columns:
        raise ValueError(
            f"Unknown sort column: {args.sort_by}. "
            f"Available columns: {trials.columns}"
        )

    columns = [
        column for column in [
            "trial_id",
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
        if column in trials.columns
    ]

    print(
        trials
        .sort(args.sort_by, descending=args.descending)
        .select(columns)
        .head(args.top_n)
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
