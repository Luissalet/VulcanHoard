"""Asset import adapter for the Silhouettes editor (task 05).

Responsibilities (spec §9, §5.3, task 05):

* ``import_asset``: convert raw PNG or SVG bytes into an ``ImportedAsset``
  record using the existing engine (``silhouettes.mask``, ``silhouettes.trace``,
  ``silhouettes.vector``).  No full pipeline call — only the stages needed
  to produce canonical SVG and polygon geometry.
* PNG path: ``prepare_mask`` → ``trace_mask`` → ``parse_vector`` →
  ``vector_to_polygons``.  The mask and tracing corrections from the
  baseline are preserved; no blur, no mask inversion, no manual ``d`` edits.
* SVG path: ``parse_vector`` → ``vector_to_polygons`` directly.
  The SVG is NOT rasterised and re-traced.
* Normalisation (spec §5.3): compute bounds of the filled geometry,
  centre at (0,0), store the source→local matrix.  The original image is
  never cropped destructively.
* Security: reject SVG containing ``<script>``, ``<foreignObject>``,
  ``on*`` event handlers, or external references before any geometry work.
  The SVG is never injected into a DOM.
* Deduplication: assets are keyed by ``source_sha256``; two layers can
  reference the same asset.  Human-readable names are not keys.

The adapter is the ONLY module that knows the internal signatures of the
raster→SVG engine.  It does not touch ``silhouettes/mesh.py`` or
``pipeline.py``.
"""
from __future__ import annotations

import hashlib
import io
import re
import uuid
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

import numpy as np
from PIL import Image

from vulcan.studio_native.plato.silhouettes.mask import prepare_mask
from vulcan.studio_native.plato.silhouettes.trace import PRESETS, trace_mask
from vulcan.studio_native.plato.silhouettes.vector import parse_vector, vector_to_polygons

# ---------------------------------------------------------------------------
# Limits (spec §6.5)
# ---------------------------------------------------------------------------

MAX_FILE_BYTES = 16 * 1024 * 1024          # 16 MB per file
MAX_BATCH_BYTES = 64 * 1024 * 1024         # 64 MB per import batch
MAX_FILES_PER_REQUEST = 16
MAX_RASTER_PIXELS = 40_000_000            # 40 megapixels

# Default calibration for uncalibrated PNG (spec §5.1):
# 25.4 mm / 96 px = 0.264583... mm per source unit
PNG_MM_PER_SOURCE_UNIT = 25.4 / 96.0

# Curve tolerance for polygonisation (spec §9.3)
PREVIEW_TOLERANCE = 0.10   # mm
EXPORT_TOLERANCE = 0.02    # mm


# ---------------------------------------------------------------------------
# Error type
# ---------------------------------------------------------------------------

class AssetImportError(ValueError):
    """Structured asset import error with a stable code."""

    def __init__(self, code: str, message: str, filename: str = "") -> None:
        super().__init__(f"{code}: {message}" + (f" ({filename})" if filename else ""))
        self.code = code
        self.message = message
        self.filename = filename


# ---------------------------------------------------------------------------
# ImportedAsset
# ---------------------------------------------------------------------------

@dataclass
class ImportedAsset:
    """Result of a successful asset import.

    All geometry is in **source units** (px for PNG, SVG user units for SVG).
    The ``normalization_pose`` maps source → local (mm, centred at origin).
    """
    asset_id: str
    source_type: str                    # "png" | "svg"
    source_filename: str = ""           # original upload name (for layer naming)
    source_sha256: str = ""
    source_bytes: bytes = b""           # immutable original
    canonical_svg: str = ""             # clean SVG string
    source_viewbox: List[float] = field(default_factory=list)  # [x, y, w, h] in source units
    mm_per_source_unit: float = 1.0     # calibration factor
    normalization_pose: Dict[str, float] = field(default_factory=dict)  # {tx, ty, scale, angle_deg}
    geometry_hash: str = ""             # sha256 of canonical SVG
    polygon_bounds: Tuple[float, float, float, float] = (0.0, 0.0, 0.0, 0.0)  # (minx, miny, maxx, maxy) in source units
    polygon_count: int = 0
    trace_settings: Dict[str, Any] = field(default_factory=dict)
    curve_tolerance_source: float = PREVIEW_TOLERANCE

    def to_document_asset(self) -> Dict[str, Any]:
        """Return the asset dict ready to be inserted into a document."""
        # Local bounds in mm (filled geometry, centred at origin after
        # normalization) — used by the 2D viewport and inspector (task 07).
        k = self.mm_per_source_unit
        x0, y0, x1, y1 = self.polygon_bounds
        local_bounds = [
            (x0 - (x0 + x1) / 2.0) * k,
            (y0 - (y0 + y1) / 2.0) * k,
            (x1 - (x0 + x1) / 2.0) * k,
            (y1 - (y0 + y1) / 2.0) * k,
        ]
        stem = ""
        if self.source_filename:
            stem = self.source_filename.rsplit(".", 1)[0] if "." in self.source_filename else self.source_filename
        return {
            "id": self.asset_id,
            "name": stem or self.asset_id,
            "source_filename": self.source_filename,
            "source_type": self.source_type,
            "source_uri": f"assets/{self.asset_id}/source.{self.source_type}",
            "canonical_svg_uri": f"assets/{self.asset_id}/canonical.svg",
            "source_sha256": self.source_sha256,
            "source_viewbox": self.source_viewbox,
            "mm_per_source_unit": self.mm_per_source_unit,
            "normalization_pose": self.normalization_pose,
            "geometry_hash": self.geometry_hash,
            "trace_settings": self.trace_settings,
            "curve_tolerance_source": self.curve_tolerance_source,
            "local_bounds": local_bounds,
        }


# ---------------------------------------------------------------------------
# SVG security checks (spec §9.2)
# ---------------------------------------------------------------------------

# Patterns that must NOT appear in an accepted SVG.
# These are checked on the raw string before any parsing.
_SVG_REJECT_PATTERNS = [
    (re.compile(r"<script[\s>]", re.IGNORECASE), "script"),
    (re.compile(r"<foreignObject[\s>]", re.IGNORECASE), "foreignObject"),
    (re.compile(r"\bon\w+\s*=", re.IGNORECASE), "on* event handler"),
    (re.compile(r"xlink:href\s*=\s*[\"']https?://", re.IGNORECASE), "external URL reference"),
    (re.compile(r"@import\s", re.IGNORECASE), "CSS @import"),
    (re.compile(r"<text[\s>]", re.IGNORECASE), "text element (convert to outlines first)"),
]


def _check_svg_security(svg_text: str, filename: str = "") -> None:
    """Reject SVG with dangerous or unsupported elements.

    Raises ``AssetImportError`` with a specific code if any pattern matches.
    """
    for pattern, label in _SVG_REJECT_PATTERNS:
        if pattern.search(svg_text):
            raise AssetImportError(
                "SVG_REJECTED",
                f"SVG contains {label}; convert to outlines in the source editor",
                filename=filename,
            )


# ---------------------------------------------------------------------------
# Normalisation (spec §5.3)
# ---------------------------------------------------------------------------

def _normalize(
    bounds: Tuple[float, float, float, float],
    mm_per_source_unit: float,
) -> Dict[str, float]:
    """Compute the source→local normalisation pose.

    ``bounds`` is (minx, miny, maxx, maxy) in source units.
    The local origin is the centre of the bounds.
    ``x_local = k * (x_source - cx)``  →  ``tx = -cx * k``
    """
    minx, miny, maxx, maxy = bounds
    cx = (minx + maxx) / 2.0
    cy = (miny + maxy) / 2.0
    k = mm_per_source_unit
    return {
        "tx": -cx * k,
        "ty": -cy * k,
        "scale": k,
        "angle_deg": 0.0,
    }


# ---------------------------------------------------------------------------
# Core import functions
# ---------------------------------------------------------------------------

def _import_png(
    data: bytes,
    filename: str,
    mm_per_source_unit: Optional[float] = None,
    *,
    smoothing: bool = True,
) -> ImportedAsset:
    """Import a PNG through the existing mask→trace→vector engine."""
    if len(data) > MAX_FILE_BYTES:
        raise AssetImportError("FILE_TOO_LARGE", f"PNG exceeds {MAX_FILE_BYTES} bytes", filename)

    try:
        image = Image.open(io.BytesIO(data))
        image.load()
    except Exception as exc:
        raise AssetImportError("INVALID_PNG", f"Cannot decode PNG: {exc}", filename)

    w, h = image.size
    if w * h > MAX_RASTER_PIXELS:
        raise AssetImportError("RASTER_TOO_LARGE", f"PNG exceeds {MAX_RASTER_PIXELS} pixels", filename)

    # Stage 1: mask (preserves baseline corrections)
    mask = prepare_mask(image)
    if (mask == 255).sum() == 0:
        raise AssetImportError("EMPTY_MASK", "PNG has no foreground pixels", filename)

    # Stage 2: trace to SVG (no blur, no inversion, no d-string edits)
    preset = "exact" if smoothing else "pixel"
    svg = trace_mask(mask, PRESETS[preset])

    # Stage 3: parse to polygons
    vec = parse_vector(svg, tolerance=PREVIEW_TOLERANCE)
    polygons = vector_to_polygons(vec)
    if not polygons:
        raise AssetImportError("NO_GEOMETRY", "Tracing produced no polygons", filename)

    # Bounds of the filled geometry (not the full image)
    all_bounds = polygons[0].bounds
    for p in polygons[1:]:
        b = p.bounds
        all_bounds = (
            min(all_bounds[0], b[0]),
            min(all_bounds[1], b[1]),
            max(all_bounds[2], b[2]),
            max(all_bounds[3], b[3]),
        )

    k = mm_per_source_unit or PNG_MM_PER_SOURCE_UNIT
    norm = _normalize(all_bounds, k)

    sha = hashlib.sha256(data).hexdigest()
    geom_hash = hashlib.sha256(svg.encode("utf-8")).hexdigest()
    asset_id = f"asset_{uuid.uuid4().hex[:12]}"

    return ImportedAsset(
        asset_id=asset_id,
        source_type="png",
        source_filename=filename,
        source_sha256=sha,
        source_bytes=data,
        canonical_svg=svg,
        source_viewbox=[0.0, 0.0, float(w), float(h)],
        mm_per_source_unit=k,
        normalization_pose=norm,
        geometry_hash=geom_hash,
        polygon_bounds=all_bounds,
        polygon_count=len(polygons),
        trace_settings={"preset": preset, "smoothing": smoothing},
        curve_tolerance_source=PREVIEW_TOLERANCE,
    )


def _import_svg(
    data: bytes,
    filename: str,
    mm_per_source_unit: Optional[float] = None,
) -> ImportedAsset:
    """Import an SVG directly through the vector parser (no rasterisation)."""
    if len(data) > MAX_FILE_BYTES:
        raise AssetImportError("FILE_TOO_LARGE", f"SVG exceeds {MAX_FILE_BYTES} bytes", filename)

    try:
        svg_text = data.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise AssetImportError("INVALID_SVG", f"SVG is not valid UTF-8: {exc}", filename)

    # Security check BEFORE any parsing
    _check_svg_security(svg_text, filename)

    # Parse directly — no rasterisation, no re-tracing
    try:
        vec = parse_vector(svg_text, tolerance=PREVIEW_TOLERANCE)
    except ValueError as exc:
        raise AssetImportError("SVG_PARSE_ERROR", str(exc), filename)

    polygons = vector_to_polygons(vec)
    if not polygons:
        raise AssetImportError("NO_GEOMETRY", "SVG contains no fillable geometry", filename)

    # Bounds
    all_bounds = polygons[0].bounds
    for p in polygons[1:]:
        b = p.bounds
        all_bounds = (
            min(all_bounds[0], b[0]),
            min(all_bounds[1], b[1]),
            max(all_bounds[2], b[2]),
            max(all_bounds[3], b[3]),
        )

    # Calibration: use SVG width/height if present, else 1.0
    k = mm_per_source_unit or 1.0
    norm = _normalize(all_bounds, k)

    sha = hashlib.sha256(data).hexdigest()
    geom_hash = hashlib.sha256(svg_text.encode("utf-8")).hexdigest()
    asset_id = f"asset_{uuid.uuid4().hex[:12]}"

    return ImportedAsset(
        asset_id=asset_id,
        source_type="svg",
        source_filename=filename,
        source_sha256=sha,
        source_bytes=data,
        canonical_svg=svg_text,
        source_viewbox=[0.0, 0.0, float(vec.width), float(vec.height)],
        mm_per_source_unit=k,
        normalization_pose=norm,
        geometry_hash=geom_hash,
        polygon_bounds=all_bounds,
        polygon_count=len(polygons),
        trace_settings={},
        curve_tolerance_source=PREVIEW_TOLERANCE,
    )


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def import_asset(
    data: bytes,
    filename: str,
    mm_per_source_unit: Optional[float] = None,
    *,
    smoothing: bool = True,
) -> ImportedAsset:
    """Import a single PNG or SVG file into an ``ImportedAsset``.

    Parameters
    ----------
    data:
        Raw file bytes.
    filename:
        Original filename (used for extension detection and error messages).
    mm_per_source_unit:
        Optional calibration override.  Defaults to ``25.4/96`` for PNG
        (spec §5.1) and ``1.0`` for SVG.
    smoothing:
        Smooth PNG contours by default. False preserves pixel edges.
        SVG geometry is imported unchanged in either mode.

    Raises
    ------
    AssetImportError
        With a stable ``code`` for every failure mode.
    """
    lower = filename.lower()
    if lower.endswith(".png"):
        return _import_png(data, filename, mm_per_source_unit, smoothing=smoothing)
    elif lower.endswith(".svg"):
        return _import_svg(data, filename, mm_per_source_unit)
    else:
        raise AssetImportError(
            "UNSUPPORTED_TYPE",
            f"Unsupported file extension: {filename!r} (expected .png or .svg)",
            filename,
        )


def import_batch(
    files: List[Tuple[str, bytes]],
    *,
    smoothing: bool = True,
) -> Tuple[List[ImportedAsset], List[Dict[str, str]]]:
    """Import a batch of files.

    Parameters
    ----------
    files:
        List of ``(filename, data)`` tuples.

    Returns
    -------
    (assets, errors):
        ``assets``: list of successfully imported ``ImportedAsset``.
        ``errors``: list of ``{"filename": ..., "code": ..., "message": ...}``.

    A batch that exceeds ``MAX_BATCH_BYTES`` or ``MAX_FILES_PER_REQUEST``
    raises ``AssetImportError`` before any file is processed.
    """
    if len(files) > MAX_FILES_PER_REQUEST:
        raise AssetImportError(
            "BATCH_TOO_LARGE",
            f"Batch exceeds {MAX_FILES_PER_REQUEST} files",
        )
    total = sum(len(d) for _, d in files)
    if total > MAX_BATCH_BYTES:
        raise AssetImportError(
            "BATCH_TOO_LARGE",
            f"Batch exceeds {MAX_BATCH_BYTES} bytes",
        )

    assets: List[ImportedAsset] = []
    errors: List[Dict[str, str]] = []
    seen_hashes: Dict[str, ImportedAsset] = {}

    for filename, data in files:
        try:
            asset = import_asset(data, filename, smoothing=smoothing)
            # Deduplicate by source hash: reuse existing asset_id
            if asset.source_sha256 in seen_hashes:
                existing = seen_hashes[asset.source_sha256]
                asset.asset_id = existing.asset_id
            else:
                seen_hashes[asset.source_sha256] = asset
            assets.append(asset)
        except AssetImportError as exc:
            errors.append({
                "filename": filename,
                "code": exc.code,
                "message": exc.message,
            })

    return assets, errors
