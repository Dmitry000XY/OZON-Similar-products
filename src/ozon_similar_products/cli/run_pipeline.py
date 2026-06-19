"""CLI entrypoint for the recommendation pipeline."""

from __future__ import annotations

import argparse
import logging
import tempfile
from collections.abc import Mapping
from copy import deepcopy
from pathlib import Path
from typing import Any

import yaml

from ozon_similar_products.config import load_yaml_config
from ozon_similar_products.pipeline.run_pipeline import run_pipeline


def _config_with_top_k_override(
    config: Mapping[str, Any],
    top_k: int | None,
) -> dict[str, Any]:
    """Return a config copy with ``top_k`` applied to all top-K selectors."""
    overridden = deepcopy(dict(config))
    if top_k is None:
        return overridden

    for section_name in ("pipeline", "topk"):
        section = overridden.get(section_name)
        if section is None:
            overridden[section_name] = {"top_k": top_k}
            continue
        if not isinstance(section, Mapping):
            raise TypeError(f"{section_name} section must be a mapping")

        section_copy = dict(section)
        section_copy["top_k"] = top_k
        overridden[section_name] = section_copy

    business = overridden.get("business")
    if isinstance(business, Mapping):
        business_copy = dict(business)
        fallback = business_copy.get("fallback")
        if isinstance(fallback, Mapping):
            fallback_copy = dict(fallback)
            fallback_copy["top_k"] = top_k
            business_copy["fallback"] = fallback_copy
            overridden["business"] = business_copy

    return overridden


def parse_args() -> argparse.Namespace:
    """Parse command-line arguments for the recommendation pipeline run."""
    parser = argparse.ArgumentParser(
        description="Run the similar-products recommendation pipeline over a rolling window.",
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
        "--top-k",
        type=int,
        default=None,
        help="Override recommendation top-K selection for the run.",
    )
    parser.add_argument(
        "--config-path",
        type=Path,
        default=Path("configs/baseline.yaml"),
        help="Path to baseline config YAML (default: configs/baseline.yaml).",
    )
    return parser.parse_args()


def main() -> int:
    """Run the recommendation pipeline from CLI arguments."""
    logging.basicConfig(level=logging.INFO, format="%(message)s")

    args = parse_args()
    logger = logging.getLogger(__name__)
    logger.info(
        "[run_pipeline] start train_until_date=%s lookback_days=%s config=%s",
        args.train_until_date,
        args.lookback_days,
        args.config_path,
    )

    try:
        config = load_yaml_config(args.config_path)
        config = _config_with_top_k_override(config, args.top_k)
        with tempfile.NamedTemporaryFile("w", suffix=".yaml", encoding="utf-8", delete=False) as file:
            yaml.safe_dump(config, file, sort_keys=False, allow_unicode=True)
            config_path = Path(file.name)
        run_pipeline(
            train_until_date=args.train_until_date,
            lookback_days=args.lookback_days,
            config_path=config_path,
        )
    except Exception:
        logger.exception("[run_pipeline] failed")
        return 1
    logger.info("[run_pipeline] done")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
