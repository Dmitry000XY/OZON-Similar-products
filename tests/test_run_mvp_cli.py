"""CLI tests for MVP pipeline runner."""

from pathlib import Path
from typing import cast

import pytest
import yaml

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
                "top_k": None,
                "config_path": Path("configs/baseline.yaml"),
            },
        )(),
    )

    caplog.set_level("INFO")

    exit_code = run_mvp_cli.main()

    assert exit_code == 1
    assert "failed train_until_date=2026-05-10" in caplog.text


def test_config_with_top_k_override_updates_pipeline_and_topk_sections() -> None:
    """Helper should override both top-k config locations without mutating input."""
    config = {
        "pipeline": {"top_k": 20, "lookback_days": 30},
        "topk": {"top_k": 20, "source": "behavioral"},
        "other": {"enabled": True},
    }

    overridden = run_mvp_cli._config_with_top_k_override(config, 7)

    assert overridden["pipeline"]["top_k"] == 7
    assert overridden["topk"]["top_k"] == 7
    assert overridden["other"] == {"enabled": True}
    assert config["pipeline"]["top_k"] == 20
    assert config["topk"]["top_k"] == 20


def test_run_mvp_cli_applies_top_k_override_to_temp_config(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """CLI should materialize a temporary config with the requested top-k override."""
    base_config = {
        "pipeline": {"top_k": 20},
        "topk": {"top_k": 20, "source": "behavioral"},
    }
    captured: dict[str, object] = {}

    monkeypatch.setattr(run_mvp_cli, "load_yaml_config", lambda _: base_config)
    monkeypatch.setattr(
        run_mvp_cli,
        "parse_args",
        lambda: type(
            "Args",
            (),
            {
                "train_until_date": "2026-05-10",
                "lookback_days": 7,
                "top_k": 9,
                "config_path": Path("configs/baseline.yaml"),
            },
        )(),
    )

    def fake_run_mvp_pipeline(**kwargs: object) -> None:
        config_path = Path(cast(Path, kwargs["config_path"]))
        captured["config_path"] = config_path
        captured["config"] = yaml.safe_load(config_path.read_text(encoding="utf-8"))

    monkeypatch.setattr(run_mvp_cli, "run_mvp_pipeline", fake_run_mvp_pipeline)

    assert run_mvp_cli.main() == 0

    assert cast(Path, captured["config_path"]).name == "run_mvp.override.yaml"
    config = cast(dict[str, object], captured["config"])
    assert cast(dict[str, object], config["pipeline"])["top_k"] == 9
    assert cast(dict[str, object], config["topk"])["top_k"] == 9

