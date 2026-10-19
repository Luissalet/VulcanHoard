"""Deterministic candidate masks. Geometry/semantics are handled by the vision engine.

Candidate masks are deliberately neutral: brightness is NOT inferred physical height.
Analysis runs at bounded resolution; exports preserve the source dimensions.
"""

from io import BytesIO
import colorsys
import struct
import zlib
import numpy as np
from PIL import Image, ImageDraw, ImageFont
from scipy import ndimage
from sklearn.cluster import MiniBatchKMeans
from threadpoolctl import threadpool_limits

PALETTE = [
    (167, 153, 225),
    (173, 212, 163),
    (234, 169, 183),
    (230, 195, 116),
    (130, 191, 205),
    (228, 154, 112),
    (188, 181, 166),
    (141, 164, 215),
]
HEIGHT_PALETTE = []
for _h in range(256):
    _c = tuple(round(v * 255) for v in colorsys.hsv_to_rgb(0.72 * _h / 255, 0.36, 0.9))
    while _c in HEIGHT_PALETTE:
        _c = (_c[0], _c[1], (_c[2] + 1) % 256)
    HEIGHT_PALETTE.append(_c)


def segment(image: Image.Image, clusters: int = 6, resolution: int = 768):
    work = image.convert("RGBA")
    work.thumbnail((resolution, resolution), Image.Resampling.LANCZOS)
    rgba = np.array(work)
    rgb = rgba[:, :, :3].astype(np.float32) / 255
    valid = rgba[:, :, 3] > 127
    labels = np.full(valid.shape, -1, dtype=np.int16)
    if not valid.any():
        raise ValueError("La textura es completamente transparente.")
    # Opponent color space keeps chroma useful in baked lighting/gradients.
    features = np.stack(
        [
            rgb.mean(2),
            rgb[:, :, 0] - rgb[:, :, 1],
            rgb[:, :, 2] - (rgb[:, :, 0] + rgb[:, :, 1]) / 2,
        ],
        axis=2,
    )
    pixels = features[valid]
    rng = np.random.default_rng(42)
    samples = pixels[rng.choice(len(pixels), min(18000, len(pixels)), replace=False)]
    k = min(clusters, len(np.unique(np.round(samples, 3), axis=0)))
    with threadpool_limits(limits=2):
        km = MiniBatchKMeans(n_clusters=max(1, k), random_state=42, n_init=3, batch_size=2048).fit(
            samples
        )
        labels[valid] = km.predict(pixels)
    labels = ndimage.median_filter(labels, size=3)
    labels[~valid] = -1
    # Connected components keep equal colors on disconnected parts independently editable.
    regions = []
    out = np.full(labels.shape, -1, dtype=np.int16)
    for color_id in range(k):
        components, count = ndimage.label(labels == color_id)
        sizes = np.bincount(components.ravel())
        large = [i for i in range(1, count + 1) if sizes[i] >= max(12, valid.sum() * 0.0006)]
        large.sort(key=lambda i: -sizes[i])
        # Bound visual/prompt complexity. Small fragments join the nearest retained component.
        retained = large[:12]
        if not retained:
            retained = [int(np.argmax(sizes[1:]) + 1)] if count else []
        for c in retained:
            mask = components == c
            rid = len(regions)
            out[mask] = rid
            regions.append({"id": rid, "cluster": color_id})
    missing = valid & (out < 0)
    if missing.any() and (out >= 0).any():
        _, idx = ndimage.distance_transform_edt(out < 0, return_indices=True)
        out[missing] = out[tuple(idx[:, missing])]
    regions = describe(out, rgba, regions)
    return out, regions, work, km.cluster_centers_


def segment_with_anchors(image, colors, resolution=1536):
    """Caller-selected prototypes; tiny islands join only their own color class."""
    if not 1 <= len(colors) <= 32:
        raise ValueError("Provide 1–32 RGB anchors.")
    for color in colors:
        displacement_color({"displacementColor": color})
    if len({tuple(c) for c in colors}) != len(colors):
        raise ValueError("RGB anchors must be distinct.")
    if not 256 <= resolution <= 4096:
        raise ValueError("Anchor resolution must be 256–4096.")
    work = image.convert("RGBA")
    work.thumbnail((resolution, resolution), Image.Resampling.LANCZOS)
    rgba = np.array(work)
    valid = rgba[:, :, 3] > 0
    if not valid.any():
        raise ValueError("The texture is completely transparent.")

    def features(rgb):
        rgb = np.asarray(rgb, dtype=np.float32) / 255
        return np.stack([rgb.mean(-1), rgb[..., 0] - rgb[..., 1],
                         rgb[..., 2] - (rgb[..., 0] + rgb[..., 1]) / 2], axis=-1)

    centers = features(colors)
    values = features(rgba[..., :3])
    best = np.full(valid.shape, np.inf, np.float32)
    classes = np.full(valid.shape, -1, np.int16)
    for i, center in enumerate(centers):
        distance = np.sum((values - center) ** 2, axis=-1)
        chosen = valid & (distance < best)
        classes[chosen], best[chosen] = i, distance[chosen]
    labels = np.full(valid.shape, -1, np.int16)
    regions = []
    for i in range(len(colors)):
        components, count = ndimage.label(classes == i)
        if not count:
            raise ValueError(f"Anchor {i} has no pixels at this resolution; adjust anchors or resolution.")
        sizes = np.bincount(components.ravel())
        retained = sorted(range(1, count + 1), key=lambda c: (-sizes[c], c))
        minimum = max(4, int(valid.sum() * 0.00001))
        retained = [c for c in retained if sizes[c] >= minimum][:64] or retained[:1]
        mapping = np.full(count + 1, -1, np.int16)
        for component in retained:
            rid = len(regions)
            mapping[component] = rid
            regions.append({"id": rid, "cluster": i})
        chosen = components > 0
        field = mapping[components]
        missing = chosen & (field < 0)
        if missing.any():
            _, nearest = ndimage.distance_transform_edt(field < 0, return_indices=True)
            field[missing] = field[tuple(nearest[:, missing])]
        labels[chosen] = field[chosen]
    return labels, describe(labels, rgba, regions), work, centers


def describe(labels, rgba, regions):
    result = []
    valid_count = max(1, int((labels >= 0).sum()))
    objects = ndimage.find_objects(labels.astype(np.int32) + 1)
    for region in regions:
        rid = region["id"]
        bounds = objects[rid] if rid < len(objects) else None
        if bounds is None:
            continue
        # One pixel of the actual surrounding domain preserves EDT boundary
        # behavior, including regions touching an image edge.
        y0, y1 = max(0, bounds[0].start - 1), min(labels.shape[0], bounds[0].stop + 1)
        x0, x1 = max(0, bounds[1].start - 1), min(labels.shape[1], bounds[1].stop + 1)
        mask = labels[y0:y1, x0:x1] == rid
        ys, xs = np.where(mask)
        if len(xs) == 0:
            continue
        # Label placement inside the region, away from boundaries.
        cy, cx = np.unravel_index(np.argmax(ndimage.distance_transform_edt(mask)), mask.shape)
        color = np.median(rgba[y0:y1, x0:x1, :3][mask], axis=0).astype(int).tolist()
        result.append(
            {
                **region,
                "name": f"Zona {region['id'] + 1}",
                "height": 128,
                "color": color,
                "area": round(len(xs) / valid_count * 100, 2),
                "center": [int(cx + x0), int(cy + y0)],
                "bbox": [int(xs.min() + x0), int(ys.min() + y0), int(xs.max() + x0), int(ys.max() + y0)],
                "confidence": None,
                "reason": "Pendiente de interpretación del modelo 3D.",
                "geometry": "unknown",
            }
        )
    return result


def render(labels, regions, mode="color", size=None):
    arr = np.zeros((*labels.shape, 4), dtype=np.uint8)
    for r in regions:
        h = max(0, min(255, round(r["height"])))
        if mode == "manual-palette" or (mode == "color" and r.get("displacementColor") is not None):
            c = displacement_color(r)
        elif mode == "height":
            c = (h, h, h)
        elif mode == "ids":
            c = PALETTE[r["id"] % len(PALETTE)]
        else:
            # Color represents an exact normalized level, independent of region identity.
            c = HEIGHT_PALETTE[h]
        arr[labels == r["id"]] = (*c, 255)
    im = Image.fromarray(arr)
    if mode == "ids":
        draw = ImageDraw.Draw(im)
        try:
            font = ImageFont.truetype("arial.ttf", max(12, round(max(im.size) / 48)))
        except OSError:
            font = ImageFont.load_default()
        for r in regions:
            x, y = r["center"]
            text = str(r["id"] + 1)
            box = draw.textbbox((x, y), text, font=font, anchor="mm")
            draw.rounded_rectangle(
                (box[0] - 3, box[1] - 3, box[2] + 3, box[3] + 3), radius=3, fill=(25, 29, 28, 235)
            )
            draw.text((x, y), text, font=font, fill="white", anchor="mm")
    if size and im.size != tuple(size):
        im = im.resize(size, Image.Resampling.NEAREST)
    return im


def png_bytes(image):
    out = BytesIO()
    image.save(out, format="PNG")
    return out.getvalue()


def native_blocks(source, labels, regions, centers):
    """Refine color boundaries at native resolution in 128-row blocks.

    Low-resolution nearest-component fields supply spatial identity. Native RGB
    chooses the color class: this is not an enlarged low-resolution mask.
    No H*W*K distance tensor and no full-resolution int label buffer are built.
    """
    w, h = source.size
    lh, lw = labels.shape
    fields = []
    for c in range(len(centers)):
        ids = [r["id"] for r in regions if r["cluster"] == c]
        seeds = np.isin(labels, ids)
        if not seeds.any():
            fields.append(None)
            continue
        _, indices = ndimage.distance_transform_edt(~seeds, return_indices=True)
        fields.append(labels[tuple(indices)])
    xidx = np.minimum((np.arange(w) * lw / w).astype(int), lw - 1)
    for y in range(0, h, 128):
        end = min(y + 128, h)
        block = np.array(source.crop((0, y, w, end)).convert("RGBA"))
        rgb = block[:, :, :3].astype(np.float32) / 255
        f = np.stack(
            [
                rgb.mean(2),
                rgb[:, :, 0] - rgb[:, :, 1],
                rgb[:, :, 2] - (rgb[:, :, 0] + rgb[:, :, 1]) / 2,
            ],
            axis=2,
        )
        best = np.full(f.shape[:2], np.inf, np.float32)
        color_ids = np.zeros(f.shape[:2], np.int16)
        for c, center in enumerate(centers):
            if fields[c] is None:
                continue
            dist = np.sum((f - center) ** 2, axis=2)
            better = dist < best
            best[better] = dist[better]
            color_ids[better] = c
        yi = np.minimum((np.arange(y, end) * lh / h).astype(int), lh - 1)
        mapped = np.zeros(color_ids.shape, np.int16)
        for c, field in enumerate(fields):
            if field is None:
                continue
            region_ids = field[yi[:, None], xidx[None, :]]
            mask = color_ids == c
            mapped[mask] = region_ids[mask]
        yield y, mapped, block[:, :, 3]


def displacement_color(region):
    """Exact artist RGB; never derive production color from ordinal height."""
    color = region.get("displacementColor")
    if not isinstance(color, list) or len(color) != 3 or any(
        type(v) is not int or not 0 <= v <= 255 for v in color
    ):
        raise ValueError("Every region needs an explicit displacementColor RGB triplet.")
    return color


def region_lookup(regions, mode):
    lookup = np.zeros((max(r["id"] for r in regions) + 1, 3), np.uint8)
    for r in regions:
        v = max(0, min(255, round(r["height"])))
        lookup[r["id"]] = displacement_color(r) if mode == "manual-palette" or (mode == "color" and r.get("displacementColor") is not None) else (
            (v, v, v) if mode == "height" else HEIGHT_PALETTE[v]
        )
    return lookup


def render_native(source, labels, regions, centers, mode="height"):
    output = Image.new("RGBA", source.size)
    lookup = region_lookup(regions, mode)
    for y, mapped, alpha in native_blocks(source, labels, regions, centers):
        out = np.dstack([lookup[mapped], alpha])
        output.paste(Image.fromarray(out), (0, y))
    return output


class PngRows:
    """Bounded-memory RGBA PNG writer; filter 0 favors categorical relief data."""

    def __init__(self, path, width, height, bit_depth=8):
        if bit_depth not in {8, 16}:
            raise ValueError("PNG depth must be 8 or 16 bits")
        self.bit_depth = bit_depth
        self.file = open(path, "wb")
        self.compressor = zlib.compressobj(3)
        self.file.write(b"\x89PNG\r\n\x1a\n")
        self.chunk(b"IHDR", struct.pack("!2I5B", width, height, bit_depth, 6, 0, 0, 0))

    def chunk(self, kind, data):
        self.file.write(
            struct.pack("!I", len(data))
            + kind
            + data
            + struct.pack("!I", zlib.crc32(kind + data) & 0xFFFFFFFF)
        )

    def rows(self, rgba):
        packed = (
            rgba.astype(">u2" if self.bit_depth == 16 else np.uint8)
            .view(np.uint8)
            .reshape(rgba.shape[0], -1)
        )
        raw = np.zeros((rgba.shape[0], packed.shape[1] + 1), np.uint8)
        raw[:, 1:] = packed
        data = self.compressor.compress(raw.tobytes())
        if data:
            self.chunk(b"IDAT", data)

    def close(self):
        self.chunk(b"IDAT", self.compressor.flush())
        self.chunk(b"IEND", b"")
        self.file.close()


def write_native_pair(source, labels, regions, centers, height_path, color_path):
    writers = [PngRows(height_path, *source.size), PngRows(color_path, *source.size)]
    lookups = [region_lookup(regions, "height"), region_lookup(regions, "color")]
    try:
        for _, mapped, alpha in native_blocks(source, labels, regions, centers):
            for writer, lookup in zip(writers, lookups):
                writer.rows(np.dstack([lookup[mapped], alpha]))
    finally:
        for writer in writers:
            writer.close()


def write_manual_palette(source, labels, regions, centers, path):
    """Native RGB production map, exact source alpha, no colorspace conversion."""
    lookup = region_lookup(regions, "manual-palette")
    writer = PngRows(path, *source.size)
    try:
        for _, mapped, alpha in native_blocks(source, labels, regions, centers):
            writer.rows(np.dstack([lookup[mapped], alpha]))
    finally:
        writer.close()


def solve_relations(nodes, relations):
    """Acyclic partial order; already-modeled volume does not force displacement.

    Returns normalized relative levels only. They are not world Z or millimeters.
    """
    keys = {n["key"] for n in nodes}
    edges = {k: [] for k in keys}
    indegree = {k: 0 for k in keys}
    for e in relations:
        if e["upper"] not in keys or e["lower"] not in keys:
            raise ValueError("La IA ha devuelto una relación con una zona inexistente.")
        if e.get("apply", True):
            edges[e["lower"]].append(e["upper"])
            indegree[e["upper"]] += 1
    queue = sorted(k for k in keys if not indegree[k])
    depths = {k: 0 for k in keys}
    visited = 0
    while queue:
        k = queue.pop(0)
        visited += 1
        for to in edges[k]:
            depths[to] = max(depths[to], depths[k] + 1)
            indegree[to] -= 1
            if indegree[to] == 0:
                queue.append(to)
    if visited != len(keys):
        raise ValueError(
            "La propuesta contiene un ciclo de alturas. Revisa las relaciones antes de aplicarla."
        )
    maximum = max(depths.values(), default=0)
    return {k: round(128 + 96 * d / maximum) if maximum else 128 for k, d in depths.items()}
