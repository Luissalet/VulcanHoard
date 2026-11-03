"""Pairwise interference in the assembly frame.

Native method (both components are B-rep solids): the OCCT Boolean common of the two placed solids gives the
penetration volume; a common volume equal to the smaller solid's volume, confirmed by a point-in-solid test,
is reported as full containment (it has no crossing surfaces, so a triangle-intersection test would miss it).
When there is no common volume the exact B-rep distance separates contact (gap within the contact tolerance)
from clearance.

Mesh approximation (a component that only has a mesh): both sides are tessellated meshes checked for closure
and consistent winding; manifold3d computes the intersection volume and the minimum gap. It is named as an
approximation with its tessellation tolerance. An open or inconsistent mesh makes the pair ``not_checkable``,
never "no clash".
"""
from __future__ import annotations

import itertools

import numpy as np

NATIVE = 'native_brep_boolean'
MESH = 'mesh_approximation_manifold3d'


def place_solid(solid, pose):
    import cadquery as cq
    from OCP.gp import gp_Trsf
    R, t = pose
    trsf = gp_Trsf()
    trsf.SetValues(*R[0], t[0], *R[1], t[1], *R[2], t[2])
    return solid.moved(cq.Location(trsf))


def place_mesh(mesh, pose):
    R, t = pose
    matrix = np.eye(4)
    matrix[:3, :3], matrix[:3, 3] = R, t
    placed = mesh.copy()
    placed.apply_transform(matrix)
    return placed


def tessellate(solid, tolerance):
    import trimesh
    vertices, triangles = solid.tessellate(tolerance, 0.1)
    return trimesh.Trimesh(np.array([v.toTuple() for v in vertices]), np.array(triangles), process=True)


def mesh_fitness(mesh):
    problems = []
    if not mesh.is_watertight:
        problems.append('open mesh (not watertight)')
    if not mesh.is_winding_consistent:
        problems.append('inconsistent winding')
    if mesh.is_watertight and mesh.volume <= 0:
        problems.append('non-positive enclosed volume (inverted or degenerate)')
    return problems


def _manifold(mesh):
    import manifold3d
    return manifold3d.Manifold(manifold3d.Mesh(vert_properties=np.asarray(mesh.vertices, np.float32), tri_verts=np.asarray(mesh.faces, np.uint32)))


def _relations_between(relations, a, b):
    out = []
    for r in relations:
        names = {r['a'].split('.')[0], r['b'].split('.')[0]} if r.get('a') and r.get('b') else {r.get('component')}
        if names == {a, b}:
            out.append(r['id'])
    return out


def check(components, poses, relations, checks):
    """components: id -> {'solid': cq.Solid | None, 'mesh': trimesh | None, 'volume': float}."""
    contact_tol, volume_tol = checks['contact_tolerance'], checks['volume_tolerance']
    requested = {}
    for r in relations:
        if r['type'] == 'clearance':
            requested.setdefault(frozenset((r['a'], r['b'])), []).append(r)
    placed = {}
    for cid, comp in components.items():
        if comp.get('solid') is not None:
            placed[cid] = {'solid': place_solid(comp['solid'], poses[cid])}
        else:
            placed[cid] = {'mesh': place_mesh(comp['mesh'], poses[cid])}
    pairs = []
    for a, b in itertools.combinations(components, 2):
        row = {'a': a, 'b': b, 'relations': _relations_between(relations, a, b)}
        sa, sb = placed[a].get('solid'), placed[b].get('solid')
        if sa is not None and sb is not None:
            row.update(_native(sa, sb, components[a]['volume'], components[b]['volume'], contact_tol, volume_tol))
        else:
            row.update(_approximate(a, b, components, placed, checks))
        for r in requested.get(frozenset((a, b)), []):
            gap = row.get('min_distance_mm')
            minimum = r['min_value']
            if row['kind'] == 'not_checkable' or gap is None:
                verdict = 'not_checkable'
            else:
                verdict = 'met' if row['kind'] not in ('penetration',) and gap >= minimum - 1e-9 else 'violated'
            row.setdefault('requested_clearance', []).append({'relation': r['id'], 'min_mm': minimum, 'verdict': verdict})
        pairs.append(row)
    penetrations = [p for p in pairs if p['kind'] == 'penetration']
    unchecked = [p for p in pairs if p['kind'] == 'not_checkable']
    violated = [dict(c, a=p['a'], b=p['b']) for p in pairs for c in p.get('requested_clearance', []) if c['verdict'] == 'violated']
    if penetrations or violated:
        verdict = 'interference'
    elif unchecked:
        verdict = 'incomplete'
    else:
        verdict = 'no_penetration'
    return {'verdict': verdict, 'pairs': pairs, 'penetrations': [(p['a'], p['b']) for p in penetrations],
            'clearance_violations': violated, 'not_checkable': [(p['a'], p['b']) for p in unchecked],
            'note': 'Contact is touching within the contact tolerance with no common volume; penetration has common volume; '
                    'clearance is the exact gap. No strength, fit class or manufacturing tolerance is implied.'}


def _native(sa, sb, va, vb, contact_tol, volume_tol):
    box_a, box_b = sa.BoundingBox(), sb.BoundingBox()
    separated = (box_a.xmin - box_b.xmax > contact_tol or box_b.xmin - box_a.xmax > contact_tol or box_a.ymin - box_b.ymax > contact_tol
                 or box_b.ymin - box_a.ymax > contact_tol or box_a.zmin - box_b.zmax > contact_tol or box_b.zmin - box_a.zmax > contact_tol)
    common = 0.0
    if not separated:
        common = sum(s.Volume() for s in sa.intersect(sb).Solids())
    if common > volume_tol:
        row = {'kind': 'penetration', 'method': NATIVE, 'volume_mm3': common, 'min_distance_mm': 0.0}
        for inner, outer, v_inner, label in ((sa, sb, va, 'a_inside_b'), (sb, sa, vb, 'b_inside_a')):
            if abs(common - v_inner) <= max(volume_tol, 1e-6 * v_inner) and outer.isInside(inner.Center(), 1e-6):
                row['containment'] = label
        return row
    distance = float(sa.distance(sb))
    kind = 'contact' if distance <= contact_tol else 'clearance'
    return {'kind': kind, 'method': NATIVE, 'volume_mm3': common, 'min_distance_mm': distance}


def _approximate(a, b, components, placed, checks):
    meshes, problems = {}, []
    for cid in (a, b):
        if placed[cid].get('solid') is not None:
            meshes[cid] = tessellate(placed[cid]['solid'], checks['mesh_tolerance'])
        else:
            meshes[cid] = placed[cid]['mesh']
        problems += [f'{cid}: {p}' for p in mesh_fitness(meshes[cid])]
    if problems:
        return {'kind': 'not_checkable', 'method': MESH, 'reason': '; '.join(problems),
                'note': 'An open or unsuitable mesh cannot prove the absence of interference.'}
    ma, mb = _manifold(meshes[a]), _manifold(meshes[b])
    common = float((ma ^ mb).volume())
    limit = f"Tessellated at {checks['mesh_tolerance']} mm; volumes and gaps are approximate within about that tolerance."
    if common > checks['volume_tolerance']:
        row = {'kind': 'penetration', 'method': MESH, 'volume_mm3': common, 'min_distance_mm': 0.0, 'approximation': limit}
        for cid, other, label in ((a, mb, 'a_inside_b'), (b, ma, 'b_inside_a')):
            own = float(meshes[cid].volume)
            if abs(common - own) <= max(checks['volume_tolerance'], 1e-3 * own):
                row['containment'] = label
        return row
    gap = float(ma.min_gap(mb, 1e6))
    kind = 'contact' if gap <= checks['contact_tolerance'] + checks['mesh_tolerance'] else 'clearance'
    return {'kind': kind, 'method': MESH, 'volume_mm3': common, 'min_distance_mm': gap, 'approximation': limit}
