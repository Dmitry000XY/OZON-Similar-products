"""Check the project skeleton and raw data layout."""

from ozon_similar_products.config import (
    PROJECT_ROOT,
    load_data_config,
    load_paths_config,
)
from ozon_similar_products.paths import project_path


def exists(relative_path: str) -> bool:
    """Check a path relative to the project root."""
    return project_path(relative_path).exists()


def count_parquet(relative_path: str) -> int:
    """Count parquet files below a project-relative directory."""
    path = project_path(relative_path)
    if not path.exists():
        return 0
    return sum(1 for _ in path.rglob("*.parquet"))


def configured_directories() -> list[str]:
    """Return directories expected by the current project config."""
    paths_config = load_paths_config()
    return paths_config["project_dirs"] + [
        paths_config["source"]["package_dir"],
        *paths_config["source"]["future_layer_dirs"],
    ]


def configured_modules() -> list[str]:
    """Return required Python modules from path config."""
    return load_paths_config()["source"]["required_modules"]


def configured_archives() -> list[str]:
    """Return archive names expected by data config."""
    data_config = load_data_config()
    return [
        data_config["product_information"]["archive_name"],
        data_config["user_actions"]["archive_name"],
    ]


def print_status(title: str, paths: list[str]) -> None:
    """Print OK/MISSING status for configured paths."""
    print(f"\n{title}:")
    for path in paths:
        status = "OK" if exists(path) else "MISSING"
        print(f"- {status}: {path}")


def main() -> None:
    """Print a human-readable project structure report."""
    paths_config = load_paths_config()["data"]
    archives_dir = project_path(paths_config["raw_archives_dir"])

    print("Project structure report:")
    print_status("Directories", configured_directories())
    print_status("Python modules", configured_modules())

    print("\nData:")
    product_dir = paths_config["product_information_dir"]
    user_actions_dir = paths_config["user_actions_dir"]
    print(f"- product_information parquet files: {count_parquet(product_dir)}")
    print(f"- user_actions parquet files: {count_parquet(user_actions_dir)}")

    print("\nArchives:")
    for archive_name in configured_archives():
        archive = archives_dir / archive_name
        status = "OK" if archive.exists() else "MISSING"
        print(f"- {status}: {archive.relative_to(PROJECT_ROOT)}")


if __name__ == "__main__":
    main()
