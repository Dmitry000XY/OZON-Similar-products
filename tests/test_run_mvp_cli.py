"""CLI tests for MVP pipeline runner."""

import importlib.util
import sys
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[1]
RUN_MVP_SCRIPT_PATH = PROJECT_ROOT / "scripts" / "run_mvp_pipeline.py"


def load_run_mvp_module():
    """Load the run_mvp_pipeline script as a module for direct function tests."""
    spec = importlib.util.spec_from_file_location(
        "run_mvp_pipeline",
        RUN_MVP_SCRIPT_PATH,
    )

    if spec is None or spec.loader is None:
        raise ImportError(f"Could not import {RUN_MVP_SCRIPT_PATH}")

    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)

    return module


run_mvp_cli = load_run_mvp_module()


def test_run_mvp_cli_reports_exception_and_exit_code(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """CLI main should log exceptions and return non-zero exit code."""
    monkeypatch.setattr(
        run_mvp_cli,
        "run_mvp_pipeline",
        lambda **_: (_ for _ in ()).throw(RuntimeError("Boom")),
    )
    monkeypatch.setattr(
        run_mvp_cli,
        "parse_args",
        lambda: type("Args", (), {
            "train_until_date": "2026-05-10",
            "lookback_days": 7,
            "config_path": Path("configs/baseline.yaml"),
        })(),
    )

    caplog.set_level("INFO")

    exit_code = run_mvp_cli.main()

    assert exit_code == 1
    assert "failed train_until_date=2026-05-10" in caplog.text
