"""Archive preparation helpers for raw datasets."""

from __future__ import annotations

import json
import logging
import shutil
import sys
import tarfile
import time
from dataclasses import dataclass
from pathlib import Path
from typing import TextIO

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


class ExtractionProgress:
    """Render archive extraction progress in terminal and CI logs.

    Interactive terminals get a one-line progress bar. Non-interactive logs,
    including CI, get periodic progress messages instead of many carriage-return
    updates.
    """

    def __init__(
        self,
        total: int,
        label: str = "[prepare] Extracting",
        stream: TextIO = sys.stderr,
        width: int = 32,
    ) -> None:
        """Create a progress reporter.

        Args:
            total: Total number of archive members to extract.
            label: Text shown before the progress indicator.
            stream: Output stream for interactive progress.
            width: Width of the visual progress bar.
        """
        self.total = max(total, 1)
        self.label = label
        self.stream = stream
        self.width = width
        self.started_at = time.monotonic()
        self.is_interactive = stream.isatty()
        self.log_every = max(self.total // 20, 1)

    def update(self, current: int, member_name: str) -> None:
        """Update progress after extracting one archive member.

        Args:
            current: Number of already extracted members.
            member_name: Name of the last extracted member.
        """
        if self.is_interactive:
            self._write_progress_bar(current=current, member_name=member_name)
            return

        should_log = current == 1 or current == self.total or current % self.log_every == 0
        if should_log:
            percent = current / self.total * 100
            LOGGER.info(
                "%s %s/%s members (%.1f%%)",
                self.label,
                current,
                self.total,
                percent,
            )

    def finish(self) -> None:
        """Finish progress output and move to the next line if needed."""
        if self.is_interactive:
            self.stream.write("\n")
            self.stream.flush()

    def _write_progress_bar(self, current: int, member_name: str) -> None:
        """Write a one-line progress bar to the output stream."""
        percent = current / self.total
        filled_width = int(self.width * percent)
        bar = "#" * filled_width + "-" * (self.width - filled_width)
        elapsed_seconds = max(time.monotonic() - self.started_at, 0.001)
        speed = current / elapsed_seconds

        short_name = member_name
        max_name_length = 48
        if len(short_name) > max_name_length:
            short_name = "..." + short_name[-max_name_length:]

        self.stream.write(
            f"\r{self.label}: [{bar}] {current}/{self.total} "
            f"({percent * 100:5.1f}%) {speed:5.1f} members/s {short_name}"
        )
        self.stream.flush()


def format_path_for_manifest(path: Path, project_root: Path) -> str:
    """Format a path for `.prepared.json`.

    Project paths are stored relative to ``project_root``. External paths are
    stored as absolute paths.
    """
    resolved_path = path.resolve()

    try:
        return str(resolved_path.relative_to(project_root))
    except ValueError:
        return str(resolved_path)


def is_safe_member(base_dir: Path, target_path: Path) -> bool:
    """Check that a tar member stays inside the extraction directory."""
    resolved_base_dir = base_dir.resolve()
    resolved_target_path = target_path.resolve()

    return (
        resolved_target_path == resolved_base_dir
        or resolved_base_dir in resolved_target_path.parents
    )


def path_has_parquet(path: Path, parquet_glob: str) -> bool:
    """Check whether a path contains at least one parquet file."""
    return path.exists() and any(path.glob(parquet_glob))


def count_parquet_files(path: Path, parquet_glob: str) -> int:
    """Count parquet files in a directory."""
    return sum(1 for _ in path.glob(parquet_glob))


def build_staging_dir(spec: ArchiveSpec) -> Path:
    """Build a temporary extraction directory for an archive."""
    return spec.extract_to / f".{spec.dataset_name}.extracting"


def marker_path(spec: ArchiveSpec) -> Path:
    """Build the preparation marker path for a dataset."""
    return spec.target_dir / ".prepared.json"


def is_dataset_prepared(spec: ArchiveSpec) -> bool:
    """Check whether a dataset was fully prepared earlier."""
    return marker_path(spec).is_file() and path_has_parquet(
        spec.target_dir,
        spec.parquet_glob,
    )


def remove_path(path: Path) -> None:
    """Remove a file or directory if it exists."""
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
    """Find the directory that contains extracted parquet files."""
    candidates = [base_dir / root_name for root_name in payload_root_names]
    candidates.append(base_dir)

    for candidate in candidates:
        if path_has_parquet(candidate, parquet_glob):
            return candidate

    checked = "\n".join(f"  - {path}" for path in candidates)
    raise FileNotFoundError(
        "Could not find parquet payload after extraction.\n"
        f"Checked:\n{checked}"
    )


def extract_tar_gz(archive_path: Path, staging_dir: Path) -> None:
    """Extract a `.tar.gz` archive into a temporary directory."""
    with tarfile.open(archive_path, mode="r:gz") as tar:
        LOGGER.info("[prepare] Reading archive index: %s", archive_path.name)
        members = tar.getmembers()
        LOGGER.info("[prepare] Archive members:      %s", len(members))

        for member in members:
            target_path = staging_dir / member.name
            if not is_safe_member(staging_dir, target_path):
                raise RuntimeError(f"Unsafe path in archive: {member.name}")

        progress = ExtractionProgress(total=len(members))
        try:
            for index, member in enumerate(members, start=1):
                tar.extract(member, path=staging_dir, filter="data")
                progress.update(current=index, member_name=member.name)
        finally:
            progress.finish()


def move_payload_contents(payload_dir: Path, target_dir: Path) -> None:
    """Move extracted payload contents into the final target directory."""
    target_dir.mkdir(parents=True, exist_ok=True)

    for child in payload_dir.iterdir():
        shutil.move(str(child), str(target_dir / child.name))


def remove_success_files(path: Path) -> int:
    """Remove Spark `_SUCCESS` marker files from a prepared dataset."""
    removed_count = 0

    for success_path in path.rglob("_SUCCESS"):
        remove_path(success_path)
        removed_count += 1

    return removed_count


def write_manifest(
    spec: ArchiveSpec,
    parquet_files_count: int,
    removed_success_files_count: int,
    project_root: Path,
) -> None:
    """Write `.prepared.json` for a successfully prepared dataset."""
    manifest = {
        "dataset_name": spec.dataset_name,
        "archive_name": spec.archive_name,
        "archive_path": format_path_for_manifest(spec.archive_path, project_root),
        "target_dir": format_path_for_manifest(spec.target_dir, project_root),
        "parquet_glob": spec.parquet_glob,
        "payload_root_names": spec.payload_root_names,
        "parquet_files_count": parquet_files_count,
        "removed_success_files_count": removed_success_files_count,
    }

    marker_path(spec).write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def safe_extract_tar_gz(
    spec: ArchiveSpec,
    project_root: Path,
    *,
    force: bool = False,
) -> Path:
    """Prepare one dataset from a `.tar.gz` archive."""
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

    parquet_files_count = 0
    removed_success_files_count = 0

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
            project_root=project_root,
        )
    finally:
        remove_path(staging_dir)

    LOGGER.info("[prepare] Done:              %s", spec.dataset_name)
    LOGGER.info("[prepare] Target:            %s", spec.target_dir)
    LOGGER.info("[prepare] Parquet files:     %s", parquet_files_count)
    LOGGER.info("[prepare] Removed _SUCCESS:  %s", removed_success_files_count)
    LOGGER.info("[prepare] Marker:            %s", marker_path(spec))

    return spec.target_dir


def print_archive_preview(archive_path: Path, *, limit: int = 30) -> None:
    """Log the first archive members without extracting them."""
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
