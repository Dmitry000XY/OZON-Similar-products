import argparse
import json
import logging
import shutil
import tarfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

PROJECT_ROOT = Path(__file__).resolve().parents[1]
LOGGER = logging.getLogger(__name__)


@dataclass(frozen=True)
class ArchiveSpec:
    """Configuration for preparing one raw data archive.

    Attributes:
        dataset_name: Human-readable dataset name for logs and manifest.
        archive_name: Archive file name expected in the archives directory.
        archive_path: Full path to the source `.tar.gz` archive.
        extract_to: Directory where a temporary extraction folder is created.
        target_dir: Final directory with prepared parquet files.
        payload_root_names: Optional root directories expected inside the archive.
        parquet_glob: Glob pattern used to find parquet files in the dataset.
    """

    dataset_name: str
    archive_name: str
    archive_path: Path
    extract_to: Path
    target_dir: Path
    payload_root_names: list[str]
    parquet_glob: str


def load_yaml(path: Path) -> dict[str, Any]:
    """Load a YAML file as a dictionary.

    Args:
        path: Path to a YAML config file.

    Returns:
        Parsed YAML content. Empty files are returned as an empty dictionary.

    Raises:
        FileNotFoundError: If the config file does not exist.
        TypeError: If the YAML root object is not a dictionary.
    """
    if not path.exists():
        raise FileNotFoundError(f"Config not found: {path}")

    with path.open("r", encoding="utf-8") as file:
        data = yaml.safe_load(file)

    if data is None:
        return {}

    if not isinstance(data, dict):
        raise TypeError(f"Config must contain a mapping: {path}")

    return data


def project_path(relative_path: str) -> Path:
    """Build an absolute project path from a relative path.

    Args:
        relative_path: Path relative to the project root.

    Returns:
        Absolute path inside the project.
    """
    return PROJECT_ROOT / relative_path


def format_path_for_manifest(path: Path) -> str:
    """Format a path for `.prepared.json`.

    Project paths are stored relative to `PROJECT_ROOT`. External paths, for
    example pytest temporary directories, are stored as absolute paths.

    Args:
        path: Path to format.

    Returns:
        String path suitable for the preparation manifest.
    """
    resolved_path = path.resolve()

    try:
        return str(resolved_path.relative_to(PROJECT_ROOT))
    except ValueError:
        return str(resolved_path)


def is_safe_member(base_dir: Path, target_path: Path) -> bool:
    """Check that a tar member stays inside the extraction directory.

    Args:
        base_dir: Directory where the archive is being extracted.
        target_path: Final path of a member from the tar archive.

    Returns:
        True if the member path is safe, otherwise False.
    """
    resolved_base_dir = base_dir.resolve()
    resolved_target_path = target_path.resolve()

    return (
        resolved_target_path == resolved_base_dir
        or resolved_base_dir in resolved_target_path.parents
    )


def path_has_parquet(path: Path, parquet_glob: str) -> bool:
    """Check whether a path contains at least one parquet file.

    Args:
        path: Directory to search in.
        parquet_glob: Glob pattern for parquet files.

    Returns:
        True if at least one parquet file is found, otherwise False.
    """
    return path.exists() and any(path.glob(parquet_glob))


def count_parquet_files(path: Path, parquet_glob: str) -> int:
    """Count parquet files in a directory.

    Args:
        path: Directory to search in.
        parquet_glob: Glob pattern for parquet files.

    Returns:
        Number of parquet files matching the pattern.
    """
    return sum(1 for _ in path.glob(parquet_glob))


def build_staging_dir(spec: ArchiveSpec) -> Path:
    """Build a temporary extraction directory for an archive.

    Args:
        spec: Archive preparation configuration.

    Returns:
        Temporary directory path used for extraction.
    """
    return spec.extract_to / f".{spec.dataset_name}.extracting"


def marker_path(spec: ArchiveSpec) -> Path:
    """Build the preparation marker path for a dataset.

    Args:
        spec: Archive preparation configuration.

    Returns:
        Path to `.prepared.json` in the final dataset directory.
    """
    return spec.target_dir / ".prepared.json"


def is_dataset_prepared(spec: ArchiveSpec) -> bool:
    """Check whether a dataset was fully prepared earlier.

    A dataset is treated as ready only when it has both the final marker and at
    least one parquet file. Partially extracted parquet files without the marker
    are not accepted as a completed preparation.

    Args:
        spec: Archive preparation configuration.

    Returns:
        True if the dataset is ready, otherwise False.
    """
    return marker_path(spec).is_file() and path_has_parquet(
        spec.target_dir,
        spec.parquet_glob,
    )


def remove_path(path: Path) -> None:
    """Remove a file or directory if it exists.

    Args:
        path: File or directory to remove.
    """
    if not path.exists():
        return

    if path.is_dir():
        shutil.rmtree(path)
    else:
        path.unlink()


def find_payload_dir(
    base_dir: Path,
    payload_root_names: list[str],
    parquet_glob: str,
) -> Path:
    """Find the directory that contains extracted parquet files.

    The function first checks expected archive root folders and then falls back
    to the extraction root itself. This lets us support archives with and
    without a top-level dataset folder.

    Args:
        base_dir: Temporary extraction directory.
        payload_root_names: Expected top-level folder names inside the archive.
        parquet_glob: Glob pattern for parquet files.

    Returns:
        Directory that contains parquet files.

    Raises:
        FileNotFoundError: If no parquet payload is found.
    """
    candidates = [base_dir / root_name for root_name in payload_root_names]
    candidates.append(base_dir)

    for candidate in candidates:
        if path_has_parquet(candidate, parquet_glob):
            return candidate

    checked = "\n".join(f"  - {path}" for path in candidates)
    raise FileNotFoundError(
        "Could not find parquet payload after extraction.\n" f"Checked:\n{checked}"
    )


def extract_tar_gz(archive_path: Path, staging_dir: Path) -> None:
    """Extract a `.tar.gz` archive into a temporary directory.

    Args:
        archive_path: Source `.tar.gz` archive.
        staging_dir: Temporary extraction directory.

    Raises:
        RuntimeError: If the archive contains an unsafe path.
    """
    with tarfile.open(archive_path, mode="r:gz") as tar:
        members = tar.getmembers()

        for member in members:
            target_path = staging_dir / member.name
            if not is_safe_member(staging_dir, target_path):
                raise RuntimeError(f"Unsafe path in archive: {member.name}")

        for member in members:
            tar.extract(member, path=staging_dir, filter="data")


def move_payload_contents(payload_dir: Path, target_dir: Path) -> None:
    """Move extracted payload contents into the final target directory.

    The top-level folder from the archive is not kept. For example,
    `user_actions_3_months/date=...` becomes `user_actions/date=...`.

    Args:
        payload_dir: Directory with extracted parquet payload.
        target_dir: Final dataset directory.
    """
    target_dir.mkdir(parents=True, exist_ok=True)

    for child in payload_dir.iterdir():
        shutil.move(str(child), str(target_dir / child.name))


def remove_success_files(path: Path) -> int:
    """Remove Spark `_SUCCESS` marker files from a prepared dataset.

    Args:
        path: Dataset directory to clean.

    Returns:
        Number of removed `_SUCCESS` markers.
    """
    removed_count = 0

    for success_path in path.rglob("_SUCCESS"):
        remove_path(success_path)
        removed_count += 1

    return removed_count


def write_manifest(
    spec: ArchiveSpec,
    parquet_files_count: int,
    removed_success_files_count: int,
) -> None:
    """Write `.prepared.json` for a successfully prepared dataset.

    Args:
        spec: Archive preparation configuration.
        parquet_files_count: Number of parquet files in the final dataset.
        removed_success_files_count: Number of removed `_SUCCESS` files.
    """
    manifest = {
        "dataset_name": spec.dataset_name,
        "archive_name": spec.archive_name,
        "archive_path": format_path_for_manifest(spec.archive_path),
        "target_dir": format_path_for_manifest(spec.target_dir),
        "parquet_glob": spec.parquet_glob,
        "payload_root_names": spec.payload_root_names,
        "parquet_files_count": parquet_files_count,
        "removed_success_files_count": removed_success_files_count,
    }

    marker_path(spec).write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def safe_extract_tar_gz(spec: ArchiveSpec, force: bool = False) -> Path:
    """Prepare one dataset from a `.tar.gz` archive.

    Existing data is accepted only when `.prepared.json` exists. Partial data
    without the marker is removed and prepared again.

    Args:
        spec: Archive preparation configuration.
        force: If True, remove existing prepared data and extract again.

    Returns:
        Final dataset directory with prepared parquet files.

    Raises:
        FileNotFoundError: If the archive does not exist or has no parquet
            payload.
        RuntimeError: If extraction finishes without parquet files.
    """
    if not spec.archive_path.exists():
        raise FileNotFoundError(f"Archive not found: {spec.archive_path}")

    if is_dataset_prepared(spec) and not force:
        LOGGER.info("[prepare] Skip %s: already prepared", spec.dataset_name)
        LOGGER.info("[prepare] Target: %s", spec.target_dir)
        LOGGER.info("[prepare] Use --force to extract again.")
        return spec.target_dir

    if spec.target_dir.exists():
        if force:
            LOGGER.info("[prepare] Removing existing target: %s", spec.target_dir)
        else:
            LOGGER.info("[prepare] Removing incomplete target: %s", spec.target_dir)
        remove_path(spec.target_dir)

    spec.extract_to.mkdir(parents=True, exist_ok=True)
    staging_dir = build_staging_dir(spec)
    remove_path(staging_dir)
    staging_dir.mkdir(parents=True)

    LOGGER.info("[prepare] Extracting dataset: %s", spec.dataset_name)
    LOGGER.info("[prepare] Archive:            %s", spec.archive_path)
    LOGGER.info("[prepare] Temporary dir:      %s", staging_dir)
    LOGGER.info("[prepare] Target:             %s", spec.target_dir)

    try:
        extract_tar_gz(spec.archive_path, staging_dir)
        payload_dir = find_payload_dir(
            base_dir=staging_dir,
            payload_root_names=spec.payload_root_names,
            parquet_glob=spec.parquet_glob,
        )
        move_payload_contents(payload_dir, spec.target_dir)

        removed_success_files_count = remove_success_files(spec.target_dir)
        parquet_files_count = count_parquet_files(
            spec.target_dir,
            spec.parquet_glob,
        )

        if parquet_files_count == 0:
            raise RuntimeError(
                f"Prepared dataset has no parquet files: {spec.target_dir}"
            )

        write_manifest(
            spec=spec,
            parquet_files_count=parquet_files_count,
            removed_success_files_count=removed_success_files_count,
        )
    finally:
        remove_path(staging_dir)

    LOGGER.info("[prepare] Done:              %s", spec.dataset_name)
    LOGGER.info("[prepare] Target:            %s", spec.target_dir)
    LOGGER.info("[prepare] Parquet files:     %s", parquet_files_count)
    LOGGER.info("[prepare] Removed _SUCCESS:  %s", removed_success_files_count)
    LOGGER.info("[prepare] Marker:            %s", marker_path(spec))

    return spec.target_dir


def print_archive_preview(archive_path: Path, limit: int = 30) -> None:
    """Log the first archive members without extracting them.

    Args:
        archive_path: Archive to inspect.
        limit: Maximum number of archive members to print.
    """
    LOGGER.info("\n[preview] %s", archive_path)

    if not archive_path.exists():
        LOGGER.info("[preview] Missing: %s", archive_path)
        return

    with tarfile.open(archive_path, mode="r:gz") as tar:
        for index, member in enumerate(tar):
            if index >= limit:
                LOGGER.info("[preview] ... first %s items shown", limit)
                break

            LOGGER.info("  %s", member.name)


def build_specs() -> list[ArchiveSpec]:
    """Build archive preparation specs from project configs.

    Returns:
        Archive specifications for all raw datasets.
    """
    paths_config = load_yaml(PROJECT_ROOT / "configs" / "paths.yaml")
    data_config = load_yaml(PROJECT_ROOT / "configs" / "data.yaml")

    raw_dir = project_path(paths_config["data"]["raw_dir"])
    archives_dir = project_path(paths_config["data"]["raw_archives_dir"])
    product_information_dir = project_path(
        paths_config["data"]["product_information_dir"]
    )
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
    parser = argparse.ArgumentParser(
        description="Prepare raw parquet data from .tar.gz archives."
    )
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

    LOGGER.info("[prepare] Project root: %s", PROJECT_ROOT)

    specs = build_specs()

    for spec in specs:
        if args.preview:
            print_archive_preview(spec.archive_path)
        else:
            safe_extract_tar_gz(spec, force=args.force)


if __name__ == "__main__":
    main()
