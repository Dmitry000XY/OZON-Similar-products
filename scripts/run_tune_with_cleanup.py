"""Run tuning with live cleanup of completed trial artifacts.

This wrapper is meant for long local-heavy sweeps. It keeps the package tuning
logic unchanged, but patches the trial runner so heavyweight per-trial artifacts
are removed immediately after a trial has persisted its metrics and manifest.
"""

from __future__ import annotations

import logging
import shutil
from pathlib import Path
from typing import Any

from ozon_similar_products.cli import run_tune

_ORIGINAL_RUN_TRIAL = run_tune._run_trial


def _cleanup_trial_dir(trial_dir: Path) -> list[Path]:
    removed: list[Path] = []
    for relative_path in (
        Path("artifacts"),
        Path("recommendations"),
        Path("evaluation") / "debug",
    ):
        target = trial_dir / relative_path
        if target.exists():
            shutil.rmtree(target, ignore_errors=True)
            removed.append(target)
    return removed


def _run_trial_with_cleanup(*args: Any, **kwargs: Any) -> tuple[dict[str, Any], dict[str, Any]]:
    row, trial_config = _ORIGINAL_RUN_TRIAL(*args, **kwargs)
    trial_dir = Path(str(row["run_dir"]))
    removed = _cleanup_trial_dir(trial_dir)
    if removed:
        logging.getLogger(__name__).info(
            "[run_tune] pruned_trial_artifacts trial_id=%s removed=%s",
            row.get("trial_id"),
            [path.as_posix() for path in removed],
        )
    return row, trial_config


def main() -> int:
    run_tune._run_trial = _run_trial_with_cleanup
    return run_tune.main()


if __name__ == "__main__":
    raise SystemExit(main())
