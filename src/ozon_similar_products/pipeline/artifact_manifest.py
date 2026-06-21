"""Small JSON sidecar manifests for reusable daily pipeline artifacts."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

SCHEMA_VERSION = "incremental-v1"


def _json_default(value: Any) -> Any:
    if isinstance(value, Path):
        return value.as_posix()
    if isinstance(value, datetime):
        return value.isoformat()
    return str(value)


def stable_json_dumps(payload: Any) -> str:
    """Serialize payload deterministically for fingerprints and manifests."""

    return json.dumps(
        payload,
        default=_json_default,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )


def fingerprint_payload(payload: Any) -> str:
    """Return a stable SHA-256 fingerprint for JSON-compatible payload."""

    return hashlib.sha256(stable_json_dumps(payload).encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class ArtifactManifest:
    """Manifest metadata for one reusable daily artifact group."""

    artifact_type: str
    date: str
    fingerprint: str
    paths: dict[str, str]
    rows: dict[str, int]
    metadata: dict[str, Any] = field(default_factory=dict)
    schema_version: str = SCHEMA_VERSION
    created_at: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "artifact_type": self.artifact_type,
            "date": self.date,
            "schema_version": self.schema_version,
            "fingerprint": self.fingerprint,
            "created_at": self.created_at or datetime.now(UTC).isoformat(),
            "paths": dict(self.paths),
            "rows": dict(self.rows),
            "metadata": dict(self.metadata),
        }

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> "ArtifactManifest":
        return cls(
            artifact_type=str(payload["artifact_type"]),
            date=str(payload["date"]),
            schema_version=str(payload.get("schema_version", "")),
            fingerprint=str(payload["fingerprint"]),
            created_at=(
                str(payload["created_at"])
                if payload.get("created_at") is not None
                else None
            ),
            paths={str(key): str(value) for key, value in dict(payload.get("paths", {})).items()},
            rows={str(key): int(value) for key, value in dict(payload.get("rows", {})).items()},
            metadata=dict(payload.get("metadata", {})),
        )


@dataclass(frozen=True)
class ArtifactStatus:
    """Result of validating a manifest and its referenced files."""

    valid: bool
    manifest: ArtifactManifest | None = None
    reason: str | None = None


def read_manifest(path: Path) -> ArtifactManifest | None:
    if not path.exists():
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    if not isinstance(payload, dict):
        return None
    try:
        return ArtifactManifest.from_dict(payload)
    except (KeyError, TypeError, ValueError):
        return None


def write_manifest(path: Path, manifest: ArtifactManifest) -> Path:
    """Atomically write a manifest JSON file."""

    path.parent.mkdir(parents=True, exist_ok=True)
    temp_path = path.with_suffix(path.suffix + ".tmp")
    temp_path.write_text(
        json.dumps(
            manifest.to_dict(),
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
        ),
        encoding="utf-8",
    )
    temp_path.replace(path)
    return path


def validate_manifest(
    *,
    manifest_path: Path,
    artifact_root: Path,
    artifact_type: str,
    partition_date: str,
    expected_fingerprint: str,
    required_path_keys: tuple[str, ...],
) -> ArtifactStatus:
    """Validate manifest identity, fingerprint, and required relative paths."""

    manifest = read_manifest(manifest_path)
    if manifest is None:
        return ArtifactStatus(valid=False, reason="manifest_missing_or_invalid")
    if manifest.schema_version != SCHEMA_VERSION:
        return ArtifactStatus(valid=False, manifest=manifest, reason="schema_version_mismatch")
    if manifest.artifact_type != artifact_type:
        return ArtifactStatus(valid=False, manifest=manifest, reason="artifact_type_mismatch")
    if manifest.date != partition_date:
        return ArtifactStatus(valid=False, manifest=manifest, reason="date_mismatch")
    if manifest.fingerprint != expected_fingerprint:
        return ArtifactStatus(valid=False, manifest=manifest, reason="fingerprint_mismatch")

    for key in required_path_keys:
        relative_path = manifest.paths.get(key)
        if not relative_path:
            return ArtifactStatus(valid=False, manifest=manifest, reason=f"path_missing:{key}")
        if not (artifact_root / relative_path).exists():
            return ArtifactStatus(valid=False, manifest=manifest, reason=f"file_missing:{key}")

    return ArtifactStatus(valid=True, manifest=manifest)
