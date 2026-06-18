"""Architecture guardrails for cleanup refactor."""

from __future__ import annotations

import re
from pathlib import Path

IMPORT_ARCHIVE_PATTERN = re.compile(
    r"^\s*(from|import)\s+docs\.archive(?:\b|\.)",
    flags=re.MULTILINE,
)


def _python_files(root: Path) -> list[Path]:
    return sorted(path for path in root.rglob("*.py") if path.is_file())


def test_runtime_layers_do_not_import_archive_code() -> None:
    """Runtime code must not import archived helpers from docs/archive/code."""
    project_root = Path(__file__).resolve().parents[1]
    scan_roots = [
        project_root / "src",
        project_root / "tests",
        project_root / "scripts",
    ]

    offenders: list[str] = []
    for root in scan_roots:
        for path in _python_files(root):
            content = path.read_text(encoding="utf-8")
            if IMPORT_ARCHIVE_PATTERN.search(content):
                offenders.append(path.relative_to(project_root).as_posix())

    assert offenders == [], (
        "Archive imports are forbidden in src/tests/scripts: "
        + ", ".join(offenders)
    )


def test_no_eda_prefixed_modules_outside_diagnostics() -> None:
    """Production package should not keep eda_* modules outside diagnostics."""
    project_root = Path(__file__).resolve().parents[1]
    package_root = project_root / "src" / "ozon_similar_products"
    diagnostics_root = package_root / "diagnostics"

    offenders: list[str] = []
    for path in sorted(package_root.rglob("eda_*.py")):
        if not path.is_relative_to(diagnostics_root):
            offenders.append(path.relative_to(project_root).as_posix())

    assert offenders == [], (
        "eda_* modules are allowed only in diagnostics: "
        + ", ".join(offenders)
    )
