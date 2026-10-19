"""Native resolution export of semantic surfaces, with UV-aware padding."""

import time
import numpy as np
from PIL import Image
from scipy.spatial import cKDTree
from scipy import ndimage
from .surface_plan import features
from .processing import PngRows
from .surface_regions import spatial_region_maps


def export_control(source, path):
    """Keep source alpha: transparency also participates in Figure Tools geometry."""
    alpha = source.getchannel("A") if "A" in source.getbands() else None
    if alpha is None or alpha.getextrema() == (255, 255):
        Image.new("RGBA", (8, 8), (128, 128, 128, 255)).save(path)
        return
    image = Image.new("RGBA", source.size, (128, 128, 128, 255))
    image.putalpha(alpha)
    image.save(path, compress_level=1)


def export_height(source, centers, regions, path, coverage=None, softness=1.2, labels=None):
    start = time.perf_counter()
    w, h = source.size
    tree = cKDTree(centers)
    heights = np.full(len(centers), 128, np.float32)
    for r in regions:
        heights[r["cluster"]] = r["height"]
    spatial = spatial_region_maps(labels, regions) if labels is not None else {}
    if labels is None and len({r["cluster"] for r in regions}) < len(regions):
        raise ValueError("Las zonas de un mismo color necesitan su máscara espacial.")
    region_heights = np.full(max(r["id"] for r in regions) + 1, 128, np.float32)
    for r in regions:
        region_heights[r["id"]] = r["height"]
    sigma = softness * max(w, h) / 1024
    pad = int(np.ceil(4 * sigma)) + 1
    cv = np.asarray(coverage) > 0 if coverage is not None else None
    x = (
        np.minimum((np.arange(w) * cv.shape[1] / w).astype(int), cv.shape[1] - 1)
        if cv is not None
        else None
    )
    writer = PngRows(path, w, h, bit_depth=16)
    try:
        for y in range(0, h, 128):
            end = min(y + 128, h)
            lo = max(0, y - pad)
            hi = min(h, end + pad)
            block = np.asarray(source.crop((0, lo, w, hi)).convert("RGBA"))
            valid = block[..., 3] > 0
            if cv is not None:
                yi = np.minimum((np.arange(lo, hi) * cv.shape[0] / h).astype(int), cv.shape[0] - 1)
                valid = valid & cv[yi[:, None], x[None, :]]
            out = np.full(valid.shape, 128, np.float32)
            if valid.any():
                classes = tree.query(features(block[valid, :3]), workers=2)[1]
                values = heights[classes]
                if spatial:
                    sy, sx = np.nonzero(valid)
                    sx = np.minimum((sx * labels.shape[1] / w).astype(int), labels.shape[1] - 1)
                    sy = np.minimum(
                        ((sy + lo) * labels.shape[0] / h).astype(int), labels.shape[0] - 1
                    )
                    for cluster, lookup in spatial.items():
                        chosen = classes == cluster
                        values[chosen] = region_heights[lookup[sy[chosen], sx[chosen]]]
                out[valid] = values
                # Unused UV texels may contain unrelated colors. Extend the
                # actual surface into the blur margin instead of classifying
                # that background or blending the border towards base height.
                if cv is not None and not valid.all() and sigma:
                    distance, nearest = ndimage.distance_transform_edt(~valid, return_indices=True)
                    margin = (~valid) & (distance <= pad)
                    out[margin] = out[nearest[0][margin], nearest[1][margin]]
            if sigma:
                out = ndimage.gaussian_filter(out, sigma=sigma, mode="nearest")
            rows = np.rint(out[y - lo : end - lo].clip(0, 255) * 257).astype(np.uint16)
            rgba = np.empty((*rows.shape, 4), np.uint16)
            rgba[..., :3] = rows[..., None]
            rgba[..., 3] = block[y - lo : end - lo, :, 3].astype(np.uint16) * 257
            writer.rows(rgba)
    finally:
        writer.close()
    return {
        "seconds": round(time.perf_counter() - start, 2),
        "size": [w, h],
        "bit_depth": 16,
        "softness_pixels": sigma,
        "encoding": "Figure Tools native grayscale / Non-Color; 128 reference level, no node replacement",
    }
