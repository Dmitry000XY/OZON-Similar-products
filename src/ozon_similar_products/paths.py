"""Project paths derived from configuration.

This module keeps path resolution lazy: config files are read only when paths
are requested, not during module import.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from ozon_similar_products.config import PROJECT_ROOT, load_paths_config


def project_path(relative_path: str | Path) -> Path:
    """Resolve a project-relative path."""
    path = Path(relative_path)
    if path.is_absolute():
        return path
    return PROJECT_ROOT / path


@dataclass(frozen=True)
class ProjectPaths:
    """Resolved project directory structure."""

    configs_dir: Path
    data_dir: Path
    raw_data_dir: Path
    raw_archives_dir: Path
    product_information_dir: Path
    user_actions_dir: Path
    processed_data_dir: Path
    outputs_dir: Path
    recommendations_dir: Path


def _build_project_paths(paths_config: dict[str, Any]) -> ProjectPaths:
    data_config = paths_config["data"]
    outputs_config = paths_config["outputs"]
    return ProjectPaths(
        configs_dir=project_path(paths_config["configs"]["root_dir"]),
        data_dir=project_path(data_config["raw_dir"]).parent,
        raw_data_dir=project_path(data_config["raw_dir"]),
        raw_archives_dir=project_path(data_config["raw_archives_dir"]),
        product_information_dir=project_path(data_config["product_information_dir"]),
        user_actions_dir=project_path(data_config["user_actions_dir"]),
        processed_data_dir=project_path(data_config["processed_dir"]),
        outputs_dir=project_path(outputs_config["root_dir"]),
        recommendations_dir=project_path(outputs_config["recommendations_dir"]),
    )


_PROJECT_PATHS_CACHE: ProjectPaths | None = None


def get_project_paths(*, force_reload: bool = False) -> ProjectPaths:
    """Return resolved project paths from ``configs/paths.yaml``."""
    global _PROJECT_PATHS_CACHE  # noqa: PLW0603
    if force_reload or _PROJECT_PATHS_CACHE is None:
        _PROJECT_PATHS_CACHE = _build_project_paths(load_paths_config())
    return _PROJECT_PATHS_CACHE


def __getattr__(name: str) -> Any:
    """Backward-compatible access to module-level path constants."""
    paths = get_project_paths()
    compatibility_map = {
        "CONFIGS_DIR": paths.configs_dir,
        "DATA_DIR": paths.data_dir,
        "RAW_DATA_DIR": paths.raw_data_dir,
        "RAW_ARCHIVES_DIR": paths.raw_archives_dir,
        "PRODUCT_INFORMATION_DIR": paths.product_information_dir,
        "USER_ACTIONS_DIR": paths.user_actions_dir,
        "PROCESSED_DATA_DIR": paths.processed_data_dir,
        "OUTPUTS_DIR": paths.outputs_dir,
        "RECOMMENDATIONS_DIR": paths.recommendations_dir,
    }
    if name in compatibility_map:
        return compatibility_map[name]
    raise AttributeError(name)


__all__ = [
    "ProjectPaths",
    "get_project_paths",
    "project_path",
]
