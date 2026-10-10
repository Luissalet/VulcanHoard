"""Built-in solid modelling for the parametric CAD generator: BSP-tree CSG on triangle soups, in pure Python.

No boolean backend ships with the app (no OpenSCAD, CadQuery or manifold3d), so union / difference / intersection are
done here with the classic BSP-tree algorithm (split polygons by planes, keep the pieces outside / inside the other
solid). It is exact enough for printable parts built from primitives (hundreds of triangles); a cheap bounding-box
shortcut skips the BSP work when two solids do not touch. T-junctions that the splitting leaves behind are repaired
afterwards, so the result is normally watertight. When `manifold3d` happens to be installed, trimesh's own boolean
engine is used instead (`backend()` says which one is active).
"""

from __future__ import annotations

import sys
import time

import numpy as np

EPS = 1e-7
_COPLANAR, _FRONT, _BACK, _SPANNING = 0, 1, 2, 3


class CsgTimeout(RuntimeError):
    """The boolean took longer than the time budget: the part is too complex for the built-in engine."""


def backend() -> str:
    """Name of the boolean engine in use: 'manifold' (trimesh + manifold3d, if installed) or 'bsp' (built in)."""
    try:
        import manifold3d  # noqa: F401
        return "manifold"
    except Exception:  # noqa: BLE001 - optional dependency
        return "bsp"


def empty_mesh() -> tuple[np.ndarray, np.ndarray]:
    return np.zeros((0, 3)), np.zeros((0, 3), dtype=np.int64)


# ---------- polygons ----------
class Poly:
    __slots__ = ("v", "n", "w")

    def __init__(self, verts, n, w):
        self.v, self.n, self.w = verts, n, w

    def flip(self) -> None:
        self.v.reverse()
        self.n = (-self.n[0], -self.n[1], -self.n[2])
        self.w = -self.w


def _poly(verts):
    """A polygon from vertices, with its plane from Newell's method; None when it has no area."""
    nx = ny = nz = 0.0
    count = len(verts)
    for i in range(count):
        x1, y1, z1 = verts[i]
        x2, y2, z2 = verts[(i + 1) % count]
        nx += (y1 - y2) * (z1 + z2)
        ny += (z1 - z2) * (x1 + x2)
        nz += (x1 - x2) * (y1 + y2)
    length = (nx * nx + ny * ny + nz * nz) ** 0.5
    if length < 1e-12:
        return None
    n = (nx / length, ny / length, nz / length)
    x, y, z = verts[0]
    return Poly(list(verts), n, n[0] * x + n[1] * y + n[2] * z)


def _split(nrm, w, poly, cof, cob, front, back):
    types = []
    kind = 0
    nx, ny, nz = nrm
    for x, y, z in poly.v:
        t = nx * x + ny * y + nz * z - w
        k = _BACK if t < -EPS else (_FRONT if t > EPS else _COPLANAR)
        kind |= k
        types.append(k)
    if kind == _COPLANAR:
        (cof if nx * poly.n[0] + ny * poly.n[1] + nz * poly.n[2] > 0 else cob).append(poly)
    elif kind == _FRONT:
        front.append(poly)
    elif kind == _BACK:
        back.append(poly)
    else:
        f, b = [], []
        count = len(poly.v)
        for i in range(count):
            j = (i + 1) % count
            ti, tj = types[i], types[j]
            vi, vj = poly.v[i], poly.v[j]
            if ti != _BACK:
                f.append(vi)
            if ti != _FRONT:
                b.append(vi)
            if (ti | tj) == _SPANNING:
                dx, dy, dz = vj[0] - vi[0], vj[1] - vi[1], vj[2] - vi[2]
                denom = nx * dx + ny * dy + nz * dz
                t = (w - (nx * vi[0] + ny * vi[1] + nz * vi[2])) / denom
                mid = (vi[0] + dx * t, vi[1] + dy * t, vi[2] + dz * t)
                f.append(mid)
                b.append(mid)
        if len(f) >= 3:
            p = _poly(f)
            if p is not None:
                front.append(p)
        if len(b) >= 3:
            p = _poly(b)
            if p is not None:
                back.append(p)


class _Node:
    __slots__ = ("n", "w", "front", "back", "polys")

    def __init__(self):
        self.n = None
        self.w = 0.0
        self.front = None
        self.back = None
        self.polys: list[Poly] = []

    def build(self, polys, clock) -> None:
        stack = [(self, polys)]
        while stack:
            node, items = stack.pop()
            if not items:
                continue
            clock()
            if node.n is None:
                node.n, node.w = items[0].n, items[0].w
            f, b = [], []
            for p in items:
                _split(node.n, node.w, p, node.polys, node.polys, f, b)
            if f:
                if node.front is None:
                    node.front = _Node()
                stack.append((node.front, f))
            if b:
                if node.back is None:
                    node.back = _Node()
                stack.append((node.back, b))

    def invert(self) -> None:
        stack = [self]
        while stack:
            node = stack.pop()
            for p in node.polys:
                p.flip()
            if node.n is not None:
                node.n = (-node.n[0], -node.n[1], -node.n[2])
                node.w = -node.w
            node.front, node.back = node.back, node.front
            for child in (node.front, node.back):
                if child is not None:
                    stack.append(child)

    def clip(self, polys, clock):
        if self.n is None:
            return list(polys)
        clock()
        f, b = [], []
        for p in polys:
            _split(self.n, self.w, p, f, b, f, b)
        f = self.front.clip(f, clock) if self.front is not None else f
        b = self.back.clip(b, clock) if self.back is not None else []
        return f + b

    def clip_to(self, other, clock) -> None:
        stack = [self]
        while stack:
            node = stack.pop()
            node.polys = other.clip(node.polys, clock)
            for child in (node.front, node.back):
                if child is not None:
                    stack.append(child)

    def all(self):
        out, stack = [], [self]
        while stack:
            node = stack.pop()
            out.extend(node.polys)
            for child in (node.front, node.back):
                if child is not None:
                    stack.append(child)
        return out


def _to_polys(vertices: np.ndarray, faces: np.ndarray) -> list[Poly]:
    out = []
    for tri in np.asarray(vertices, dtype=np.float64)[np.asarray(faces)]:
        p = _poly([tuple(map(float, row)) for row in tri])
        if p is not None:
            out.append(p)
    return out


def _to_mesh(polys: list[Poly]) -> tuple[np.ndarray, np.ndarray]:
    verts, faces = [], []
    for p in polys:
        base = len(verts)
        verts.extend(p.v)
        for i in range(1, len(p.v) - 1):
            faces.append((base, base + i, base + i + 1))
    if not faces:
        return empty_mesh()
    return clean(np.array(verts, dtype=np.float64), np.array(faces, dtype=np.int64))


# ---------- mesh clean-up ----------
def clean(vertices: np.ndarray, faces: np.ndarray, digits: int = 6) -> tuple[np.ndarray, np.ndarray]:
    """Merge coincident vertices, drop degenerate triangles, then repair T-junctions."""
    if len(faces) == 0:
        return vertices, faces
    key = np.round(vertices, digits) + 0.0
    _, first, inverse = np.unique(key, axis=0, return_index=True, return_inverse=True)
    vertices = vertices[first]
    faces = inverse.reshape(-1)[faces]
    keep = (faces[:, 0] != faces[:, 1]) & (faces[:, 1] != faces[:, 2]) & (faces[:, 2] != faces[:, 0])
    faces = faces[keep]
    tri = vertices[faces]
    area = np.linalg.norm(np.cross(tri[:, 1] - tri[:, 0], tri[:, 2] - tri[:, 0]), axis=1) / 2.0
    faces = faces[area > 1e-10]
    used = np.unique(faces)
    remap = np.full(len(vertices), -1, dtype=np.int64)
    remap[used] = np.arange(len(used))
    return fix_tjunctions(vertices[used], remap[faces])


def fix_tjunctions(vertices: np.ndarray, faces: np.ndarray, tol: float = 1e-5, rounds: int = 60) -> tuple[np.ndarray, np.ndarray]:
    """Split the triangles whose edge passes through another boundary vertex (what polygon splitting leaves behind)."""
    faces = faces.copy()
    for _ in range(rounds):
        edges = np.sort(np.concatenate([faces[:, [0, 1]], faces[:, [1, 2]], faces[:, [2, 0]]]), axis=1)
        uniq, counts = np.unique(edges, axis=0, return_counts=True)
        boundary = uniq[counts == 1]
        if len(boundary) == 0:
            break
        wanted = {tuple(e) for e in boundary.tolist()}
        owner = {}
        for fi, tri in enumerate(faces.tolist()):
            for k in range(3):
                a, b = tri[k], tri[(k + 1) % 3]
                key = (a, b) if a < b else (b, a)
                if key in wanted:
                    owner[key] = (fi, k)
        bverts = np.unique(boundary)
        points = vertices[bverts]
        touched: set[int] = set()
        added: list[list[int]] = []
        for (a, b), (fi, k) in owner.items():
            if fi in touched:
                continue
            origin, end = vertices[a], vertices[b]
            d = end - origin
            length2 = float(d @ d)
            if length2 < 1e-18:
                continue
            t = ((points - origin) @ d) / length2
            off = np.linalg.norm(points - (origin + t[:, None] * d), axis=1)
            hit = np.flatnonzero((t > 1e-6) & (t < 1 - 1e-6) & (off < tol))
            if len(hit) == 0:
                continue
            p = int(bverts[hit[np.argmin(t[hit])]])
            tri = faces[fi].tolist()
            u, v, w = tri[k], tri[(k + 1) % 3], tri[(k + 2) % 3]  # the face's directed edge u -> v (= a,b or b,a), third vertex w
            faces[fi] = (u, p, w)
            added.append([p, v, w])
            touched.add(fi)
        if not added:
            break
        faces = np.vstack([faces, np.array(added, dtype=np.int64)])
    return vertices, faces


# ---------- boolean operations ----------
def _disjoint(a: np.ndarray, b: np.ndarray, margin: float = 1e-4) -> bool:
    return bool(np.any(a.max(axis=0) < b.min(axis=0) - margin) or np.any(b.max(axis=0) < a.min(axis=0) - margin))


def _clock(budget_s: float):
    start = time.monotonic()

    def tick() -> None:
        if time.monotonic() - start > budget_s:
            raise CsgTimeout(f"The boolean operation took more than {budget_s:.0f} s: simplify the part or lower the segment counts.")
    return tick


def boolean(op: str, a, b, budget_s: float = 60.0):
    """a, b are (vertices, faces); op is union | difference | intersection. Returns (vertices, faces)."""
    if op not in ("union", "difference", "intersection"):
        raise ValueError(f"Unknown boolean operation '{op}'.")
    (va, fa), (vb, fb) = a, b
    if len(fa) == 0:
        return (vb, fb) if op == "union" else empty_mesh()
    if len(fb) == 0:
        return empty_mesh() if op == "intersection" else (va, fa)
    if _disjoint(va, vb):
        if op == "union":
            return np.vstack([va, vb]), np.vstack([fa, fb + len(va)])
        return (va, fa) if op == "difference" else empty_mesh()
    if backend() == "manifold":
        import trimesh
        from trimesh import boolean as tb

        ma, mb = trimesh.Trimesh(va, fa, process=False), trimesh.Trimesh(vb, fb, process=False)
        result = getattr(tb, op)([ma, mb], engine="manifold")
        return np.asarray(result.vertices, dtype=np.float64), np.asarray(result.faces, dtype=np.int64)
    limit = sys.getrecursionlimit()
    sys.setrecursionlimit(max(limit, 20000))
    try:
        clock = _clock(budget_s)
        na, nb = _Node(), _Node()
        na.build(_to_polys(va, fa), clock)
        nb.build(_to_polys(vb, fb), clock)
        if op == "union":
            na.clip_to(nb, clock)
            nb.clip_to(na, clock)
            nb.invert()
            nb.clip_to(na, clock)
            nb.invert()
            na.build(nb.all(), clock)
        elif op == "difference":
            na.invert()
            na.clip_to(nb, clock)
            nb.clip_to(na, clock)
            nb.invert()
            nb.clip_to(na, clock)
            nb.invert()
            na.build(nb.all(), clock)
            na.invert()
        else:
            na.invert()
            nb.clip_to(na, clock)
            nb.invert()
            na.clip_to(nb, clock)
            nb.clip_to(na, clock)
            na.build(nb.all(), clock)
            na.invert()
        return _to_mesh(na.all())
    finally:
        sys.setrecursionlimit(limit)
