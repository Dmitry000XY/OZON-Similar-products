"""Project-level configuration helpers."""

from pathlib import Path
from typing import Any

import yaml

PROJECT_ROOT = Path(__file__).resolve().parents[2]
ProjectConfig = dict[str, Any]


def find_project_root(start: str | Path | None = None) -> Path:
    """Find project root by locating ``configs/paths.yaml`` in parent directories."""
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


def resolve_config_path(path: str | Path) -> Path:
    """Return an absolute path for a config file."""
    config_path = Path(path)
    if config_path.is_absolute():
        return config_path
    return PROJECT_ROOT / config_path


def load_yaml_config(path: str | Path) -> dict[str, Any]:
    """Load a YAML config file as a dictionary."""
    resolved = resolve_config_path(path)
    with resolved.open("r", encoding="utf-8") as file:
        loaded = yaml.safe_load(file)

    if loaded is None:
        return {}
    if not isinstance(loaded, dict):
        msg = f"YAML config must contain a dictionary: {resolved}"
        raise TypeError(msg)
    return loaded


def load_paths_config() -> dict[str, Any]:
    """Load project path configuration."""
    return load_yaml_config("configs/paths.yaml")


def load_data_config() -> dict[str, Any]:
    """Load raw data and schema configuration."""
    return load_yaml_config("configs/data.yaml")


def load_configs(
        config_dir: str | Path = "configs",
        project_root: str | Path | None = None,
) -> ProjectConfig:
    """Load project-level ``paths`` and ``data`` configs."""
    root = find_project_root(project_root) if project_root else find_project_root()
    config_path = root / config_dir
    return {
        "project_root": root,
        "paths": load_yaml_config(config_path / "paths.yaml"),
        "data": load_yaml_config(config_path / "data.yaml"),
    }


def resolve_project_path(config: ProjectConfig, relative_path: str | Path) -> Path:
    """Resolve an absolute path from ``config['project_root']``."""
    root = Path(config["project_root"])
    return (root / relative_path).resolve()


def get_path_from_config(config: ProjectConfig, section: str, key: str) -> Path:
    """Read a path from ``paths`` config and resolve it from project root."""
    relative_path = config["paths"][section][key]
    return resolve_project_path(config, relative_path)
