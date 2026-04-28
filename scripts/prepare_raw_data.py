"""Prepare raw data folders for the Ozon similar products project."""

import shutil
import tarfile
import tempfile
from pathlib import Path
from typing import TypedDict

from ozon_similar_products.config import PROJECT_ROOT, load_data_config, load_paths_config
from ozon_similar_products.paths import project_path


class ArchiveConfig(TypedDict):
    """Extraction settings for one configured archive."""

    destination: Path
    payload_root_names: list[str]


def raw_paths() -> dict[str, Path]:
    """Return configured raw data paths."""
    paths_config = load_paths_config()["data"]
    return {
        "archives": project_path(paths_config["raw_archives_dir"]),
        "product_information": project_path(paths_config["product_information_dir"]),
        "user_actions": project_path(paths_config["user_actions_dir"]),
    }


def archive_plan() -> dict[str, ArchiveConfig]:
    """Return configured tar.gz archives and their extraction targets."""
    data_config = load_data_config()
    paths = raw_paths()
    return {
        data_config["product_information"]["archive_name"]: {
            "destination": paths["product_information"],
            "payload_root_names": data_config["product_information"]["payload_root_names"],
        },
        data_config["user_actions"]["archive_name"]: {
            "destination": paths["user_actions"],
            "payload_root_names": data_config["user_actions"]["payload_root_names"],
        },
    }


def ensure_directories(paths: dict[str, Path]) -> None:
    """Create raw data directories if they do not exist."""
    for path in paths.values():
        path.mkdir(parents=True, exist_ok=True)


def move_archives(archives_dir: Path, archive_names: list[str], report_missing: bool = True) -> list[str]:
    """Move configured archives from the project root into raw archives."""
    actions: list[str] = []
    for archive_name in archive_names:
        source = PROJECT_ROOT / archive_name
        target = archives_dir / archive_name
        if source.exists() and not target.exists():
            shutil.move(str(source), str(target))
            actions.append(f"moved archive: {source.name} -> {target.relative_to(PROJECT_ROOT)}")
        elif target.exists():
            actions.append(f"archive already in place: {target.relative_to(PROJECT_ROOT)}")
        elif report_missing:
            actions.append(f"archive not found: {archive_name}")
    return actions


def has_payload(path: Path) -> bool:
    """Return True when a directory contains files other than placeholders."""
    ignored = {"README.md", ".gitkeep"}
    return path.exists() and any(child.is_file() and child.name not in ignored for child in path.rglob("*"))


def safe_extract_tar_gz(archive: Path, destination: Path, payload_root_names: list[str]) -> str:
    """Safely extract a tar.gz archive without allowing path traversal."""
    if has_payload(destination):
        return f"already extracted: {destination.relative_to(PROJECT_ROOT)}"

    raw_dir = destination.parent
    with tempfile.TemporaryDirectory(prefix="extract_", dir=raw_dir) as tmp_name:
        tmp_dir = Path(tmp_name)
        with tarfile.open(archive, mode="r:gz") as tar:
            members = tar.getmembers()
            for member in members:
                target = (tmp_dir / member.name).resolve()
                if not target.is_relative_to(tmp_dir.resolve()):
                    raise RuntimeError(f"unsafe archive member path: {member.name}")
            tar.extractall(tmp_dir, members=members)

        payload_root = normalize_payload_root(tmp_dir, payload_root_names)
        move_payload(payload_root, destination)

    return f"extracted: {archive.relative_to(PROJECT_ROOT)} -> {destination.relative_to(PROJECT_ROOT)}"


def normalize_payload_root(extracted_root: Path, payload_root_names: list[str]) -> Path:
    """Collapse a single top-level folder when it matches a configured payload name."""
    children = [child for child in extracted_root.iterdir() if child.name != ".gitkeep"]
    if len(children) == 1 and children[0].is_dir() and children[0].name in payload_root_names:
        return children[0]
    return extracted_root


def move_payload(source: Path, destination: Path) -> None:
    """Move extracted payload files into a destination directory."""
    destination.mkdir(parents=True, exist_ok=True)
    for child in source.iterdir():
        if child.name in {"README.md", ".gitkeep"}:
            continue
        target = destination / child.name
        if not target.exists():
            shutil.move(str(child), str(target))


def extract_archives(archives_dir: Path, archives: dict[str, ArchiveConfig]) -> list[str]:
    """Extract configured tar.gz archives into their raw data folders."""
    actions: list[str] = []
    for archive_name, archive_config in archives.items():
        archive = archives_dir / archive_name
        if archive.exists():
            actions.append(
                safe_extract_tar_gz(
                    archive,
                    archive_config["destination"],
                    archive_config["payload_root_names"],
                )
            )
    return actions


def remove_success_markers(paths: dict[str, Path]) -> list[str]:
    """Remove Spark marker files with the configured exact marker name."""
    marker_name = load_data_config()["raw_data"]["success_marker_name"]
    actions: list[str] = []
    for raw_path in (paths["product_information"], paths["user_actions"]):
        for marker in raw_path.rglob(marker_name):
            if marker.is_file() and marker.name == marker_name:
                marker.unlink()
                actions.append(f"removed marker: {marker.relative_to(PROJECT_ROOT)}")
    return actions


def main() -> None:
    """Run raw data preparation."""
    paths = raw_paths()
    archives = archive_plan()

    ensure_directories(paths)
    actions = []
    actions.extend(move_archives(paths["archives"], list(archives)))
    actions.extend(extract_archives(paths["archives"], archives))
    actions.extend(remove_success_markers(paths))

    print("Raw data preparation report:")
    for action in actions:
        print(f"- {action}")


if __name__ == "__main__":
    main()
