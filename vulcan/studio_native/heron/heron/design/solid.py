"""OCCT construction of a solved part: extrusion, operations on semantically named faces, independent measures, exports.

Faces are found again on every regeneration from what they mean (the top cap, the side swept by a sketch line, the
face that contains a named point). A reference that no longer finds exactly one face invalidates the feature
explicitly: nothing is built or exported from a part whose references do not resolve.
"""
from __future__ import annotations

import hashlib
import math
from pathlib import Path

import numpy as np

from .sketch import arc_geometry, chain, edge_mid, loops, _inside
from .spec import value

TOL = 1e-5


class ReferenceError_(ValueError):
    """A semantic face reference that does not resolve to exactly one face."""


def _vec(v):
    return np.array([v.x, v.y, v.z], dtype=float)


def rotate(point, origin, axis, angle):
    """Rotate a 3D point about the line through origin along the unit axis by angle (radians, right-hand rule)."""
    p, o, k = np.asarray(point, dtype=float), np.asarray(origin, dtype=float), np.asarray(axis, dtype=float)
    v = p - o
    return o + v * math.cos(angle) + np.cross(k, v) * math.sin(angle) + k * np.dot(k, v) * (1 - math.cos(angle))


def wire(wp, sketch, loop, points):
    """A closed wire of lines and three-point arcs on a workplane (local 2D coordinates)."""
    start = points[chain(sketch, loop)[0]]
    wp = wp.moveTo(*start)
    for edge in loop:
        if edge in sketch.lines:
            wp = wp.lineTo(*points[sketch.lines[edge][1]])
        else:
            wp = wp.threePointArc(tuple(arc_geometry(sketch, edge, points)['mid']), tuple(points[sketch.arcs[edge].end]))
    return wp.close()


def prism(plane, sketch, solved, depth):
    """The outline of a solved sketch extruded `depth` along the plane normal (negative: against it), its inner loops and circles cut."""
    import cadquery as cq
    pts = solved['points']
    body = wire(cq.Workplane(plane), sketch, sketch.profile, pts).extrude(depth)
    for hole in sketch.holes:
        if isinstance(hole, list):
            body = body.cut(wire(cq.Workplane(plane), sketch, hole, pts).extrude(depth))
        else:
            body = body.cut(cq.Workplane(plane).moveTo(*pts[sketch.circles[hole].center]).circle(solved['radii'][hole]).extrude(depth))
    return body


def revolve_axis(part, points):
    p, q = part.sketch.lines[part.revolve.axis]
    A, B = np.array(points[p] + [0.0]), np.array(points[q] + [0.0])
    if np.linalg.norm(B - A) < TOL:
        raise ValueError('revolve.axis has zero length')
    return A, B, (B - A) / np.linalg.norm(B - A)


def revolve_errors(part, solved):
    """The outline of a revolution must stay on one side of its axis (touching it is allowed)."""
    A, _, u = revolve_axis(part, solved['points'])
    outer, inner = loops(part.sketch, solved['points'])
    side = lambda v: float(u[0] * v[1] - u[1] * v[0])  # noqa: E731  signed distance from the axis in the sketch plane
    sides = [side(np.array(p) - A[:2]) for ring in [outer] + inner for p in ring]
    for h in part.sketch.holes:
        if isinstance(h, str):
            c = np.array(solved['points'][part.sketch.circles[h].center]) - A[:2]
            d, r = side(c), solved['radii'][h]
            sides += [d - r, d + r]
    if min(sides) < -1e-6 and max(sides) > 1e-6:
        return [f'the outline crosses the revolution axis {part.revolve.axis!r}; keep it on one side']
    return []


def interior_point(sketch, solved):
    """A point inside the solved outline and outside its holes (for naming the faces of a revolution)."""
    outer, inner = loops(sketch, solved['points'])
    circles = [(solved['points'][sketch.circles[h].center], solved['radii'][h]) for h in sketch.holes if isinstance(h, str)]
    ys = [p[1] for p in outer]
    for f in (0.5, 0.37, 0.63, 0.21, 0.79, 0.11, 0.89):
        y = min(ys) + f * (max(ys) - min(ys))
        xs = sorted(p[0] + (y - p[1]) * (q[0] - p[0]) / (q[1] - p[1]) for p, q in zip(outer, outer[1:] + outer[:1]) if (p[1] > y) != (q[1] > y))
        for x0, x1 in zip(xs[::2], xs[1::2]):
            for t in (0.5, 0.25, 0.75):
                pt = [x0 + t * (x1 - x0), y]
                if _inside(pt, outer) and not any(_inside(pt, r) for r in inner) and all(math.dist(pt, c) > r + 1e-6 for c, r in circles):
                    return pt
    raise ReferenceError_('no interior point of the outline was found to name its end faces')


def exact_bounds(shape):
    """Axis-aligned extents from the exact geometry (no tessellation, no shape tolerance enlargement)."""
    from OCP.Bnd import Bnd_Box
    from OCP.BRepBndLib import BRepBndLib
    box = Bnd_Box()
    BRepBndLib.AddOptimal_s(shape.wrapped, box, False, False)
    x0, y0, z0, x1, y1, z1 = box.Get()
    return [x1 - x0, y1 - y0, z1 - z0]


def fingerprint(face):
    c = face.Center()
    info = {'surface': face.geomType(), 'center_mm': [round(float(x), 6) for x in _vec(c)], 'area_mm2': round(float(face.Area()), 6)}
    if face.geomType() == 'PLANE':
        info['normal'] = [round(float(x), 9) + 0.0 for x in _vec(face.normalAt(c))]
    return info


def _planar(solid):
    for face in solid.Faces():
        if face.geomType() == 'PLANE':
            c = face.Center()
            yield face, _vec(c), _vec(face.normalAt(c))


def resolve_face(solid, ref, ctx):
    """The single face a reference names, or ReferenceError_ with the reason."""
    import cadquery as cq
    if ctx.get('mode') == 'revolve':
        return _resolve_revolved(solid, ref, ctx)
    depth = ctx['depth']
    if ref.side and ref.side in ctx['sketch'].arcs:  # the cylinder swept by an arc holds the arc's mid point at half height
        mx, my = edge_mid(ctx['sketch'], ref.side, ctx['points'])
        hits = [f for f in solid.Faces() if f.distance(cq.Vertex.makeVertex(mx, my, depth / 2)) < TOL]
        if not hits:
            raise ReferenceError_(f'no face is swept by sketch arc {ref.side!r} any more')
        if len(hits) > 1:
            raise ReferenceError_(f'the side of arc {ref.side!r} is split into {len(hits)} faces; name one with contains')
        return hits[0]
    if ref.cap:
        want_n, want_z = (np.array([0, 0, 1.0]), depth) if ref.cap == 'top' else (np.array([0, 0, -1.0]), 0.0)
        hits = [f for f, c, n in _planar(solid) if np.linalg.norm(n - want_n) < 1e-6 and abs(c[2] - want_z) < TOL]
        if not hits:
            raise ReferenceError_(f'the {ref.cap} face no longer exists (removed by an earlier operation)')
        if len(hits) > 1:
            raise ReferenceError_(f'the {ref.cap} face is split into {len(hits)} faces; name one with contains')
        return hits[0]
    if ref.side:
        if ref.side not in ctx['sketch'].lines:
            raise ReferenceError_(f'sketch line {ref.side!r} no longer exists')
        p, q = ctx['sketch'].lines[ref.side]
        A, B = np.array(ctx['points'][p] + [0.0]), np.array(ctx['points'][q] + [0.0])
        d = B - A
        if np.linalg.norm(d) < TOL:
            raise ReferenceError_(f'sketch line {ref.side!r} has zero length')
        normal = np.cross(d / np.linalg.norm(d), [0, 0, 1.0])
        hits = [f for f, c, n in _planar(solid) if abs(abs(float(np.dot(n, normal))) - 1) < 1e-6 and abs(float(np.dot(c - A, n))) < TOL]
        if len(hits) > 1:  # coplanar sides elsewhere in the outline: keep the one that holds this line's own mid point
            mid = cq.Vertex.makeVertex(*((A + B) / 2)[:2], depth / 2)
            own = [f for f in hits if f.distance(mid) < TOL]
            if own:
                hits = own
        if not hits:
            raise ReferenceError_(f'no face is swept by sketch line {ref.side!r} any more')
        if len(hits) > 1:
            raise ReferenceError_(f'the side of {ref.side!r} is split into {len(hits)} faces; name one with contains')
        return hits[0]
    if ref.contains not in ctx['points']:
        raise ReferenceError_(f'sketch point {ref.contains!r} no longer exists')
    x, y = ctx['points'][ref.contains]
    z = {'top': depth, 'bottom': 0.0, 'mid': depth / 2}[ref.level]
    vertex = cq.Vertex.makeVertex(x, y, z)
    hits = [f for f in solid.Faces() if f.distance(vertex) < TOL]
    if not hits:
        raise ReferenceError_(f'no face contains point {ref.contains!r} at level {ref.level}')
    if len(hits) > 1:
        raise ReferenceError_(f'point {ref.contains!r} lies on an edge shared by {len(hits)} faces')
    return hits[0]


def _single(solid, point, what):
    import cadquery as cq
    hits = [f for f in solid.Faces() if f.distance(cq.Vertex.makeVertex(*map(float, point))) < TOL]
    if not hits:
        raise ReferenceError_(f'no face holds {what} any more')
    if len(hits) > 1:
        raise ReferenceError_(f'{what} lies on an edge shared by {len(hits)} faces')
    return hits[0]


def _resolve_revolved(solid, ref, ctx):
    """Faces of a revolution: bottom = the outline itself (sweep start), top = the outline at the end of the sweep, a side = the
    surface an edge sweeps (found at half the sweep), contains = a point carried to the start, middle or end of the sweep."""
    A, u, angle = ctx['axis_origin'], ctx['axis_dir'], ctx['angle']
    sketch, pts = ctx['sketch'], ctx['points']
    if ref.cap:
        if angle >= 2 * math.pi - 1e-9:
            raise ReferenceError_('a full revolution has no end faces; name a face with side or contains')
        x, y = ctx['interior']
        p = rotate([x, y, 0.0], A, u, angle if ref.cap == 'top' else 0.0)
        return _single(solid, p, f'the {ref.cap} end face of the revolution')
    if ref.side:
        if ref.side not in sketch.lines and ref.side not in sketch.arcs:
            raise ReferenceError_(f'sketch edge {ref.side!r} no longer exists')
        if ref.side in sketch.lines:
            ends = [np.array(pts[p] + [0.0]) for p in sketch.lines[ref.side]]
            if all(np.linalg.norm(np.cross(e - A, u)) < TOL for e in ends):
                raise ReferenceError_(f'sketch line {ref.side!r} lies on the revolution axis and sweeps no face')
        mx, my = edge_mid(sketch, ref.side, pts)
        return _single(solid, rotate([mx, my, 0.0], A, u, angle / 2), f'the surface swept by {ref.side!r}')
    if ref.contains not in pts:
        raise ReferenceError_(f'sketch point {ref.contains!r} no longer exists')
    x, y = pts[ref.contains]
    turn = {'bottom': 0.0, 'mid': angle / 2, 'top': angle}[ref.level]
    return _single(solid, rotate([x, y, 0.0], A, u, turn), f'point {ref.contains!r} at the {ref.level} of the sweep')


def _one_solid(shape, what):
    import cadquery as cq
    solids = shape.Solids()
    if len(solids) != 1:
        raise ValueError(f'{what} left {len(solids)} separate solids')
    solid = solids[0]
    if not solid.isValid() or solid.Volume() <= 0:
        raise ValueError(f'{what} produced an invalid solid')
    return cq.Solid(solid.wrapped)


def base_solid(part, solved, values):
    """The extrusion (returns its depth) or the revolution (returns None) of the solved outline."""
    import cadquery as cq
    if part.revolve:
        angle = value(part.revolve.angle, values, 'revolve.angle')
        if not 0 < angle <= 360:
            raise ValueError('revolve.angle must be in (0, 360] degrees')
        A, B, _ = revolve_axis(part, solved['points'])
        pts = solved['points']
        body = wire(cq.Workplane('XY'), part.sketch, part.sketch.profile, pts).revolve(angle, tuple(A), tuple(B))
        for hole in part.sketch.holes:
            tool = wire(cq.Workplane('XY'), part.sketch, hole, pts) if isinstance(hole, list) else \
                cq.Workplane('XY').moveTo(*pts[part.sketch.circles[hole].center]).circle(solved['radii'][hole])
            body = body.cut(tool.revolve(angle, tuple(A), tuple(B)))
        return _one_solid(body.val(), 'the revolution'), None
    depth = value(part.extrude.depth, values, 'extrude.depth')
    if not 0 < depth <= 2000:
        raise ValueError('extrude.depth must be in (0, 2000] mm')
    return _one_solid(prism(cq.Plane.XY(), part.sketch, solved, depth).val(), 'the extrusion'), depth


def _hole_centres(face, feature, ctx, values):
    import cadquery as cq
    c = _vec(face.Center())
    n = _vec(face.normalAt(face.Center()))
    if face.geomType() != 'PLANE':
        raise ValueError(f'{feature.id}: holes go on planar faces; {feature.face.label()} is {face.geomType().lower()}')

    def lift(pid):
        x, y = ctx['points'][pid]
        if ctx.get('mode') == 'revolve':  # the sketch point where it lies on this plane: at the start or the end of the sweep
            base = np.array([x, y, value(feature.z, values, f'{feature.id}.z') if feature.z is not None else 0.0])
            options = [base, rotate(base, ctx['axis_origin'], ctx['axis_dir'], ctx['angle'])]
            lifted = min(options, key=lambda p: abs(float(np.dot(p - c, n))))
            return lifted - np.dot(lifted - c, n) * n
        if abs(abs(n[2]) - 1) < 1e-6:
            return np.array([x, y, c[2]])
        zh = value(feature.z, values, f'{feature.id}.z') if feature.z is not None else ctx['depth'] / 2
        lifted = np.array([x, y, zh])
        return lifted - np.dot(lifted - c, n) * n

    on_face = lambda p: face.distance(cq.Vertex.makeVertex(*map(float, p))) <= TOL  # noqa: E731
    centres = []
    for pid in feature.at:
        point = lift(pid)
        if not on_face(point):
            raise ReferenceError_(f'hole centre {pid!r} falls outside the {feature.face.label()} face')
        centres.append((pid, point))
    pattern = feature.pattern
    if pattern:
        copies = []
        if pattern.type == 'linear':
            spacing = value(pattern.spacing, values, f'{feature.id}.pattern.spacing')
            if not 0 < spacing <= 5000:
                raise ValueError(f'{feature.id}: pattern spacing must be in (0, 5000] mm')
            if isinstance(pattern.direction, str):
                p, q = ctx['sketch'].lines[pattern.direction]
                d = np.array(ctx['points'][q] + [0.0]) - np.array(ctx['points'][p] + [0.0])
            else:
                d = np.array(list(pattern.direction) + [0.0] * (3 - len(pattern.direction)), dtype=float)
            d = d - np.dot(d, n) * n
            if np.linalg.norm(d) < 1e-9:
                raise ValueError(f'{feature.id}: the pattern direction is perpendicular to the {feature.face.label()} face')
            d /= np.linalg.norm(d)
            step = lambda point, k: point + d * spacing * k  # noqa: E731
            facts = {'type': 'linear', 'count': pattern.count, 'spacing_mm': spacing, 'direction': [round(float(v), 9) + 0.0 for v in d]}
        else:
            total = value(pattern.angle, values, f'{feature.id}.pattern.angle')
            if not 0 < total <= 360:
                raise ValueError(f'{feature.id}: pattern angle must be in (0, 360] degrees')
            pitch = math.radians(total) / (pattern.count if abs(total - 360) < 1e-9 else pattern.count - 1)
            pivot = lift(pattern.center)
            step = lambda point, k: rotate(point, pivot, n, pitch * k)  # noqa: E731
            facts = {'type': 'circular', 'count': pattern.count, 'pitch_deg': math.degrees(pitch), 'center_mm': [round(float(v), 6) for v in pivot]}
        for pid, point in centres:
            for k in range(pattern.count):
                copy = step(point, k)
                if not on_face(copy):
                    raise ValueError(f'{feature.id}: pattern copy {k + 1} of {pid!r} falls outside the {feature.face.label()} face')
                copies.append((pid if k == 0 else f'{pid}#{k + 1}', copy))
        return copies, n, facts
    return centres, n, None


def face_frame(face, ref, ctx):
    """Origin, x and y axes and outward normal of the 2D frame a sketch on this planar face is drawn in.

    Extrusion side of a line: origin at the line start on the sketch plane, x along the line, y = normal x x (up for a
    counter-clockwise outline). Extrusion top/bottom: origin on the z axis, x = world X (top keeps world Y; bottom mirrors it).
    Any other planar face: origin = the world origin projected onto the face plane, x = world X (or Y, or Z) projected, y = normal x x."""
    c, n = _vec(face.Center()), _vec(face.normalAt(face.Center()))
    n = n / np.linalg.norm(n)
    if ctx.get('mode') != 'revolve' and ref.side and ref.side in ctx['sketch'].lines:
        p, q = ctx['sketch'].lines[ref.side]
        origin = np.array(ctx['points'][p] + [0.0])
        x = np.array(ctx['points'][q] + [0.0]) - origin
    elif ctx.get('mode') != 'revolve' and ref.cap:
        origin, x = np.array([0.0, 0.0, ctx['depth'] if ref.cap == 'top' else 0.0]), np.array([1.0, 0.0, 0.0])
    else:
        origin = np.dot(c, n) * n
        x = next(v for v in (np.eye(3)[0], np.eye(3)[1], np.eye(3)[2]) if np.linalg.norm(v - np.dot(v, n) * n) > 1e-6)
    x = x - np.dot(x, n) * n
    x /= np.linalg.norm(x)
    clean = lambda v: [round(float(t), 9) + 0.0 for t in v]  # noqa: E731
    return origin, x, np.cross(n, x), n, {'origin_mm': clean(origin), 'x': clean(x), 'y': clean(np.cross(n, x)), 'normal': clean(n)}


def _sketch_on_face(solid, face, feature, ctx, values):
    """A pad (material added out of the face) or pocket (material removed into it) from a constrained sketch on a planar face."""
    import cadquery as cq
    from .sketch import solve
    if face.geomType() != 'PLANE':
        raise ValueError(f'{feature.id}: a sketch needs a planar face; {feature.face.label()} is {face.geomType().lower()}')
    sub = solve(feature.sketch, values)
    summary = {'status': sub['status'], 'dof': sub['dof'], 'area_mm2': sub['geometry'].get('area_mm2'), 'free': sub['free'],
               'conflicts': sub['conflicts'], 'errors': sub['geometry']['errors'], 'points': sub['points']}
    if sub['status'] in ('incompatible', 'invalid_geometry'):
        reason = '; '.join(sub['geometry']['errors']) or ', '.join(f"{c['failing_constraint']} conflicts with {', '.join(c['conflicts_with'])}" for c in sub['conflicts'])
        raise ValueError(f'{feature.id}: the face sketch is {sub["status"]} ({reason})')
    origin, x, _, n, frame = face_frame(face, feature.face, ctx)
    bb = solid.BoundingBox()
    through = 2 * math.dist((bb.xmin, bb.ymin, bb.zmin), (bb.xmax, bb.ymax, bb.zmax)) + 2
    if feature.depth is not None:
        depth = value(feature.depth, values, f'{feature.id}.depth')
        if not 0 < depth <= 2000:
            raise ValueError(f'{feature.id}: depth must be in (0, 2000] mm')
    else:
        depth = through
    plane = cq.Plane(origin=tuple(map(float, origin)), xDir=tuple(map(float, x)), normal=tuple(map(float, n)))
    tool = prism(plane, feature.sketch, sub, depth if feature.type == 'pad' else -depth).val()
    before = solid.Volume()
    shaped = _one_solid(solid.fuse(tool).clean() if feature.type == 'pad' else solid.cut(tool), feature.id)
    change = shaped.Volume() - before
    if feature.type == 'pad' and change <= before * 1e-9 or feature.type == 'pocket' and change >= -before * 1e-9:
        raise ValueError(f'{feature.id}: the {feature.type} changed no material (its sketch is outside the {feature.face.label()} face?)')
    return shaped, {'sketch': summary, 'frame': frame, 'depth_mm': depth if feature.depth is not None else None, 'volume_change_mm3': change}


def apply_feature(solid, feature, ctx, values):
    """Apply one operation; returns the new solid and the facts of what was resolved."""
    import cadquery as cq
    face = resolve_face(solid, feature.face, ctx)
    facts = {'resolved': fingerprint(face)}
    if feature.type == 'hole':
        d = value(feature.diameter, values, f'{feature.id}.diameter')
        if not 0 < d <= 1000:
            raise ValueError(f'{feature.id}: diameter must be in (0, 1000] mm')
        depth = value(feature.depth, values, f'{feature.id}.depth') if feature.depth is not None else None
        if depth is not None and not 0 < depth <= 4000:
            raise ValueError(f'{feature.id}: depth must be in (0, 4000] mm')
        centres, n, pattern = _hole_centres(face, feature, ctx, values)
        bb = solid.BoundingBox()
        through = 2 * math.dist((bb.xmin, bb.ymin, bb.zmin), (bb.xmax, bb.ymax, bb.zmax)) + 2
        tools = [cq.Solid.makeCylinder(d / 2, (depth + 1.0) if depth is not None else through, cq.Vector(*(point + n * 1.0)), cq.Vector(*(-n)))
                 for _, point in centres]
        solid = _one_solid(solid.cut(tools[0] if len(tools) == 1 else cq.Compound.makeCompound(tools)), f'{feature.id}')
        facts['holes'] = [{'at': pid, 'center_mm': [round(float(v), 6) for v in point], 'axis': [round(float(v), 9) + 0.0 for v in -n],
                           'diameter_mm': d, 'depth_mm': depth} for pid, point in centres]
        if pattern:
            facts['pattern'] = pattern
    elif feature.type in ('pad', 'pocket'):
        solid, more = _sketch_on_face(solid, face, feature, ctx, values)
        facts.update(more)
    elif feature.type in ('fillet', 'chamfer'):
        size = value(feature.radius if feature.type == 'fillet' else feature.distance, values, f'{feature.id}')
        if not 0 < size <= 100:
            raise ValueError(f'{feature.id}: size must be in (0, 100] mm')
        edges = face.outerWire().Edges() if feature.edges == 'outer' else face.Edges()
        try:
            shaped = solid.fillet(size, edges) if feature.type == 'fillet' else solid.chamfer(size, None, edges)
        except Exception as exc:  # OCCT raises StdFail_NotDone and friends
            raise ValueError(f'{feature.id}: the kernel could not {feature.type} {len(edges)} edges at {size:g} mm ({type(exc).__name__})') from exc
        solid = _one_solid(shaped, feature.id)
        facts['edges'] = len(edges)
    else:
        t = value(feature.thickness, values, f'{feature.id}.thickness')
        if not 0 < t <= 200:
            raise ValueError(f'{feature.id}: thickness must be in (0, 200] mm')
        before = solid.Volume()
        try:
            shaped = solid.hollow([face], -t)
        except Exception as exc:
            raise ValueError(f'{feature.id}: the kernel could not shell the solid at {t:g} mm ({type(exc).__name__})') from exc
        solid = _one_solid(shaped, feature.id)
        if solid.Volume() > before * (1 - 1e-6):  # the kernel can hand back the unchanged solid for an impossible wall
            raise ValueError(f'{feature.id}: a {t:g} mm wall removed no material (thicker than the solid allows); nothing was hollowed')
        facts['wall_mm'] = t
    return solid, facts


def cylinders(solid):
    """Every cylindrical face of the finished solid: axis point, direction, radius (read from the B-rep)."""
    from OCP.BRepAdaptor import BRepAdaptor_Surface
    from OCP.GeomAbs import GeomAbs_Cylinder
    out = []
    for face in solid.Faces():
        surface = BRepAdaptor_Surface(face.wrapped)
        if surface.GetType() == GeomAbs_Cylinder:
            cyl = surface.Cylinder()
            loc, direction = cyl.Location(), cyl.Axis().Direction()
            out.append({'point': np.array([loc.X(), loc.Y(), loc.Z()]), 'dir': np.array([direction.X(), direction.Y(), direction.Z()]),
                        'radius': cyl.Radius()})
    return out


def _axis_gap(point, cyl):
    v = point - cyl['point']
    return float(np.linalg.norm(v - np.dot(v, cyl['dir']) * cyl['dir']))


def measure_holes(solid, facts):
    """For each declared hole, the cylinder of the finished solid whose axis passes through its centre."""
    found = cylinders(solid)
    out = []
    for hole in facts.get('holes', []):
        centre = np.array(hole['center_mm'])
        near = [c for c in found if _axis_gap(centre, c) < 1e-4 and abs(abs(float(np.dot(c['dir'], hole['axis']))) - 1) < 1e-6]
        if not near:
            out.append({'at': hole['at'], 'found': False})
            continue
        c = min(near, key=lambda c: abs(c['radius'] * 2 - hole['diameter_mm']))
        out.append({'at': hole['at'], 'found': True, 'diameter_mm': 2 * c['radius'], 'axis_point': c['point'], 'axis': c['dir']})
    return out


def run_checks(part, values, solid, feature_facts):
    bx, by, bz = exact_bounds(solid)
    rows = []
    measured_holes = {fid: measure_holes(solid, facts) for fid, facts in feature_facts.items() if 'holes' in facts}
    for check in part.checks:
        expected = value(check.value, values, f'check {check.id}')
        got, note = None, None
        if check.measure in ('bbox_x', 'bbox_y', 'bbox_z'):
            got = {'bbox_x': bx, 'bbox_y': by, 'bbox_z': bz}[check.measure]
        elif check.measure == 'volume':
            got = solid.Volume()
        else:
            holes = measured_holes.get(check.feature) or []
            if check.measure == 'hole_count':
                got = float(sum(h['found'] for h in holes))
            elif not holes or not all(h['found'] for h in holes):
                note = 'the hole was not found as a cylinder of the finished solid'
            elif check.measure == 'hole_diameter':
                got = holes[0]['diameter_mm']
                if len(holes) > 1:
                    got = max(holes, key=lambda h: abs(h['diameter_mm'] - expected))['diameter_mm']  # the worst one
            else:
                a, b = holes[0], holes[1]
                got = float(np.linalg.norm(np.cross(b['axis_point'] - a['axis_point'], a['axis'])))
        row = {'id': check.id, 'measure': check.measure, 'expected': expected, 'tolerance': check.tolerance}
        if got is None:
            row.update(passed=False, note=note)
        else:
            row.update(measured=float(got), difference=float(got - expected), passed=abs(got - expected) <= check.tolerance)
        rows.append(row)
    return rows


def reference_changes(features, previous):
    """How each feature's reference fared against the base revision: kept (and how far its face moved), invalidated, new."""
    before = {f['id']: f for f in (previous or {}).get('features', [])}
    out = []
    for f in features:
        old = before.pop(f['id'], None)
        if old is None:
            out.append({'id': f['id'], 'change': 'new', 'state': f['state']})
        elif f['state'] == 'resolved' and old.get('state') == 'resolved':
            moved = float(np.linalg.norm(np.array(f['resolved']['center_mm']) - np.array(old['resolved']['center_mm'])))
            out.append({'id': f['id'], 'change': 'kept', 'face': f['face'], 'face_center_moved_mm': moved,
                        'area_change_mm2': f['resolved']['area_mm2'] - old['resolved']['area_mm2']})
        elif f['state'] == 'invalidated':
            out.append({'id': f['id'], 'change': 'invalidated', 'face': f['face'], 'reason': f.get('reason'), 'was': old.get('state')})
        else:
            out.append({'id': f['id'], 'change': 'restored' if f['state'] == 'resolved' else f['state'], 'was': old.get('state')})
    out += [{'id': fid, 'change': 'removed'} for fid in before]
    return out


def sketch_svg(part, solved, path):
    """The solved sketch: outline, construction lines dashed, circles, labelled points (y up)."""
    from xml.sax.saxutils import escape
    pts = solved['points']
    xs = [p[0] for p in pts.values()] + [pts[c.center][0] + s * solved['radii'][k] for k, c in part.sketch.circles.items() for s in (-1, 1)]
    ys = [p[1] for p in pts.values()] + [pts[c.center][1] + s * solved['radii'][k] for k, c in part.sketch.circles.items() for s in (-1, 1)]
    for aid in part.sketch.arcs:
        g = arc_geometry(part.sketch, aid, pts)
        xs += [g['center'][0] - g['radius'], g['center'][0] + g['radius']]
        ys += [g['center'][1] - g['radius'], g['center'][1] + g['radius']]
    x0, x1, y0, y1 = min(xs), max(xs), min(ys), max(ys)
    span = max(x1 - x0, y1 - y0, 1.0)
    pad = span * 0.12
    k = 760 / (span + 2 * pad)
    X = lambda x: (x - x0 + pad) * k + 20  # noqa: E731
    Y = lambda y: 580 - (y - y0 + pad) * k  # noqa: E731
    used = set(part.sketch.profile) | {lid for h in part.sketch.holes if isinstance(h, list) for lid in h}
    out = ['<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 800 600" preserveAspectRatio="xMidYMid meet">',
           '<rect width="800" height="600" fill="#ffffff"/>']
    for lid, (a, b) in part.sketch.lines.items():
        dash = '' if lid in used else ' stroke-dasharray="8 6"'
        out.append(f'<line x1="{X(pts[a][0]):.2f}" y1="{Y(pts[a][1]):.2f}" x2="{X(pts[b][0]):.2f}" y2="{Y(pts[b][1]):.2f}" stroke="#1d3557" stroke-width="2.5"{dash}/>')
        mx, my = (X(pts[a][0]) + X(pts[b][0])) / 2, (Y(pts[a][1]) + Y(pts[b][1])) / 2
        out.append(f'<text x="{mx:.1f}" y="{my - 6:.1f}" font-size="13" fill="#457b9d" font-family="sans-serif">{escape(lid)}</text>')
    from .sketch import polygon
    for aid, arc in part.sketch.arcs.items():
        ring = polygon(part.sketch, [aid], pts, per_turn=96) + [pts[arc.end]]
        dash = '' if aid in used else ' stroke-dasharray="8 6"'
        out.append('<polyline fill="none" stroke="#1d3557" stroke-width="2.5"' + dash + ' points="' +
                   ' '.join(f'{X(p[0]):.2f},{Y(p[1]):.2f}' for p in ring) + '"/>')
        mid = arc_geometry(part.sketch, aid, pts)['mid']
        out.append(f'<text x="{X(mid[0]):.1f}" y="{Y(mid[1]) - 6:.1f}" font-size="13" fill="#457b9d" font-family="sans-serif">{escape(aid)}</text>')
    for cid, circle in part.sketch.circles.items():
        cx, cy = pts[circle.center]
        out.append(f'<circle cx="{X(cx):.2f}" cy="{Y(cy):.2f}" r="{solved["radii"][cid] * k:.2f}" fill="none" stroke="#1d3557" stroke-width="2.5"/>')
    holes = {p for f in part.features if f.type == 'hole' for p in f.at}
    for pid, (x, y) in pts.items():
        colour = '#e63946' if pid in holes else '#1d3557'
        out.append(f'<circle cx="{X(x):.2f}" cy="{Y(y):.2f}" r="4" fill="{colour}"/>')
        out.append(f'<text x="{X(x) + 6:.1f}" y="{Y(y) + 15:.1f}" font-size="12" fill="#333" font-family="sans-serif">{escape(pid)}</text>')
    out.append('</svg>')
    Path(path).write_text('\n'.join(out), encoding='utf-8')


def export(solid, part, solved, folder):
    import cadquery as cq
    import trimesh
    import xml.etree.ElementTree as ET
    folder = Path(folder)
    shape = cq.Workplane(obj=solid)
    step, stl = folder / 'model.step', folder / 'model.stl'
    cq.exporters.export(shape, str(step))
    cq.exporters.export(shape, str(stl), tolerance=part.tolerance, angularTolerance=0.1)
    for name, direction in [('isometric', (-1, -1, 1)), ('front', (0, -1, 0)), ('top', (0, 0, 1))]:
        view = folder / (name + '.svg')
        cq.exporters.export(shape, str(view), opt={'width': 800, 'height': 600, 'marginLeft': 100, 'marginTop': 75, 'projectionDir': direction,
                                                   'showAxes': False, 'showHidden': False})
        svg = ET.parse(view)
        svg.getroot().set('viewBox', '0 0 800 600')
        svg.getroot().set('preserveAspectRatio', 'xMidYMid meet')
        svg.write(view, encoding='utf-8', xml_declaration=True)
    sketch_svg(part, solved, folder / 'sketch.svg')
    volume, bounds = solid.Volume(), exact_bounds(solid)
    restored = cq.importers.importStep(str(step))
    rsolids = restored.solids().vals()
    rvolume = sum(s.Volume() for s in rsolids)
    rbounds = exact_bounds(restored.val())
    mesh = trimesh.load_mesh(stl, process=True)
    roundtrip = {'step': {'solids': len(rsolids), 'volume_mm3': rvolume, 'bounds_mm': rbounds,
                          'volume_difference_mm3': rvolume - volume, 'max_bounds_difference_mm': max(abs(a - b) for a, b in zip(rbounds, bounds))},
                 'stl': {'watertight': bool(mesh.is_watertight), 'winding_consistent': bool(mesh.is_winding_consistent), 'faces': len(mesh.faces),
                         'volume_mm3': float(mesh.volume), 'bounds_mm': [float(v) for v in mesh.extents]}}
    roundtrip['ok'] = (len(rsolids) == 1 and math.isclose(volume, rvolume, rel_tol=1e-7, abs_tol=1e-5)
                       and roundtrip['step']['max_bounds_difference_mm'] < 1e-6 and roundtrip['stl']['watertight'])
    files = {p.name: {'bytes': p.stat().st_size, 'sha256': hashlib.sha256(p.read_bytes()).hexdigest()}
             for p in sorted(folder.iterdir()) if p.suffix in ('.stl', '.step', '.svg')}
    return {'volume_mm3': volume, 'bounds_mm': bounds, 'faces': len(solid.Faces()), 'roundtrip': roundtrip, 'files': files}


def evaluate(spec, folder, previous=None, start=None):
    """Solve the sketch, build the part, apply its operations, measure and export; never raises for model errors."""
    from .sketch import solve
    from .spec import Part, resolve
    part = Part.model_validate(spec)
    values = resolve(part.parameters)
    solved = solve(part.sketch, values, start)
    if part.revolve and solved['status'] not in ('incompatible', 'invalid_geometry'):
        errors = revolve_errors(part, solved)
        if errors:
            solved['geometry']['errors'] += errors
            solved['geometry']['valid'] = False
            solved['status'] = 'invalid_geometry'
    result = {'ok': True, 'parameters': values, 'sketch': solved, 'features': [], 'checks': [], 'files': {},
              'base': 'revolve' if part.revolve else 'extrude'}
    if solved['status'] in ('incompatible', 'invalid_geometry'):
        result['status'] = solved['status']
        result['features'] = [{'id': f.id, 'type': f.type, 'face': f.face.label(), 'state': 'not_evaluated'} for f in part.features]
        result['reference_changes'] = reference_changes(result['features'], previous)
        return result
    solid, depth = base_solid(part, solved, values)
    ctx = {'sketch': part.sketch, 'points': solved['points'], 'depth': depth, 'mode': 'revolve' if part.revolve else 'extrude'}
    if part.revolve:
        A, _, u = revolve_axis(part, solved['points'])
        ctx.update(axis_origin=A, axis_dir=u, angle=math.radians(value(part.revolve.angle, values, 'revolve.angle')), depth=0.0)
        try:
            ctx['interior'] = interior_point(part.sketch, solved)
        except ReferenceError_:
            ctx['interior'] = solved['points'][chain(part.sketch, part.sketch.profile)[0]]
        result['revolution'] = {'axis': part.revolve.axis, 'angle_deg': math.degrees(ctx['angle']),
                                'axis_origin_mm': [float(v) for v in A], 'axis_direction': [float(v) for v in u]}
    status, facts_by_id = None, {}
    for feature in part.features:
        row = {'id': feature.id, 'type': feature.type, 'face': feature.face.label()}
        if status:
            row['state'] = 'not_evaluated'
        else:
            try:
                solid, facts = apply_feature(solid, feature, ctx, values)
                row.update(state='resolved', **facts)
                facts_by_id[feature.id] = facts
            except ReferenceError_ as exc:
                row.update(state='invalidated', reason=str(exc))
                status = 'invalid_reference'
            except ValueError as exc:
                row.update(state='failed', reason=str(exc))
                status = 'feature_failed'
        result['features'].append(row)
    result['reference_changes'] = reference_changes(result['features'], previous)
    if status:
        result['status'] = status
        return result
    result['checks'] = run_checks(part, values, solid, facts_by_id)
    result.update(export(solid, part, solved, folder))
    result['status'] = solved['status']  # solved or under_constrained (built from the current geometry)
    result['checks_passed'] = all(c['passed'] for c in result['checks'])
    return result
