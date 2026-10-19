"""Continuity constraints grounded in shared mesh edges, not global color equality."""

import hashlib, json, copy, re, unicodedata
import numpy as np
from PIL import Image
from scipy.spatial import cKDTree
from .surface_plan import features, eye_role
from .surface_regions import spatial_region_maps


def constrain(out, plans, apply=True):
    path = out / "scene/seams.json"
    if not path.exists():
        return {"available": False, "constraints": [], "changes": []}
    meshes = json.loads((out / "scene/materials.json").read_text("utf-8"))
    destination = plans
    plans = copy.deepcopy(plans)
    lookup = {(m, r["id"]): r for m, regions in plans.items() for r in regions}
    initial = {key: r["height"] for key, r in lookup.items()}
    trees = {}
    colors = {}
    classes = {}
    spatial = {}
    for item in meshes:
        m = item["material"]
        folder = out / "surfaces" / m
        with np.load(folder / "masks.npz") as data:
            trees[m] = cKDTree(data["centers"])
            spatial[m] = spatial_region_maps(data["labels"], plans[m])
        classes[m] = {r["cluster"]: r["id"] for r in plans[m]}
        with Image.open(item["source"]) as image:
            image = image.convert("RGB")
            image.thumbnail((1024, 1024), Image.Resampling.LANCZOS)
            colors[m] = np.array(image)
    links = {}
    for pair in json.loads(path.read_text("utf-8")):
        keys = []
        rgb = []
        for point in pair:
            m = point["material"]
            if m not in trees:
                break
            u, v = np.array(point["uv"]) % 1
            a = colors[m]
            color = a[
                min(a.shape[0] - 1, int((1 - v) * a.shape[0])),
                min(a.shape[1] - 1, int(u * a.shape[1])),
            ].astype(float)
            cluster = int(trees[m].query(features(color))[1])
            rid = classes[m][cluster]
            if cluster in spatial[m]:
                region_grid = spatial[m][cluster]
                rid = int(
                    region_grid[
                        min(region_grid.shape[0] - 1, int((1 - v) * region_grid.shape[0])),
                        min(region_grid.shape[1] - 1, int(u * region_grid.shape[1])),
                    ]
                )
            keys.append((m, rid))
            rgb.append(color)
        if len(keys) == 2 and np.linalg.norm(rgb[0] - rgb[1]) < 28:
            key = tuple(sorted(keys))
            links[key] = links.get(key, 0) + 1

    # A precisely identified feature can continue into an atlas where it was
    # grouped with generic skin/nose. Split that particular spatial region from
    # the generic group; never raise the entire skin to the feature's height.
    def specific(region):
        if region.get("role") in {
            "sclera",
            "iris",
            "pupil",
            "eyelid",
            "eyelash",
            "eyebrow",
            "nostril",
            "freckle",
            "hair",
            "coating",
        }:
            return region["role"]
        role = eye_role(region.get("name", ""))
        if role:
            return role
        name = "".join(
            c
            for c in unicodedata.normalize("NFKD", region.get("name", "").lower())
            if not unicodedata.combining(c)
        )
        return "hair" if re.search(r"\bpelo\b|mechon|\bhair\b|tuft", name) else None

    continuations = {}
    for (a, b), count in links.items():
        if count < 2:
            continue
        roles = [specific(lookup[k]) for k in [a, b]]
        if bool(roles[0]) == bool(roles[1]):
            refined = [lookup[k].get("semanticSource") == "focused-inspection" for k in [a, b]]
            if not all(roles) or roles[0] == roles[1] or refined[0] == refined[1]:
                continue
            source, target = (a, b) if refined[0] else (b, a)
        else:
            source, target = (a, b) if roles[0] else (b, a)
        continuations.setdefault(target, []).append(source)
    for target, sources in continuations.items():
        if len({(specific(lookup[s]), lookup[s]["height"]) for s in sources}) != 1:
            continue
        source = sources[0]
        region = lookup[target]
        exemplar = lookup[source]
        region.update(
            height=exemplar["height"],
            name=exemplar["name"],
            role=exemplar.get("role", "unspecified"),
            scenePart=exemplar.get("scenePart"),
            semanticSource=exemplar.get("semanticSource", "initial-recognition"),
            reason="Continuidad de la misma superficie en un borde UV compartido.",
            heightGroup="continued-" + hashlib.sha256(json.dumps(target).encode()).hexdigest()[:10],
        )
    parent = {k: k for k in lookup}

    def find(k):
        while parent[k] != k:
            parent[k] = parent[parent[k]]
            k = parent[k]
        return k

    def union(a, b):
        parent[find(b)] = find(a)

    groups = {}
    for key, r in lookup.items():
        if r.get("heightGroup"):
            groups.setdefault(r["heightGroup"], []).append(key)
    for keys in groups.values():
        for key in keys[1:]:
            union(keys[0], key)
    constraints = []
    for (a, b), count in links.items():
        if count < 2:
            continue
        union(a, b)
        constraints.append(
            {
                "a": a,
                "b": b,
                "samples": count,
                "difference": abs(lookup[a]["height"] - lookup[b]["height"]),
            }
        )
    components = {}
    for key in lookup:
        components.setdefault(find(key), []).append(key)
    for keys in components.values():
        if len({k[0] for k in keys}) < 2:
            continue
        # Weighted mode prefers the dominant physical surface and avoids
        # averaging an intended discrete height into a new intermediate terrace.
        votes = {}
        for key in keys:
            r = lookup[key]
            votes[r["height"]] = votes.get(r["height"], 0) + r.get("modelBounds", {}).get(
                "samples", max(1, r.get("area", 1))
            )
        height = max(votes, key=lambda h: (votes[h], -abs(h - 128)))
        group = "seam-" + hashlib.sha256(json.dumps(keys).encode()).hexdigest()[:10]
        for key in keys:
            r = lookup[key]
            r["height"] = height
            r["heightGroup"] = group
    changes = [
        {"material": key[0], "class": key[1], "before": initial[key], "after": r["height"]}
        for key, r in lookup.items()
        if initial[key] != r["height"]
    ]
    if apply:
        for material, regions in destination.items():
            for region in regions:
                region.update(lookup[(material, region["id"])])
    return {"available": True, "constraints": constraints, "changes": changes, "applied": apply}
