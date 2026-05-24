"""Project path tests."""

from ozon_similar_products.config import load_paths_config
from ozon_similar_products.paths import get_project_paths, project_path


def test_key_directories_exist() -> None:
    """Configured project directories should exist."""
    paths_config = load_paths_config()
    expected = paths_config["project_dirs"] + [paths_config["source"]["package_dir"]]

    for relative_path in expected:
        assert project_path(relative_path).is_dir()


def test_get_project_paths_returns_resolved_paths() -> None:
    """get_project_paths should resolve configured directories lazily."""
    paths = get_project_paths(force_reload=True)

    assert paths.raw_data_dir.is_dir()
    assert paths.raw_archives_dir.is_dir()
    assert paths.outputs_dir.is_dir()
