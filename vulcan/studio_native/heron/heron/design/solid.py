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

from .sketch import loops
from .spec import value

TOL = 1e-5


class ReferenceError_(ValueError):
    """A semantic face reference that does not resolve to exactly one face."""


def _vec(v):
    return np.array([v.x, v.y, v.z], dtype=float)


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
    depth = ctx['depth']
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
    import cadquery as cq
    depth = value(part.extrude.depth, values, 'extrude.depth')
    if not 0 < depth <= 2000:
        raise ValueError('extrude.depth must be in (0, 2000] mm')
    outer, inner = loops(part.sketch, solved['points'])
    wp = cq.Workplane('XY').polyline([tuple(p) for p in outer]).close().extrude(depth)
    for ring in inner:
        wp = wp.cut(cq.Workplane('XY').polyline([tuple(p) for p in ring]).close().extrude(depth))
    for hole in part.sketch.holes:
        if isinstance(hole, str):
            cx, cy = solved['points'][part.sketch.circles[hole].center]
            wp = wp.cut(cq.Workplane('XY').center(cx, cy).circle(solved['radii'][hole]).extrude(depth))
    return _one_solid(wp.val(), 'the extrusion'), depth


def _hole_centres(face, feature, ctx, values):
    import cadquery as cq
    c = _vec(face.Center())
    n = _vec(face.normalAt(face.Center()))
    centres = []
    for pid in feature.at:
        x, y = ctx['points'][pid]
        if abs(abs(n[2]) - 1) < 1e-6:
            point = np.array([x, y, c[2]])
        else:
            zh = value(feature.z, values, f'{feature.id}.z') if feature.z is not None else ctx['depth'] / 2
            lifted = np.array([x, y, zh])
            point = lifted - np.dot(lifted - c, n) * n
        if face.distance(cq.Vertex.makeVertex(*point)) > TOL:
            raise ReferenceError_(f'hole centre {pid!r} falls outside the {feature.face.label()} face')
        centres.append((pid, point))
    return centres, n


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
        centres, n = _hole_centres(face, feature, ctx, values)
        bb = solid.BoundingBox()
        through = 2 * math.dist((bb.xmin, bb.ymin, bb.zmin), (bb.xmax, bb.ymax, bb.zmax)) + 2
        for _, point in centres:
            start = point + n * 1.0
            tool = cq.Solid.makeCylinder(d / 2, (depth + 1.0) if depth is not None else through, cq.Vector(*start), cq.Vector(*(-n)))
            solid = _one_solid(solid.cut(tool), f'{feature.id}')
        facts['holes'] = [{'at': pid, 'center_mm': [round(float(v), 6) for v in point], 'axis': [round(float(v), 9) + 0.0 for v in -n],
                           'diameter_mm': d, 'depth_mm': depth} for pid, point in centres]
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
        try:
            shaped = solid.hollow([face], -t)
        except Exception as exc:
            raise ValueError(f'{feature.id}: the kernel could not shell the solid at {t:g} mm ({type(exc).__name__})') from exc
        solid = _one_solid(shaped, feature.id)
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
            if not holes or not all(h['found'] for h in holes):
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
    result = {'ok': True, 'parameters': values, 'sketch': solved, 'features': [], 'checks': [], 'files': {}}
    if solved['status'] in ('incompatible', 'invalid_geometry'):
        result['status'] = solved['status']
        result['features'] = [{'id': f.id, 'type': f.type, 'face': f.face.label(), 'state': 'not_evaluated'} for f in part.features]
        result['reference_changes'] = reference_changes(result['features'], previous)
        return result
    solid, depth = base_solid(part, solved, values)
    ctx = {'sketch': part.sketch, 'points': solved['points'], 'depth': depth}
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
