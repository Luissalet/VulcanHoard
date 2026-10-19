"""Keep AI-recognized printed motifs legible without using brightness as depth."""

import numpy as np
from scipy import ndimage


def preserve_patterns(labels, regions):
    changes = []
    fabrics = {}
    for region in regions:
        if region.get("role") == "fabric":
            fabrics.setdefault(region["cluster"], []).append(region)
    # Only a palette class explicitly recognized as cloth elsewhere can supply
    # its background height. Conflicting cloth heights provide no safe anchor.
    for region in regions:
        support = fabrics.get(region["cluster"], [])
        if region.get("role") != "decoration" or not support:
            continue
        heights = {r["height"] for r in support}
        if len(heights) != 1:
            continue
        height = next(iter(heights))
        old = region["height"]
        region.update(
            height=height,
            role="fabric",
            name="Fondo de " + region["name"],
            semanticGroup=region.get("semanticGroup", "pattern") + "-background",
            heightGroup=support[0].get("heightGroup", "fabric"),
            patternBackground=True,
        )
        region["reason"] = (
            "El fondo del estampado continúa el mismo tejido identificado en esta textura."
        )
        if old != height:
            changes.append(
                {"id": region["id"], "from": old, "to": height, "rule": "fabric-background"}
            )

    inks = {}
    for region in regions:
        if region.get("role") == "decoration":
            inks.setdefault(region["cluster"], []).append(region)
    masks = {c: np.isin(labels, [r["id"] for r in rows]) for c, rows in inks.items()}
    areas = {c: int(mask.sum()) for c, mask in masks.items()}
    for child in sorted(inks, key=lambda c: -areas[c]):
        mask = masks[child]
        core = ndimage.distance_transform_edt(mask) > 1.5
        if core.sum() < 24 or core.sum() < areas[child] * 0.2:
            continue
        # Cross the narrow antialiasing bands between two genuine ink colors.
        ring = ndimage.binary_dilation(mask, iterations=3) & ~mask & (labels >= 0)
        parents = []
        for parent in inks:
            if areas[parent] < areas[child] * 2:
                continue
            # Large color separation excludes ordinary edge blends and shading.
            difference = np.linalg.norm(
                np.asarray(inks[parent][0]["color"], float)
                - np.asarray(inks[child][0]["color"], float)
            )
            contact = int((ring & masks[parent]).sum())
            if difference >= 80 and contact >= max(4, ring.sum() * 0.2):
                parents.append((contact, parent))
        if not parents:
            continue
        parent = max(parents)[1]
        levels = {r["height"] for r in inks[parent]}
        if len(levels) != 1:
            continue
        parent_height = next(iter(levels))
        if any(r["height"] != parent_height for r in inks[child]):
            continue
        height = min(192, parent_height + 32)
        if height == parent_height:
            continue
        for region in inks[child]:
            region.update(
                height=height,
                heightGroup=region.get("heightGroup", "pattern") + f"-ink-{child}",
                patternDetail=True,
            )
            region["reason"] = (
                "Trazo impreso dentro de otro motivo: una altura distinta conserva su forma sin color."
            )
            changes.append(
                {"id": region["id"], "from": parent_height, "to": height, "rule": "nested-print"}
            )
    return changes
