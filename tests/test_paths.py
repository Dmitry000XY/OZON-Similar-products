"""Project path tests."""

from ozon_similar_products.config import load_paths_config
from ozon_similar_products.paths import project_path


def test_key_directories_exist() -> None:
    """Configured project directories should exist."""
    paths_config = load_paths_config()
    expected = paths_config["project_dirs"] + [paths_config["source"]["package_dir"]]

    for relative_path in expected:
        assert project_path(relative_path).is_dir()
