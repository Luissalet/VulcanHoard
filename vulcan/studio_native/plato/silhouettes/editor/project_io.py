"""Portable project package for the Silhouettes editor (task 21).

Responsibilities (spec §18, task 21):

* ``save_package``: write a ``.silhouettes`` ZIP containing
  ``document.json`` + ``assets/<id>/source.<ext>`` +
  ``assets/<id>/canonical.svg``.  All paths are RELATIVE — never a
  ``C:\\`` machine path (task 21 step 2).
* ``load_package``: validate and extract a package into a staging
  directory.  Rejects absolute paths, ``..`` traversal, duplicate
  members, symlinks, and oversized uncompressed content.  Verifies each
  asset's ``source_sha256`` against the extracted bytes.  Validates the
  document with ``validate_document``.  Returns ``(doc, assets_bytes)``;
  the caller assigns a new document id (task 21 step 4: never overwrite
  an existing document by name).

The package is DISTINCT from the manufacturing export bundle
(``pack_bundle`` in ``exports.py``).
"""
from __future__ import annotations

import hashlib
import io
import json
import zipfile
from pathlib import Path
from typing import Any, Mapping, Optional

from .models import validate_document, DocumentError

MAX_UNCOMPRESSED_BYTES = 256 * 1024 * 1024  # 256 MB cap


class ProjectIOError(ValueError):
    """Structured project I/O error with a stable code."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(f"{code}: {message}")
        self.code = code


# ---------------------------------------------------------------------------
# Save (task 21 step 1, 2)
# ---------------------------------------------------------------------------

def save_package(
    doc: Mapping[str, Any],
    assets_bytes: Mapping[str, bytes],
    out_path: Path,
) -> None:
    """Write a portable ``.silhouettes`` ZIP package.

    ``assets_bytes`` maps ``asset_id → source file bytes``.  The
    canonical SVG is taken from the asset's ``canonical_svg`` field if
    present, otherwise omitted.

    All member paths are relative.  No machine paths are written.
    """
    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)

    # Strip runtime-only fields before serialising
    clean_doc = _clean_for_save(doc)
    doc_json = json.dumps(clean_doc, indent=2, ensure_ascii=False, allow_nan=False)

    with zipfile.ZipFile(out_path, "w", compression=zipfile.ZIP_DEFLATED) as z:
        z.writestr("document.json", doc_json)
        for asset_id, asset in doc.get("assets", {}).items():
            source_bytes = assets_bytes.get(asset_id)
            if source_bytes is not None:
                ext = asset.get("source_type", "bin")
                z.writestr(f"assets/{asset_id}/source.{ext}", source_bytes)
            canonical = asset.get("canonical_svg")
            if canonical:
                z.writestr(f"assets/{asset_id}/canonical.svg", canonical)


def _clean_for_save(doc: Mapping[str, Any]) -> dict:
    """Remove runtime-only keys (``_geometry`` etc.) before serialising."""
    out = dict(doc)
    assets = {}
    for aid, a in doc.get("assets", {}).items():
        clean = {k: v for k, v in a.items() if not k.startswith("_")}
        assets[aid] = clean
    out["assets"] = assets
    return out


# ---------------------------------------------------------------------------
# Load (task 21 step 3, 4)
# ---------------------------------------------------------------------------

def _validate_member_name(name: str) -> None:
    """Reject absolute paths, traversal, and empty names."""
    if not name:
        raise ProjectIOError("INVALID_MEMBER", "empty member name")
    if name.startswith("/") or name.startswith("\\"):
        raise ProjectIOError("PATH_TRAVERSAL", f"absolute member path: {name!r}")
    # Windows drive letter
    if len(name) >= 2 and name[1] == ":":
        raise ProjectIOError("PATH_TRAVERSAL", f"drive-letter member path: {name!r}")
    parts = name.replace("\\", "/").split("/")
    if any(p == ".." for p in parts):
        raise ProjectIOError("PATH_TRAVERSAL", f"traversal in member path: {name!r}")
    if any(p == "" for p in parts):
        raise ProjectIOError("INVALID_MEMBER", f"empty path component in: {name!r}")


def load_package(
    zip_path: Path,
    staging_dir: Path,
) -> tuple[dict, dict[str, bytes]]:
    """Validate and extract a ``.silhouettes`` package.

    Returns ``(doc, assets_bytes)`` where ``assets_bytes`` maps
    ``asset_id → source file bytes``.

    Raises
    ------
    ProjectIOError
        PATH_TRAVERSAL, DUPLICATE_MEMBER, SYMLINK_MEMBER,
        PACKAGE_TOO_LARGE, HASH_MISMATCH, MISSING_DOCUMENT,
        or a ``DocumentError`` re-raised from ``validate_document``.
    """
    zip_path = Path(zip_path)
    staging_dir = Path(staging_dir)
    staging_dir.mkdir(parents=True, exist_ok=True)

    with zipfile.ZipFile(zip_path, "r") as z:
        # 1. Validate all member names BEFORE extracting anything
        names: list[str] = []
        for info in z.infolist():
            _validate_member_name(info.filename)
            # Symlink check: external_attr high bits encode Unix mode
            mode = (info.external_attr >> 16) & 0o170000
            if mode == 0o120000:  # S_IFLNK
                raise ProjectIOError("SYMLINK_MEMBER", f"symlink member: {info.filename!r}")
            names.append(info.filename)

        # 2. Duplicate check
        if len(names) != len(set(names)):
            dupes = sorted({n for n in names if names.count(n) > 1})
            raise ProjectIOError("DUPLICATE_MEMBER", f"duplicate members: {dupes}")

        # 3. Uncompressed size cap
        total = sum(i.file_size for i in z.infolist())
        if total > MAX_UNCOMPRESSED_BYTES:
            raise ProjectIOError(
                "PACKAGE_TOO_LARGE",
                f"uncompressed size {total} exceeds {MAX_UNCOMPRESSED_BYTES}",
            )

        # 4. Extract
        z.extractall(staging_dir)

    # 5. Read and validate the document
    doc_path = staging_dir / "document.json"
    if not doc_path.is_file():
        raise ProjectIOError("MISSING_DOCUMENT", "document.json not found in package")
    doc = json.loads(doc_path.read_text(encoding="utf-8"))

    # 6. Validate schema + structure
    validate_document(doc)

    # 7. Verify asset hashes
    assets_bytes: dict[str, bytes] = {}
    for asset_id, asset in doc.get("assets", {}).items():
        ext = asset.get("source_type", "bin")
        src = staging_dir / "assets" / asset_id / f"source.{ext}"
        if not src.is_file():
            continue  # asset bytes optional (may be re-imported)
        data = src.read_bytes()
        expected = asset.get("source_sha256")
        if expected:
            actual = hashlib.sha256(data).hexdigest()
            if actual != expected:
                raise ProjectIOError(
                    "HASH_MISMATCH",
                    f"asset {asset_id!r}: sha256 {actual} != expected {expected}",
                )
        assets_bytes[asset_id] = data

    return doc, assets_bytes
