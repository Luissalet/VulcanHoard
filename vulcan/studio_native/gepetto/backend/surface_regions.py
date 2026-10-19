"""Spatial regions may share a palette color without sharing a height."""

import copy
import uuid
import numpy as np
from scipy import ndimage


def split_islands(labels, regions, region_id, min_area=4):
    original = next(r for r in regions if r["id"] == region_id)
    components, count = ndimage.label(labels == region_id)
    counts = np.bincount(components.ravel())
    islands = sorted(
        (i for i in range(1, count + 1) if counts[i] >= min_area), key=lambda i: -counts[i]
    )
    if len(islands) < 2:
        raise ValueError("Esta zona no contiene varias partes separadas de tamaño suficiente.")
    if len(islands) > 32:
        raise ValueError("Hay demasiadas partes pequeñas para separarlas de una vez.")
    masks = [components == i for i in islands]
    residual = (labels == region_id) & ~np.isin(components, islands)
    if residual.any():
        masks.append(residual)
    out = labels.copy()
    result = [copy.deepcopy(r) for r in regions if r["id"] != region_id]
    next_id = max(r["id"] for r in regions) + 1
    group_prefix = uuid.uuid4().hex[:12]
    for index, mask in enumerate(masks):
        rid = region_id if index == 0 else next_id + index - 1
        y, x = np.nonzero(mask)
        out[mask] = rid
        region = copy.deepcopy(original)
        region.update(
            id=rid,
            name=f"{original['name']} · zona {index + 1}",
            pixelCount=int(mask.sum()),
            area=round(mask.sum() / max(1, (labels >= 0).sum()) * 100, 2),
            center=[float(x.mean()), float(y.mean())],
            bbox=[int(x.min()), int(y.min()), int(x.max()), int(y.max())],
            semanticGroup=f"island-{group_prefix}-{rid}",
            heightGroup=f"island-{group_prefix}-{rid}",
        )
        region.pop("modelBounds", None)
        result.append(region)
    return out, sorted(result, key=lambda r: r["id"])


def spatial_region_maps(labels, regions):
    """Resolve duplicate color classes by their nearest UV region, at analysis size.

    Native color classification still determines exact high-resolution edges.
    The spatial lookup only distinguishes separate parts with that same color.
    """
    clusters = {}
    for r in regions:
        clusters.setdefault(r["cluster"], []).append(r["id"])
    maps = {}
    for cluster, ids in clusters.items():
        if len(ids) < 2:
            continue
        mask = np.isin(labels, ids)
        if not mask.any():
            raise ValueError("La máscara espacial no contiene las zonas del grupo.")
        nearest = ndimage.distance_transform_edt(~mask, return_distances=False, return_indices=True)
        maps[cluster] = labels[tuple(nearest)]
    return maps


def separate_palette_islands(labels, regions, max_regions=64):
    """Give vision separate IDs for sizeable disconnected parts of one color."""
    for region in list(regions):
        mask = labels == region["id"]
        components, count = ndimage.label(mask)
        counts = np.bincount(components.ravel())[1:]
        # Rasterized antialiasing may create dozens of four-pixel fragments.
        # They keep their original color classification, but do not each need
        # an independent semantic ID. Scale the split threshold to the atlas.
        minimum = max(4, int(mask.sum() * 0.005), int(labels.size * 0.0001))
        significant = int(np.sum(counts >= minimum))
        if significant < 2 or significant > 32:
            continue
        residual = bool(np.any(counts < minimum))
        if len(regions) + significant - 1 + int(residual) > max_regions:
            continue
        labels, regions = split_islands(labels, regions, region["id"], min_area=minimum)
    return labels, regions
