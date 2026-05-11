import argparse
import logging
from pathlib import Path

from ozon_similar_products.config import load_data_config, load_paths_config
from ozon_similar_products.data.archives import (
    ArchiveSpec,
    print_archive_preview,
    safe_extract_tar_gz,
)
from ozon_similar_products.paths import project_path


def build_specs() -> list[ArchiveSpec]:
    """Build archive preparation specs from project configs.

    Returns:
        Archive specifications for all raw datasets.
    """
    paths_config = load_paths_config()
    data_config = load_data_config()

    raw_dir = project_path(paths_config["data"]["raw_dir"])
    archives_dir = project_path(paths_config["data"]["raw_archives_dir"])
    product_information_dir = project_path(paths_config["data"]["product_information_dir"])
    user_actions_dir = project_path(paths_config["data"]["user_actions_dir"])

    product_cfg = data_config["product_information"]
    actions_cfg = data_config["user_actions"]

    return [
        ArchiveSpec(
            dataset_name="product_information",
            archive_name=product_cfg["archive_name"],
            archive_path=archives_dir / product_cfg["archive_name"],
            extract_to=raw_dir,
            target_dir=product_information_dir,
            payload_root_names=product_cfg["payload_root_names"],
            parquet_glob=product_cfg["parquet_glob"],
        ),
        ArchiveSpec(
            dataset_name="user_actions",
            archive_name=actions_cfg["archive_name"],
            archive_path=archives_dir / actions_cfg["archive_name"],
            extract_to=raw_dir,
            target_dir=user_actions_dir,
            payload_root_names=actions_cfg["payload_root_names"],
            parquet_glob=actions_cfg["parquet_glob"],
        ),
    ]


def parse_args() -> argparse.Namespace:
    """Parse command-line arguments.

    Returns:
        Parsed CLI arguments.
    """
    parser = argparse.ArgumentParser(description="Prepare raw parquet data from .tar.gz archives.")
    parser.add_argument(
        "--force",
        action="store_true",
        help="Remove existing prepared data and extract archives again.",
    )
    parser.add_argument(
        "--preview",
        action="store_true",
        help="Only show archive contents preview, do not extract.",
    )

    return parser.parse_args()


def main() -> None:
    """Run the raw data preparation command."""
    logging.basicConfig(level=logging.INFO, format="%(message)s")

    args = parse_args()

    project_root = Path(__file__).resolve().parents[1]
    logging.getLogger(__name__).info("[prepare] Project root: %s", project_root)

    specs = build_specs()

    for spec in specs:
        if args.preview:
            print_archive_preview(spec.archive_path)
        else:
            safe_extract_tar_gz(
                spec,
                project_root=project_root,
                force=args.force,
            )


if __name__ == "__main__":
    main()
