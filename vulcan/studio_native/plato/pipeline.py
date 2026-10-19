"""PNG -> foreground mask -> SVG -> filled polygons -> validated STL."""
import base64
import io
import math
from dataclasses import replace
from PIL import Image
from shapely.affinity import scale
from vulcan.studio_native.plato.silhouettes.mask import prepare_mask, mask_to_silhouette, remove_small_components
from vulcan.studio_native.plato.silhouettes.trace import trace_mask, PRESETS
from vulcan.studio_native.plato.silhouettes.vector import parse_vector, vector_to_polygons
from vulcan.studio_native.plato.silhouettes.mesh import extrude_polygons
from vulcan.studio_native.plato.silhouettes.validation import validate_mesh

# Fixed export tolerance for curve-to-polyline flattening (pixels of source).
MESH_FLATTEN_TOLERANCE_PX = 0.025


def _settings_from_params(preset, detail, speckle_area):
    if preset not in PRESETS:
        raise ValueError(f"Unknown preset: {preset}")
    settings = replace(PRESETS[preset])
    if detail is not None:
        if not math.isfinite(detail) or not 0.0 <= detail <= 3.0:
            raise ValueError("Detail must be a tolerance in 0..3 pixels")
        settings.simplify = float(detail)
    # min_area is applied to MASK COMPONENTS, never to length_threshold.
    settings.filter_speckle = 0
    return settings


def process_image(png_bytes, thickness=10.0, preset="clean", detail=None,
                  speckle_area=10.0, alpha_threshold=128):
    if not math.isfinite(thickness) or thickness <= 0:
        raise ValueError("Thickness must be positive and finite")
    settings = _settings_from_params(preset, detail, speckle_area)
    with Image.open(io.BytesIO(png_bytes)) as image:
        if image.format != "PNG":
            raise ValueError("Only PNG input is supported")
        image.load()
        width, height = image.size
        mask = prepare_mask(image, alpha_threshold)
    mask = remove_small_components(mask, speckle_area)
    silhouette_buffer = io.BytesIO()
    mask_to_silhouette(mask).save(silhouette_buffer, format="PNG")
    svg = trace_mask(mask, settings)
    vector = parse_vector(svg, tolerance=MESH_FLATTEN_TOLERANCE_PX)
    image_polygons = vector_to_polygons(vector)
    # Image/SVG +Y points down; STL/world +Y points up. Transform ONCE.
    model_polygons = [scale(p, xfact=1, yfact=-1, origin=(0, 0)) for p in image_polygons]
    mesh = extrude_polygons(model_polygons, thickness)
    metadata = validate_mesh(mesh, model_polygons, thickness)
    metadata["mesh_to_svg"] = [1, 0, 0, -1, 0, 0]
    metadata["flatten_tolerance_px"] = vector.tolerance
    metadata["trace_tolerance_px"] = settings.simplify
    metadata["corner_threshold_deg"] = settings.corner_threshold
    metadata["splice_threshold_deg"] = settings.splice_threshold
    metadata["length_threshold_px"] = settings.length_threshold
    metadata["path_precision"] = settings.path_precision
    outlines = [list(map(list, ring.coords)) for p in model_polygons
                for ring in [p.exterior, *p.interiors]]
    b64 = lambda data: base64.b64encode(data).decode("ascii")
    return {
        "silhouette_png": b64(silhouette_buffer.getvalue()),
        "svg": b64(svg.encode("utf-8")),
        "stl": b64(mesh.export(file_type="stl")),
        "width": width, "height": height,
        "outline_count": len(image_polygons),
        "triangle_count": metadata["triangle_count"],
        "metadata": metadata,
        # UI overlay must use these rings, never another regex SVG parser.
        "outline_rings": outlines,
    }
