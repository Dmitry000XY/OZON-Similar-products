"""Tests for raw archive preparation."""

import importlib.util
import io
import json
import sys
import tarfile
from pathlib import Path

import polars as pl
import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[1]
PREPARE_SCRIPT_PATH = PROJECT_ROOT / "scripts" / "prepare_raw_data.py"


def load_prepare_raw_data_module():
    """Load the preparation script as a module for direct function tests."""
    spec = importlib.util.spec_from_file_location(
        "prepare_raw_data",
        PREPARE_SCRIPT_PATH,
    )

    if spec is None or spec.loader is None:
        raise ImportError(f"Could not import {PREPARE_SCRIPT_PATH}")

    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)

    return module


prepare_raw_data = load_prepare_raw_data_module()
ArchiveSpec = prepare_raw_data.ArchiveSpec
safe_extract_tar_gz = prepare_raw_data.safe_extract_tar_gz


def write_product_parquet(path: Path, item_ids: list[int]) -> None:
    """Write a small product parquet file."""
    path.parent.mkdir(parents=True, exist_ok=True)

    pl.DataFrame(
        {
            "item_id": item_ids,
            "sku": [f"sku_{item_id}" for item_id in item_ids],
        }
    ).write_parquet(path)


def create_product_archive(
    archive_path: Path,
    source_root: Path,
    parquet_name: str = "products.parquet",
    item_ids: list[int] | None = None,
    include_success_file: bool = False,
) -> None:
    """Create a small product_information tar.gz archive."""
    item_ids = item_ids or [1, 2]

    payload_dir = source_root / "product_information"
    payload_dir.mkdir(parents=True, exist_ok=True)

    write_product_parquet(payload_dir / parquet_name, item_ids=item_ids)

    if include_success_file:
        (payload_dir / "_SUCCESS").write_text("", encoding="utf-8")

    with tarfile.open(archive_path, mode="w:gz") as tar:
        tar.add(payload_dir, arcname="product_information")


def make_product_spec(tmp_path: Path, archive_path: Path) -> ArchiveSpec:
    """Create an ArchiveSpec for product_information tests."""
    return ArchiveSpec(
        dataset_name="product_information",
        archive_name="product_information.tar.gz",
        archive_path=archive_path,
        extract_to=tmp_path / "data" / "raw",
        target_dir=tmp_path / "data" / "raw" / "product_information",
        payload_root_names=["product_information"],
        parquet_glob="*.parquet",
    )


def test_safe_extract_tar_gz_extracts_product_information(tmp_path: Path) -> None:
    """Product archive should be extracted into the final target directory."""
    archive_path = tmp_path / "product_information.tar.gz"
    create_product_archive(
        archive_path=archive_path,
        source_root=tmp_path / "source",
        include_success_file=True,
    )

    spec = make_product_spec(tmp_path=tmp_path, archive_path=archive_path)

    target_dir = safe_extract_tar_gz(spec)

    assert target_dir == tmp_path / "data" / "raw" / "product_information"
    assert (target_dir / "products.parquet").is_file()
    assert not (target_dir / "_SUCCESS").exists()
    assert not (tmp_path / "data" / "raw" / ".product_information.extracting").exists()

    manifest_path = target_dir / ".prepared.json"
    assert manifest_path.is_file()

    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    assert manifest["dataset_name"] == "product_information"
    assert manifest["parquet_files_count"] == 1
    assert manifest["removed_success_files_count"] == 1


def test_safe_extract_tar_gz_rejects_unsafe_paths(tmp_path: Path) -> None:
    """Archive extraction should reject path traversal entries."""
    archive_path = tmp_path / "unsafe.tar.gz"

    with tarfile.open(archive_path, mode="w:gz") as tar:
        data = b"bad"
        info = tarfile.TarInfo(name="../evil.txt")
        info.size = len(data)
        tar.addfile(info, io.BytesIO(data))

    spec = ArchiveSpec(
        dataset_name="unsafe",
        archive_name="unsafe.tar.gz",
        archive_path=archive_path,
        extract_to=tmp_path / "data" / "raw",
        target_dir=tmp_path / "data" / "raw" / "unsafe",
        payload_root_names=["unsafe"],
        parquet_glob="*.parquet",
    )

    with pytest.raises(RuntimeError, match="Unsafe path"):
        safe_extract_tar_gz(spec)

    assert not (tmp_path / "data" / "evil.txt").exists()
    assert not (tmp_path / "evil.txt").exists()
    assert not (tmp_path / "data" / "raw" / ".unsafe.extracting").exists()


def test_safe_extract_tar_gz_rebuilds_incomplete_target(tmp_path: Path) -> None:
    """Existing parquet files without .prepared.json should not be treated as ready."""
    archive_path = tmp_path / "product_information.tar.gz"
    create_product_archive(
        archive_path=archive_path,
        source_root=tmp_path / "source",
        parquet_name="fresh.parquet",
        item_ids=[10, 20],
    )

    spec = make_product_spec(tmp_path=tmp_path, archive_path=archive_path)

    incomplete_target = spec.target_dir
    incomplete_target.mkdir(parents=True)
    write_product_parquet(incomplete_target / "stale.parquet", item_ids=[999])

    target_dir = safe_extract_tar_gz(spec)

    assert target_dir == spec.target_dir
    assert not (target_dir / "stale.parquet").exists()
    assert (target_dir / "fresh.parquet").is_file()
    assert (target_dir / ".prepared.json").is_file()


def test_safe_extract_tar_gz_skips_prepared_target_without_force(
    tmp_path: Path,
) -> None:
    """Prepared datasets should be skipped unless force=True is passed."""
    archive_path = tmp_path / "product_information.tar.gz"
    create_product_archive(
        archive_path=archive_path,
        source_root=tmp_path / "source",
        parquet_name="fresh.parquet",
        item_ids=[10, 20],
    )

    spec = make_product_spec(tmp_path=tmp_path, archive_path=archive_path)

    prepared_target = spec.target_dir
    prepared_target.mkdir(parents=True)
    write_product_parquet(prepared_target / "existing.parquet", item_ids=[1])
    (prepared_target / ".prepared.json").write_text("{}", encoding="utf-8")

    target_dir = safe_extract_tar_gz(spec)

    assert target_dir == spec.target_dir
    assert (target_dir / "existing.parquet").is_file()
    assert not (target_dir / "fresh.parquet").exists()
