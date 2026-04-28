from __future__ import annotations

import argparse
import json
import shutil
import tarfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml


PROJECT_ROOT = Path(__file__).resolve().parents[1]


@dataclass(frozen=True)
class ArchiveSpec:
    dataset_name: str
    archive_name: str
    archive_path: Path
    extract_to: Path
    target_dir: Path
    payload_root_names: list[str]
    parquet_glob: str


def load_yaml(path: Path) -> dict[str, Any]:
    if not path.exists():
        raise FileNotFoundError(f"Config not found: {path}")

    with path.open("r", encoding="utf-8") as file:
        return yaml.safe_load(file)


def project_path(relative_path: str) -> Path:
    return PROJECT_ROOT / relative_path


def is_safe_member(base_dir: Path, target_path: Path) -> bool:
    """
    Защита от path traversal внутри tar-архива.
    Архив не должен иметь возможность записать файл за пределы extract_to.
    """
    base_dir = base_dir.resolve()
    target_path = target_path.resolve()
    return target_path == base_dir or base_dir in target_path.parents


def path_has_parquet(path: Path, parquet_glob: str) -> bool:
    if not path.exists():
        return False

    return any(path.glob(parquet_glob))


def get_candidate_payload_dirs(spec: ArchiveSpec) -> list[Path]:
    """
    Возможные места, где после распаковки окажется parquet-датасет.

    Для product_information:
      extract_to = data/raw
      target_dir = data/raw/product_information

    Для user_actions:
      extract_to = data/raw/user_actions
      target_dir = data/raw/user_actions
      payload может лежать в data/raw/user_actions/user_actions_3_months
    """
    candidates: list[Path] = []

    candidates.append(spec.target_dir)

    for root_name in spec.payload_root_names:
        candidates.append(spec.extract_to / root_name)
        candidates.append(spec.target_dir / root_name)

    unique_candidates: list[Path] = []
    seen: set[Path] = set()

    for candidate in candidates:
        resolved = candidate.resolve()
        if resolved not in seen:
            seen.add(resolved)
            unique_candidates.append(candidate)

    return unique_candidates


def find_existing_payload_dir(spec: ArchiveSpec) -> Path | None:
    for candidate in get_candidate_payload_dirs(spec):
        if path_has_parquet(candidate, spec.parquet_glob):
            return candidate

    return None


def safe_extract_tar_gz(spec: ArchiveSpec, force: bool = False) -> Path:
    if not spec.archive_path.exists():
        raise FileNotFoundError(f"Archive not found: {spec.archive_path}")

    existing_payload_dir = find_existing_payload_dir(spec)

    if existing_payload_dir is not None and not force:
        print(f"[prepare] Skip {spec.dataset_name}: already prepared")
        print(f"[prepare] Existing payload: {existing_payload_dir}")
        print("[prepare] Use --force to extract again.")
        return existing_payload_dir

    if force and spec.target_dir.exists():
        print(f"[prepare] Removing existing target: {spec.target_dir}")
        shutil.rmtree(spec.target_dir)

    spec.extract_to.mkdir(parents=True, exist_ok=True)

    print(f"[prepare] Extracting dataset: {spec.dataset_name}")
    print(f"[prepare] Archive:            {spec.archive_path}")
    print(f"[prepare] Extract to:         {spec.extract_to}")

    with tarfile.open(spec.archive_path, mode="r:gz") as tar:
        members = tar.getmembers()

        for member in members:
            target_path = spec.extract_to / member.name

            if not is_safe_member(spec.extract_to, target_path):
                raise RuntimeError(f"Unsafe path in archive: {member.name}")

        tar.extractall(spec.extract_to)

    payload_dir = find_existing_payload_dir(spec)

    if payload_dir is None:
        candidates = "\n".join(f"  - {path}" for path in get_candidate_payload_dirs(spec))
        raise RuntimeError(
            f"Extraction finished, but parquet payload was not found for "
            f"{spec.dataset_name}.\nChecked:\n{candidates}"
        )

    parquet_files_count = sum(1 for _ in payload_dir.glob(spec.parquet_glob))

    manifest = {
        "dataset_name": spec.dataset_name,
        "archive_name": spec.archive_name,
        "archive_path": str(spec.archive_path.relative_to(PROJECT_ROOT)),
        "extract_to": str(spec.extract_to.relative_to(PROJECT_ROOT)),
        "target_dir": str(spec.target_dir.relative_to(PROJECT_ROOT)),
        "payload_dir": str(payload_dir.relative_to(PROJECT_ROOT)),
        "parquet_glob": spec.parquet_glob,
        "parquet_files_count": parquet_files_count,
    }

    spec.target_dir.mkdir(parents=True, exist_ok=True)

    marker_path = spec.target_dir / ".prepared.json"
    marker_path.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    print(f"[prepare] Done:              {spec.dataset_name}")
    print(f"[prepare] Payload:           {payload_dir}")
    print(f"[prepare] Parquet files:     {parquet_files_count}")
    print(f"[prepare] Marker:            {marker_path}")

    return payload_dir


def print_archive_preview(archive_path: Path, limit: int = 30) -> None:
    print(f"\n[preview] {archive_path}")

    if not archive_path.exists():
        print(f"[preview] Missing: {archive_path}")
        return

    with tarfile.open(archive_path, mode="r:gz") as tar:
        for index, member in enumerate(tar):
            if index >= limit:
                print(f"[preview] ... first {limit} items shown")
                break

            print(f"  {member.name}")


def build_specs() -> list[ArchiveSpec]:
    paths_config = load_yaml(PROJECT_ROOT / "configs" / "paths.yaml")
    data_config = load_yaml(PROJECT_ROOT / "configs" / "data.yaml")

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
            extract_to=user_actions_dir,
            target_dir=user_actions_dir,
            payload_root_names=actions_cfg["payload_root_names"],
            parquet_glob=actions_cfg["parquet_glob"],
        ),
    ]


def main() -> None:
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
    args = parser.parse_args()

    print(f"[prepare] Project root: {PROJECT_ROOT}")

    specs = build_specs()

    for spec in specs:
        if args.preview:
            print_archive_preview(spec.archive_path)
        else:
            safe_extract_tar_gz(spec, force=args.force)


if __name__ == "__main__":
    main()