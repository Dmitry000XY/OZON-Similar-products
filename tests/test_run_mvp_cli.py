"""CLI tests for MVP pipeline runner."""

from pathlib import Path

import pytest

import ozon_similar_products.cli.run_mvp as run_mvp_cli


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
        lambda: type(
            "Args",
            (),
            {
                "train_until_date": "2026-05-10",
                "lookback_days": 7,
                "config_path": Path("configs/baseline.yaml"),
            },
        )(),
    )

    caplog.set_level("INFO")

    exit_code = run_mvp_cli.main()

    assert exit_code == 1
    assert "failed train_until_date=2026-05-10" in caplog.text
