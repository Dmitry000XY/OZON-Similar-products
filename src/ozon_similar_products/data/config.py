"""Compatibility wrappers for project-level config helpers.

Canonical config loading lives in ``ozon_similar_products.config``.
This module is kept only for backward-compatible imports in notebooks/tests.
"""

from pathlib import Path
from typing import Any

from ozon_similar_products.config import (
    ProjectConfig,
    get_path_from_config as _get_path_from_config,
    load_configs as _load_configs,
    load_yaml_config as _load_yaml_config,
    resolve_project_path as _resolve_project_path,
)


def find_project_root(start: Path | None = None) -> Path:
    """Backward-compatible root detection with local ``__file__`` fallback."""
    start_points: list[Path] = []
    if start is not None:
        start_points.append(Path(start).resolve())
    start_points.append(Path.cwd().resolve())
    start_points.append(Path(__file__).resolve())

    for start_point in start_points:
        for candidate in [start_point, *start_point.parents]:
            if (candidate / "configs" / "paths.yaml").exists():
                return candidate

    msg = "Could not find project root. Expected configs/paths.yaml somewhere above."
    raise FileNotFoundError(msg)


def load_yaml(path: str | Path) -> dict[str, Any]:
    """Backward-compatible alias for YAML loading."""
    path = Path(path)
    if not path.exists():
        msg = f"Config file not found: {path}"
        raise FileNotFoundError(msg)
    return _load_yaml_config(path)


def load_configs(
        config_dir: str | Path = "configs",
        project_root: str | Path | None = None,
) -> ProjectConfig:
    """Backward-compatible alias for project-level config loading."""
    return _load_configs(config_dir=config_dir, project_root=project_root)


def resolve_project_path(config: ProjectConfig, relative_path: str | Path) -> Path:
    """Backward-compatible alias for project-relative path resolving."""
    return _resolve_project_path(config, relative_path)


def get_path_from_config(config: ProjectConfig, section: str, key: str) -> Path:
    """Backward-compatible alias for reading paths config."""
    return _get_path_from_config(config, section, key)
