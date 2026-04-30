"""Tests for raw archive preparation."""

from __future__ import annotations

import importlib.util
import io
import sys
import tarfile
from pathlib import Path

import polars as pl
import pytest


PROJECT_ROOT = Path(__file__).resolve().parents[1]
PREPARE_SCRIPT_PATH = PROJECT_ROOT / "scripts" / "prepare_raw_data.py"


def load_prepare_raw_data_module():
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


def test_safe_extract_tar_gz_extracts_product_information(tmp_path: Path) -> None:
    """Product archive should be extracted into the expected directory."""
    archive_path = tmp_path / "product_information.tar.gz"

    source_dir = tmp_path / "source" / "product_information"
    source_dir.mkdir(parents=True)

    pl.DataFrame(
        {
            "item_id": [1, 2],
            "sku": ["sku_1", "sku_2"],
        }
    ).write_parquet(source_dir / "products.parquet")

    with tarfile.open(archive_path, mode="w:gz") as tar:
        tar.add(source_dir, arcname="product_information")

    spec = ArchiveSpec(
        dataset_name="product_information",
        archive_name="product_information.tar.gz",
        archive_path=archive_path,
        extract_to=tmp_path / "data" / "raw",
        target_dir=tmp_path / "data" / "raw" / "product_information",
        payload_root_names=["product_information"],
        parquet_glob="*.parquet",
    )

    payload_dir = safe_extract_tar_gz(spec)

    assert payload_dir == tmp_path / "data" / "raw" / "product_information"
    assert (payload_dir / "products.parquet").is_file()
    assert (payload_dir / ".prepared.json").is_file()


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
