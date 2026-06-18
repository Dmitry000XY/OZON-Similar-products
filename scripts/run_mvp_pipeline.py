"""CLI entrypoint for the MVP recommendation pipeline."""

import argparse
import logging
import sys
from pathlib import Path

import yaml

from ozon_similar_products.pipeline.run_mvp import run_mvp_pipeline


def _config_with_top_k_override(config_path: Path, top_k: int | None) -> Path:
    """Return a config path, writing a generated local config when top_k is set."""
    if top_k is None:
        return config_path

    config = yaml.safe_load(config_path.read_text(encoding="utf-8")) or {}
    if not isinstance(config, dict):
        raise TypeError("Pipeline config must contain a YAML mapping")

    pipeline_config = config.setdefault("pipeline", {})
    if not isinstance(pipeline_config, dict):
        raise TypeError("pipeline config section must be a mapping")
    pipeline_config["top_k"] = top_k

    topk_config = config.setdefault("topk", {})
    if not isinstance(topk_config, dict):
        raise TypeError("topk config section must be a mapping")
    topk_config["top_k"] = top_k

    generated_config = Path("outputs/logs") / f"generated_top_k_{top_k}.yaml"
    generated_config.parent.mkdir(parents=True, exist_ok=True)
    generated_config.write_text(
        yaml.safe_dump(config, sort_keys=False, allow_unicode=True),
        encoding="utf-8",
    )
    return generated_config


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
    parser.add_argument(
        "--top-k",
        type=int,
        default=None,
        help="Override pipeline/topk.top_k for this run.",
    )
    return parser.parse_args()


def main() -> int:
    """Run the MVP pipeline from CLI arguments."""
    logging.basicConfig(level=logging.INFO, format="%(message)s")

    args = parse_args()
    logger = logging.getLogger(__name__)
    config_path = _config_with_top_k_override(args.config_path, getattr(args, "top_k", None))

    logger.info(
        "[run_mvp_pipeline] start train_until_date=%s lookback_days=%s config=%s",
        args.train_until_date,
        args.lookback_days,
        config_path,
    )

    try:
        run_mvp_pipeline(
            train_until_date=args.train_until_date,
            lookback_days=args.lookback_days,
            config_path=config_path,
        )
    except Exception:
        logger.exception(
            "[run_mvp_pipeline] failed train_until_date=%s lookback_days=%s config=%s",
            args.train_until_date,
            args.lookback_days,
            config_path,
        )
        return 1

    logger.info("[run_mvp_pipeline] done")
    return 0


if __name__ == "__main__":
    sys.exit(main())
