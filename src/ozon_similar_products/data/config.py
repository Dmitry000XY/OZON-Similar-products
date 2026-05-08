"""Configuration helpers for the data layer."""

from pathlib import Path
from typing import Any

import yaml

ProjectConfig = dict[str, Any]


def find_project_root(start: Path | None = None) -> Path:
    """Find the project root by looking for ``configs/paths.yaml``.

    Args:
        start: Optional path to start the search from. When omitted, the
            current working directory and this file location are used.

    Returns:
        Path to the project root.

    Raises:
        FileNotFoundError: If no parent directory contains ``configs/paths.yaml``.
    """
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
    """Load a YAML file as a dictionary.

    Args:
        path: Path to a YAML file.

    Returns:
        Parsed YAML content. Empty YAML files are returned as an empty dict.

    Raises:
        FileNotFoundError: If the YAML file does not exist.
        TypeError: If the YAML root object is not a dictionary.
    """
    path = Path(path)

    if not path.exists():
        msg = f"Config file not found: {path}"
        raise FileNotFoundError(msg)

    with path.open("r", encoding="utf-8") as file:
        data = yaml.safe_load(file)

    if data is None:
        return {}

    if not isinstance(data, dict):
        msg = f"YAML config must contain a dictionary: {path}"
        raise TypeError(msg)

    return data


def load_configs(
    config_dir: str | Path = "configs",
    project_root: str | Path | None = None,
) -> ProjectConfig:
    """Load project path and data configs.

    Args:
        config_dir: Directory with project configs, relative to the project root.
        project_root: Optional explicit project root. When omitted, the root is
            detected automatically.

    Returns:
        Dictionary with ``project_root``, ``paths`` and ``data`` keys.
    """
    root = find_project_root(Path(project_root)) if project_root else find_project_root()
    config_path = root / config_dir

    return {
        "project_root": root,
        "paths": load_yaml(config_path / "paths.yaml"),
        "data": load_yaml(config_path / "data.yaml"),
    }


def resolve_project_path(config: ProjectConfig, relative_path: str | Path) -> Path:
    """Resolve a project-relative path.

    Args:
        config: Project config returned by ``load_configs``.
        relative_path: Path relative to the project root.

    Returns:
        Absolute resolved path.
    """
    root = Path(config["project_root"])
    return (root / relative_path).resolve()


def get_path_from_config(config: ProjectConfig, section: str, key: str) -> Path:
    """Read a path from ``configs/paths.yaml`` and resolve it.

    Args:
        config: Project config returned by ``load_configs``.
        section: Top-level section inside ``paths.yaml``.
        key: Path key inside the selected section.

    Returns:
        Absolute resolved path.
    """
    relative_path = config["paths"][section][key]
    return resolve_project_path(config, relative_path)
