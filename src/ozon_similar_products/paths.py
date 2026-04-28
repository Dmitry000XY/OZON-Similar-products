"""Project paths derived from configuration."""

from pathlib import Path

from ozon_similar_products.config import PROJECT_ROOT, load_paths_config


def project_path(relative_path: str | Path) -> Path:
    """Resolve a project-relative path."""
    path = Path(relative_path)
    if path.is_absolute():
        return path
    return PROJECT_ROOT / path


_PATHS = load_paths_config()
_DATA = _PATHS["data"]
_OUTPUTS = _PATHS["outputs"]

CONFIGS_DIR = project_path(_PATHS["configs"]["root_dir"])
DATA_DIR = project_path(_DATA["raw_dir"]).parent
RAW_DATA_DIR = project_path(_DATA["raw_dir"])
RAW_ARCHIVES_DIR = project_path(_DATA["raw_archives_dir"])
PRODUCT_INFORMATION_DIR = project_path(_DATA["product_information_dir"])
USER_ACTIONS_DIR = project_path(_DATA["user_actions_dir"])
INTERIM_DATA_DIR = project_path(_DATA["interim_dir"])
PROCESSED_DATA_DIR = project_path(_DATA["processed_dir"])
SAMPLES_DATA_DIR = project_path(_DATA["samples_dir"])
OUTPUTS_DIR = project_path(_OUTPUTS["root_dir"])
RECOMMENDATIONS_DIR = project_path(_OUTPUTS["recommendations_dir"])
