"""Configuration helpers for YAML files."""

from pathlib import Path
from typing import Any

import yaml

PROJECT_ROOT = Path(__file__).resolve().parents[2]


def resolve_config_path(path: str | Path) -> Path:
    """Return an absolute path for a config file."""
    config_path = Path(path)
    if config_path.is_absolute():
        return config_path
    return PROJECT_ROOT / config_path


def load_yaml_config(path: str | Path) -> dict[str, Any]:
    """Load a YAML config file as a dictionary."""
    with resolve_config_path(path).open("r", encoding="utf-8") as file:
        loaded = yaml.safe_load(file)
    return loaded or {}


def load_paths_config() -> dict[str, Any]:
    """Load project path configuration."""
    return load_yaml_config("configs/paths.yaml")


def load_data_config() -> dict[str, Any]:
    """Load raw data and schema configuration."""
    return load_yaml_config("configs/data.yaml")
