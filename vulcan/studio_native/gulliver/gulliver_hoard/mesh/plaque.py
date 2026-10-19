"""The title plaque of a model: the flat 2D artwork that is raised from, or engraved into, a strip of the frame around the
terrain. It holds the title of the place, a line with its coordinates and scale, a north arrow and a scale bar. Everything
here is plain shapely geometry in millimetres (model coordinates, x to the right, y up and north); turning it into a solid
is left to ``solids.prism`` and the model builder.

The artwork is drawn for a strip of a given width and height whose lower-left corner is the origin, so it can be tested and
previewed on its own and then moved to wherever the frame has room for it: ``plaque_strip`` finds the largest rectangle of
the frame along one side, ``plaque_layout`` fills a strip of that size, and ``place`` moves the result onto the rectangle.

Printing sets the limits. Bold letters have stems about a fifth of their cap height, so the text is never drawn smaller than
the size at which those stems reach the minimum stroke the printer can lay down; when a strip is too small for the text, the
scale bar goes first, then the subtitle, and a title that still does not fit is shortened with an ellipsis rather than
shrunk into slivers. Every piece of the artwork keeps a margin of 0.8 mm from the edge of the strip."""

from __future__ import annotations

import functools

import math
from typing import Any, Optional

import numpy as np
import shapely
from shapely import affinity
from shapely.geometry import LineString, Polygon, box
from shapely.ops import unary_union

from . import solids as S

MARGIN_MM = 0.8             # clear space between the artwork and the edge of the strip
EDGE_MM = 0.6               # how far a strip stays away from both edges of the frame
STEM_RATIO = 0.22           # stem width of the bold font as a fraction of its cap height (a little under the real 0.25)
BAR_SEGMENTS = 4
ELLIPSIS = "…"


# -- helpers --------------------------------------------------------------------------------------------------------------------
@functools.lru_cache(maxsize=1)
def _stem_ratio() -> float:
    """Stem width of the font the text is drawn with, as a fraction of its cap height (measured on an "I": Arial Bold
    and DejaVu Sans Bold differ), a little under the real value."""
    try:
        from PIL import Image, ImageDraw

        font = S._font(200)
        img = Image.new("L", (400, 400), 0)
        ImageDraw.Draw(img).text((100, 50), "I", font=font, fill=255)
        a = np.asarray(img) > 127
        rows, cols = np.nonzero(a)
        if len(rows) == 0:
            return STEM_RATIO
        mid = a[(rows.min() + rows.max()) // 2]
        ratio = float(mid.sum()) / float(rows.max() - rows.min() + 1)
        return max(0.08, min(STEM_RATIO, ratio * 0.8))
    except Exception:  # noqa: BLE001
        return STEM_RATIO


def _min_cap(min_stroke_mm: float) -> float:
    """The smallest cap height whose letter stems still reach ``min_stroke_mm``."""
    return float(min_stroke_mm) / _stem_ratio()


def _text_at(text: str, cap_mm: float, x: float = 0.0, y: float = 0.0) -> Any:
    """Text with the lower-left corner of its bounding box at (x, y). ``text_shape`` centres its line box on the origin and
    reports ink bounds that depend on the letters (descenders, accents), so the ink itself is measured."""
    shape = S.text_shape([text], cap_mm)
    if shape.is_empty:
        return Polygon()
    minx, miny, _maxx, _maxy = shape.bounds
    return affinity.translate(shape, x - minx, y - miny)


def _size(geom: Any) -> tuple[float, float]:
    if geom is None or geom.is_empty:
        return 0.0, 0.0
    minx, miny, maxx, maxy = geom.bounds
    return maxx - minx, maxy - miny


def _move(geom: Any, x: float, y: float) -> Any:
    """Move the lower-left corner of the bounds of ``geom`` to (x, y)."""
    if geom is None or geom.is_empty:
        return geom
    minx, miny, _a, _b = geom.bounds
    return affinity.translate(geom, x - minx, y - miny)


def _clean(geom: Any) -> Any:
    if geom is None or geom.is_empty:
        return Polygon()
    return shapely.make_valid(geom).buffer(0) if not geom.is_valid else geom.buffer(0)


# -- the north arrow ------------------------------------------------------------------------------------------------------------
def north_arrow(size_mm: float, *, min_stroke_mm: float = 0.4) -> Any:
    """A compass arrow pointing to +y with an "N" above it, centred on (0, 0) and ``size_mm`` tall.

    The arrowhead is a slim kite whose left half is solid and whose right half is an outline (the classic north-arrow
    look), so it reads clearly even at a few millimetres; when the head is too narrow to hold an outline it is solid."""
    size = float(size_mm)
    if size <= 0:
        return Polygon()
    cap = max(0.35 * size, _min_cap(min_stroke_mm))
    letter = S.text_shape(["N"], cap)
    lw, lh = _size(letter)
    gap = 0.06 * size
    head_h = max(size - lh - gap, 0.3 * size)
    half_w = 0.2 * head_h
    wall = max(0.5, float(min_stroke_mm))
    notch = 0.28 * head_h
    tip = (0.0, head_h)
    left_foot, right_foot, mid = (-half_w, 0.0), (half_w, 0.0), (0.0, notch)
    left = Polygon([tip, mid, left_foot])
    right = Polygon([tip, right_foot, mid])
    if half_w < 3.0 * wall:
        head = Polygon([tip, right_foot, mid, left_foot])
    else:
        hollow = right.buffer(-wall, join_style="mitre")
        head = unary_union([left, right.difference(hollow)])
    head = _clean(head)
    if not letter.is_empty:
        letter = _move(letter, -lw / 2.0, head_h + gap)
    art = unary_union([head, letter]) if not letter.is_empty else head
    minx, miny, maxx, maxy = art.bounds
    art = affinity.translate(art, -(minx + maxx) / 2.0, -(miny + maxy) / 2.0)
    return _clean(art)


# -- the scale bar --------------------------------------------------------------------------------------------------------------
def _ground_label(metres: float) -> str:
    if metres >= 1000:
        km = metres / 1000.0
        return f"{km:g} km"
    if metres >= 1:
        return f"{metres:g} m"
    return f"{metres * 100:g} cm"


def _round_length(scale_mm_per_m: float, max_len_mm: float) -> float:
    """The largest 1, 2 or 5 x 10^n metres whose model length does not exceed ``max_len_mm``."""
    best = None
    max_m = max_len_mm / scale_mm_per_m
    exp = int(math.floor(math.log10(max_m))) if max_m > 0 else 0
    for e in range(exp + 1, exp - 3, -1):
        for m in (5, 2, 1):
            d = m * 10.0 ** e
            if d <= max_m * (1 + 1e-9):
                best = d
                break
        if best is not None:
            break
    return best if best is not None else 10.0 ** (exp - 1)


def scale_bar(scale_mm_per_m: float, max_len_mm: float, height_mm: float, *, wall_mm: float = 0.4) -> tuple[Any, str]:
    """A checkered scale bar and its label, e.g. ``(geometry, "500 m")``.

    The ground length is the largest round value (1, 2 or 5 x 10^n metres) whose model length fits in ``max_len_mm``; as the
    steps are never more than 2.5 times apart it is always at least 0.4 of it. The bar is four segments in a frame of
    ``wall_mm``, alternate segments filled, with its lower-left corner at the origin."""
    if scale_mm_per_m <= 0 or max_len_mm <= 0:
        return Polygon(), ""
    metres = _round_length(scale_mm_per_m, max_len_mm)
    length = metres * scale_mm_per_m
    h = max(float(height_mm), 2.0 * wall_mm + 0.8)
    frame = box(0.0, 0.0, length, h).difference(box(wall_mm, wall_mm, length - wall_mm, h - wall_mm))
    seg = length / BAR_SEGMENTS
    fills = [box(i * seg, 0.0, (i + 1) * seg, h) for i in range(0, BAR_SEGMENTS, 2)]
    return _clean(unary_union([frame] + fills)), _ground_label(metres)


# -- the layout -----------------------------------------------------------------------------------------------------------------
def _fit_text(text: str, cap_mm: float, max_w: float, floor_cap: float) -> Optional[Any]:
    """The text at ``cap_mm`` or, if it is wider than ``max_w``, as large as fits but not below ``floor_cap``. A text that
    does not fit even at the floor is shortened with an ellipsis. None when not even one letter fits."""
    cap = max(cap_mm, floor_cap)
    shape = _text_at(text, cap)
    w = _size(shape)[0]
    if w > max_w:
        cap = max(floor_cap, cap * max_w / w * 0.995)
        shape = _text_at(text, cap)
        w = _size(shape)[0]
    if w <= max_w:
        return shape
    chars = text.rstrip(ELLIPSIS).rstrip()
    n = max(1, int(len(chars) * max_w / w))
    while n >= 1:
        cut = chars[:n].rstrip(" ,.-") + ELLIPSIS
        shape = _text_at(cut, cap)
        if _size(shape)[0] <= max_w:
            return shape
        n -= 1
    return None


def _build(width: float, height: float, title: str, subtitle: str, scale: float, arrow: bool, bar: bool, with_sub: bool,
           min_stroke: float) -> Optional[Any]:
    m = MARGIN_MM
    inner_w, inner_h = width - 2 * m, height - 2 * m
    if inner_w <= 0 or inner_h <= 0:
        return None
    min_cap = _min_cap(min_stroke)
    gap = max(1.5, 0.3 * height)
    pieces: list[Any] = []
    right = width - m                                   # the free area for the text ends here, moving left as items are added

    if arrow:
        art = north_arrow(inner_h, min_stroke_mm=min_stroke)
        aw, _ah = _size(art)
        art = _move(art, right - aw, m + (inner_h - _size(art)[1]) / 2.0)
        pieces.append(art)
        right = right - aw - gap

    if bar:
        label_cap = max(min_cap, 0.2 * height)
        bar_h = max(0.17 * height, 2.0 * max(0.4, min_stroke) + 0.8)
        stack = label_cap + 0.6 * label_cap + bar_h
        max_len = min(0.28 * inner_w, 60.0)
        if stack > inner_h or max_len < 8.0:
            return None
        geom, label = scale_bar(scale, max_len, bar_h, wall_mm=max(0.4, min_stroke))
        text = _text_at(label, label_cap)
        bw = max(_size(geom)[0], _size(text)[0])
        block_h = bar_h + 0.5 * label_cap + _size(text)[1]
        y0 = m + (inner_h - block_h) / 2.0
        geom = _move(geom, right - bw, y0)
        text = _move(text, right - bw, y0 + bar_h + 0.5 * label_cap)
        pieces += [geom, text]
        right = right - bw - gap

    avail = right - m
    if avail < 4 * min_cap:
        return None
    t_cap, s_cap = 0.45 * height, max(0.22 * height, min_cap)
    vgap = 0.12 * height
    # fit both lines to the width first, then both to the height together
    for _ in range(6):
        title_g = _fit_text(title, t_cap, avail, min_cap) if title else None
        if title_g is None:
            return None
        sub_g = None
        if with_sub and subtitle:
            sub_g = _fit_text(subtitle, s_cap, avail, min_cap)
            if sub_g is None or _size(sub_g)[0] > avail:
                return None
        total = _size(title_g)[1] + (vgap + _size(sub_g)[1] if sub_g is not None else 0.0)
        if total <= inner_h + 1e-6:
            break
        k = inner_h / total * 0.98
        t_cap, s_cap = t_cap * k, s_cap * k
        vgap *= k
        if t_cap < min_cap or (with_sub and subtitle and s_cap < min_cap):
            return None
    else:
        return None
    if _size(title_g)[1] > inner_h:
        return None
    total = _size(title_g)[1] + (vgap + _size(sub_g)[1] if sub_g is not None else 0.0)
    y = m + (inner_h - total) / 2.0
    if sub_g is not None:
        sub_g = _move(sub_g, m, y)
        y += _size(sub_g)[1] + vgap
    title_g = _move(title_g, m, y)
    pieces += [title_g] + ([sub_g] if sub_g is not None else [])
    return unary_union(pieces)


def plaque_layout(width_mm: float, height_mm: float, *, title: str, subtitle: str, scale_mm_per_m: float,
                  arrow: bool = True, bar: bool = True, min_stroke_mm: float = 0.4) -> Any:
    """All the artwork of a plaque strip spanning [0, width] x [0, height]: the title on the left (about 45% of the height),
    the subtitle below it (about 22%), the north arrow at the right end and, between text and arrow, the scale bar with its
    label. One valid geometry, everything at least 0.8 mm inside the strip.

    When the strip is too small the scale bar is dropped first, then the subtitle, then the arrow; the title is shortened
    with an ellipsis if it still does not fit. An empty geometry means not even the title can be drawn legibly."""
    w, h = float(width_mm), float(height_mm)
    want_bar = bool(bar) and scale_mm_per_m > 0
    want_sub = bool(subtitle)
    options: list[tuple[bool, bool, bool]] = []
    for keep_arrow in ([True, False] if arrow else [False]):
        # everything, then without the bar, then without the subtitle (keeping the bar), then the title alone
        for combo in ((want_bar, want_sub), (False, want_sub), (want_bar, False), (False, False)):
            if (keep_arrow, *combo) not in options:
                options.append((keep_arrow, *combo))
    for keep_arrow, use_bar, use_sub in options:
        geom = _build(w, h, title, subtitle, scale_mm_per_m, keep_arrow, use_bar, use_sub, min_stroke_mm)
        if geom is not None and not geom.is_empty:
            inner = box(MARGIN_MM, MARGIN_MM, w - MARGIN_MM, h - MARGIN_MM)
            return _clean(geom.intersection(inner))
    return Polygon()


# -- where the plaque goes ------------------------------------------------------------------------------------------------------
_TO_SOUTH = {"south": 0.0, "east": -90.0, "north": 180.0, "west": 90.0}


def _chord_intervals(shape: Any, y: float, minx: float, maxx: float) -> list[tuple[float, float]]:
    cut = shape.intersection(LineString([(minx - 1.0, y), (maxx + 1.0, y)]))
    parts = [cut] if cut.geom_type == "LineString" else [g for g in getattr(cut, "geoms", []) if g.geom_type == "LineString"]
    return sorted((g.bounds[0], g.bounds[2]) for g in parts if g.length > 1e-6)


def _try_rect(area: Any, h: float, lift: float, minx: float, miny: float, maxx: float) -> Optional[Polygon]:
    """The widest rectangle ``h`` tall with its bottom ``lift`` above the lowest point of ``area`` that fits inside it."""
    eps = 1e-4
    ya, yb = miny + lift, miny + lift + h
    levels = [ya + eps] + [ya + h * f for f in (0.125, 0.25, 0.5, 0.75, 0.875)] + [yb - eps]
    best = None
    for lo, hi in _chord_intervals(area, levels[0], minx, maxx):
        for y in levels[1:]:
            # the stretch of the chord at this level that overlaps what is left of the candidate the most
            over = [(max(a, lo), min(b, hi)) for a, b in _chord_intervals(area, y, minx, maxx)]
            over = [o for o in over if o[1] - o[0] > 1e-6]
            if not over:
                lo = hi = None
                break
            lo, hi = max(over, key=lambda o: o[1] - o[0])
        if lo is None or hi - lo < 3.0 * h:
            continue
        for shrink in (0.0, 0.01, 0.03, 0.06):
            pad = (hi - lo) * shrink / 2.0
            rect = box(lo + pad, ya, hi - pad, yb)
            if rect.within(area.buffer(1e-3)):
                if best is None or rect.area > best.area:
                    best = rect
                break
    return best


def _best_rect(area: Any, min_h: float = 2.0) -> Optional[Polygon]:
    """The largest axis-aligned rectangle at least three times wider than tall that lies inside ``area``, in the lower part
    of it (a horizontal strip sits along the bottom edge of the frame). Heights and lifts above the lowest point are sampled
    coarsely and then again around the best find; a rectangular frame also gets its whole band tried exactly."""
    if area is None or area.is_empty:
        return None
    minx, miny, maxx, maxy = area.bounds
    span = maxy - miny
    best: Optional[Polygon] = None

    def consider(h: float, lift: float) -> None:
        nonlocal best
        if h < min_h - 1e-9 or lift < 0 or lift + h > span * 0.75:
            return
        rect = _try_rect(area, h, lift, minx, miny, maxx)
        if rect is not None and (best is None or rect.area > best.area):
            best = rect

    consider(_largest_flush_height(area, minx, maxx, miny, maxy), 0.0)
    h_max = min(span / 2.0, 30.0)
    hs = np.linspace(min_h, max(h_max, min_h), 10)
    ls = np.concatenate([[0.0], np.linspace(0.5, span / 2.0, 13)])
    for h in hs:
        for lift in ls:
            consider(float(h), float(lift))
    if best is not None:                       # refine around the best coarse find
        h0, l0 = best.bounds[3] - best.bounds[1], best.bounds[1] - miny
        dh, dl = (hs[1] - hs[0]), (ls[2] - ls[1])
        for h in np.linspace(h0 - dh, h0 + dh, 9):
            for lift in np.linspace(max(0.0, l0 - dl), l0 + dl, 9):
                consider(float(h), float(lift))
    return best


def _largest_flush_height(area: Any, minx: float, maxx: float, miny: float, maxy: float) -> float:
    """The height of the full-width band along the bottom of ``area`` (a rectangle frame): where the chord at the middle of the
    area's width stops being solid."""
    xm = (minx + maxx) / 2.0
    cut = area.intersection(LineString([(xm, miny - 1.0), (xm, maxy + 1.0)]))
    parts = [cut] if cut.geom_type == "LineString" else [g for g in getattr(cut, "geoms", []) if g.geom_type == "LineString"]
    if not parts:
        return 0.0
    first = min(parts, key=lambda g: g.bounds[1])
    return first.bounds[3] - first.bounds[1]


def plaque_strip(outer_footprint: Any, model_footprint: Any, side: str = "south") -> Optional[tuple[Polygon, float]]:
    """The largest axis-aligned strip of the frame ring (``outer_footprint`` minus ``model_footprint``) along one side,
    kept 0.6 mm from both edges of the ring, and the rotation in degrees to apply to a layout drawn for it (0 for south,
    90 for east, -90 for west, 180 for north, so that the text always reads with its top towards the model).

    A rectangular footprint gets the whole band (for a 200 x 150 model with a 10 mm frame, 218.8 x 8.8 mm). For a circle or a
    hexagon the band under the model is a crescent, and the result is the largest wide rectangle that fits inside it, or None
    when the frame is too narrow for a legible strip (under 2 mm tall)."""
    side = side.lower()
    if side not in _TO_SOUTH:
        raise ValueError(f"unknown side {side!r}")
    ring = outer_footprint.difference(model_footprint)
    if ring.is_empty:
        return None
    rot = _TO_SOUTH[side]
    centre = outer_footprint.centroid
    turned = affinity.rotate(ring, rot, origin=(centre.x, centre.y)) if rot else ring
    eroded = turned.buffer(-EDGE_MM, join_style="mitre")
    rect = _best_rect(eroded)
    if rect is None:
        return None
    if rot:
        rect = affinity.rotate(rect, -rot, origin=(centre.x, centre.y))
        minx, miny, maxx, maxy = rect.bounds           # exact multiples of 90 degrees: snap back to an axis-aligned box
        rect = box(minx, miny, maxx, maxy)
    return rect, (180.0 if rot == 180.0 else 0.0 - rot)


def place(geom: Any, strip_rect: Any, angle: float = 0.0, layout_size: Optional[tuple[float, float]] = None) -> Any:
    """Move a layout made for a (w, h) strip at the origin onto ``strip_rect``. With ``angle`` (from ``plaque_strip``) the
    layout is turned about its own centre first; ``layout_size`` is then the (w, h) it was made for, and the rectangle is the
    turned one (taller than wide for the east and west sides)."""
    if geom is None or geom.is_empty:
        return geom
    minx, miny, maxx, maxy = strip_rect.bounds
    if not angle:
        return affinity.translate(geom, minx, miny)
    w, h = layout_size if layout_size is not None else (maxy - miny, maxx - minx) if abs(angle) % 180 == 90 else (maxx - minx, maxy - miny)
    turned = affinity.rotate(geom, angle, origin=(w / 2.0, h / 2.0))
    return affinity.translate(turned, (minx + maxx) / 2.0 - w / 2.0, (miny + maxy) / 2.0 - h / 2.0)
