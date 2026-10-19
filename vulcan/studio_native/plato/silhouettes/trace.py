"""Binary foreground mask (255=shape) -> SVG. No rewriting of path data."""
from dataclasses import dataclass
import xml.etree.ElementTree as ET
import numpy as np

SVG_NS = "http://www.w3.org/2000/svg"
ET.register_namespace("", SVG_NS)


@dataclass
class TraceSettings:
    mode: str = "spline"
    simplify: float = 0.4  # Tolerance in pixels, NOT a normalized 0..1 value.
    path_precision: int = 4
    color_precision: int = 8
    layer_difference: int = 8
    corner_threshold: int = 90
    length_threshold: float = 4.0
    max_iterations: int = 10
    splice_threshold: int = 45
    filter_speckle: int = 0  # Component area is filtered in the mask stage.


PRESETS = {
    "pixel": TraceSettings(mode="none", simplify=0.0),
    "exact": TraceSettings(
        simplify=0.1, path_precision=5, corner_threshold=90,
        length_threshold=4.0, splice_threshold=45,
    ),
    "clean": TraceSettings(
        simplify=0.8, path_precision=5, corner_threshold=90,
        length_threshold=4.0, splice_threshold=45,
    ),
    "smooth": TraceSettings(
        simplify=1.25, path_precision=5, corner_threshold=100,
        length_threshold=4.0, splice_threshold=45,
    ),
}


def mask_to_rgba(mask: np.ndarray, pad: int = 16) -> np.ndarray:
    mask = np.asarray(mask)
    if mask.ndim != 2 or not mask.size:
        raise ValueError("Mask must be a nonempty H x W array")
    if not np.isin(mask, [0, 255]).all():
        raise ValueError("Mask convention is 255=foreground, 0=background")
    if not np.any(mask == 255):
        raise ValueError("No foreground pixels")
    if not isinstance(pad, int) or pad < 0:
        raise ValueError("Padding must be a nonnegative integer")
    h, w = mask.shape
    rgba = np.full((h + 2 * pad, w + 2 * pad, 4), 255, dtype=np.uint8)
    interior = rgba[pad:pad + h, pad:pad + w]
    interior[mask == 255] = (0, 0, 0, 255)  # The shape is BLACK, not the background.
    return rgba


def _strip_background(svg: str, width: int, height: int, pad: int = 0) -> str:
    """Compatibility name: set viewport only; DO NOT shift/rewrite any d string.

    Binary VTracer must emit shape paths. An unexpected rect is an error,
    not something to delete to conceal an inverted mask.
    """
    root = ET.fromstring(svg)
    if root.tag.rsplit("}", 1)[-1] != "svg":
        raise ValueError("VTracer did not return an SVG document")
    if any(e.tag.rsplit("}", 1)[-1] == "rect" for e in root.iter()):
        raise ValueError("Unexpected rectangle in binary tracer output")
    root.set("width", str(width))
    root.set("height", str(height))
    root.set("viewBox", f"{pad} {pad} {width} {height}")
    root.set("overflow", "hidden")
    # d attributes, fill-rule and transforms are preserved exactly as data.
    return ET.tostring(root, encoding="unicode") + "\n"


class VTracerBackend:
    def trace(self, mask: np.ndarray, settings: TraceSettings | None = None) -> str:
        # Lazy import permits diagnostics of preparation without the native wheel.
        import vtracer
        settings = settings or PRESETS["clean"]
        if not np.isfinite(settings.simplify) or settings.simplify < 0:
            raise ValueError("Trace tolerance must be finite and nonnegative")
        pad = 16
        rgba = mask_to_rgba(mask, pad)
        ph, pw = rgba.shape[:2]
        config = vtracer.Config(
            clustering="bw",
            mode=settings.mode,
            filter_speckle=settings.filter_speckle,
            color_precision=settings.color_precision,
            layer_difference=settings.layer_difference,
            corner_threshold=settings.corner_threshold,
            length_threshold=settings.length_threshold,
            max_iterations=settings.max_iterations,
            splice_threshold=settings.splice_threshold,
            simplify=settings.simplify,
            path_precision=settings.path_precision,
            optimize=0,
        )
        svg = config.convert_pixels(rgba.tobytes(order="C"), pw, ph)
        if isinstance(svg, bytes):
            svg = svg.decode("utf-8")
        h, w = mask.shape
        return _strip_background(svg, w, h, pad)


def trace_mask(mask: np.ndarray, settings: TraceSettings | None = None) -> str:
    return VTracerBackend().trace(mask, settings)
