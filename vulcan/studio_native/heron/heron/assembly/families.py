"""Parametric part families with semantic references derived from their definition.

A reference is a named plane (point + outward normal) or axis (point + direction) in the part's local frame,
computed from the parameters, never picked from a face index. After a parameter change the same name is
recomputed; a name the new parameters no longer produce (a hole that disappeared) is reported as invalidated.
Each reference is also checked against the solid the kernel built ("materialized"): a planar face on that
plane with that outward normal, or a cylindrical face on that axis. This does not make arbitrary faces stable.
"""
from __future__ import annotations

import hashlib
import json
import numpy as np

from .spec import UNITS, evaluate

FAMILY_VERSION = 'heron-assembly-families-2'


def _plane(origin, normal, derived):
    return {'kind': 'plane', 'origin': [float(v) for v in origin], 'direction': [float(v) for v in normal], 'derived_from': derived}


def _axis(origin, direction, derived):
    return {'kind': 'axis', 'origin': [float(v) for v in origin], 'direction': [float(v) for v in direction], 'derived_from': derived}


def _point(origin, derived):
    return {'kind': 'point', 'origin': [float(v) for v in origin], 'direction': [0.0, 0.0, 0.0], 'derived_from': derived}


def _hole_axis(origin, direction, derived, diameter, length):
    """Axis of a hole or bore. hole: its diameter and axial extent measured from the origin along the direction."""
    ref = _axis(origin, direction, derived)
    ref['hole'] = {'diameter': float(diameter), 'extent': [0.0, float(length)]}
    return ref


# ISO 261 coarse pitch and ISO 273 clearance hole diameters (fine / medium / coarse series), mm.
THREADS = {
    'M2': {'nominal': 2.0, 'pitch': 0.4, 'clearance': (2.2, 2.4, 2.6)},
    'M2.5': {'nominal': 2.5, 'pitch': 0.45, 'clearance': (2.7, 2.9, 3.1)},
    'M3': {'nominal': 3.0, 'pitch': 0.5, 'clearance': (3.2, 3.4, 3.6)},
    'M4': {'nominal': 4.0, 'pitch': 0.7, 'clearance': (4.3, 4.5, 4.8)},
    'M5': {'nominal': 5.0, 'pitch': 0.8, 'clearance': (5.3, 5.5, 5.8)},
    'M6': {'nominal': 6.0, 'pitch': 1.0, 'clearance': (6.4, 6.6, 7.0)},
    'M8': {'nominal': 8.0, 'pitch': 1.25, 'clearance': (8.4, 9.0, 10.0)},
}


def resolve_thread(thread, raw_params):
    """Validate declared thread metadata: known designation, pitch not coarser than the coarse series. Returns the record."""
    if thread['designation'] not in THREADS:
        raise ValueError(f"screw: unknown thread {thread['designation']!r}; available: {', '.join(THREADS)}")
    row = THREADS[thread['designation']]
    pitch = thread.get('pitch') if thread.get('pitch') is not None else row['pitch']
    if pitch > row['pitch'] + 1e-9:
        raise ValueError(f"screw: pitch {pitch:g} mm is coarser than the coarse pitch {row['pitch']:g} mm of {thread['designation']}")
    return {'designation': thread['designation'], 'nominal_d': row['nominal'], 'pitch': float(pitch),
            'clearance_fine': row['clearance'][0], 'clearance_medium': row['clearance'][1], 'clearance_coarse': row['clearance'][2]}


def _pattern(dx, dy, count):
    """Hole pattern numbered counter-clockwise from (-x,-y): 1 (-,-), 2 (+,-), 3 (+,+), 4 (-,+)."""
    points = [(-dx / 2, -dy / 2), (dx / 2, -dy / 2), (dx / 2, dy / 2), (-dx / 2, dy / 2)]
    return points[:count]


def _p(name, default, low, high, label, integer=False):
    return {'name': name, 'default': default, 'min': low, 'max': high, 'label': label, **({'integer': True} if integer else {})}


FAMILIES = {
    'enclosure': {
        'label': 'Caja abierta con taladros de montaje en el fondo / Open enclosure with floor mounting holes',
        'params': [_p('inner_length', 100, 5, 1000, 'Interior X'), _p('inner_width', 70, 5, 1000, 'Interior Y'),
                   _p('inner_height', 30, 2, 1000, 'Interior Z (fondo a borde)'), _p('wall', 3, 0.5, 50, 'Pared'),
                   _p('floor', 3, 0.5, 50, 'Fondo'), _p('mount_dx', 80, 1, 1000, 'Separación X de taladros'),
                   _p('mount_dy', 50, 1, 1000, 'Separación Y de taladros'), _p('mount_hole_d', 3.2, 0.5, 50, 'Diámetro de taladro')],
        'frame': 'Origin at the centre of the outer bottom face; +Z up through the opening.',
    },
    'standoff': {
        'label': 'Separador cilíndrico / Cylindrical standoff',
        'params': [_p('height', 12, 0.5, 500, 'Altura'), _p('outer_d', 6, 1, 100, 'Diámetro exterior'), _p('bore_d', 3.2, 0, 99, 'Diámetro interior (0 = macizo)')],
        'frame': 'Origin at the centre of the bottom face; +Z along the axis.',
        'symmetry': ['axis'],
    },
    'plate': {
        'label': 'Placa con patrón de taladros (placa electrónica o tapa) / Plate with a hole pattern (board or lid)',
        'params': [_p('length', 90, 2, 1000, 'X'), _p('width', 60, 2, 1000, 'Y'), _p('thickness', 1.6, 0.2, 100, 'Espesor'),
                   _p('hole_d', 3.2, 0.5, 50, 'Diámetro de taladro'), _p('hole_dx', 80, 1, 1000, 'Separación X'),
                   _p('hole_dy', 50, 1, 1000, 'Separación Y'), _p('holes', 4, 0, 4, 'Número de taladros (0, 2 o 4)', integer=True)],
        'frame': 'Origin at the centre of the bottom face; +Z through the thickness.',
    },
    'bracket': {
        'label': 'Soporte en L con un taladro en la base / L bracket with one base hole',
        'params': [_p('length', 40, 5, 500, 'Base X'), _p('width', 20, 2, 500, 'Y'), _p('thickness', 4, 0.5, 50, 'Espesor'),
                   _p('height', 30, 2, 500, 'Altura del ala'), _p('hole_d', 4.5, 0.5, 50, 'Diámetro de taladro'),
                   _p('hole_x', 8, -250, 250, 'Posición X del taladro')],
        'frame': 'Origin at the centre of the base bottom face; the upright rises at -X; +Z up.',
    },
    'screw': {
        'label': 'Tornillo de cabeza cilíndrica (geometría nominal, rosca declarada como metadato) / Cap screw (nominal cylinder, declared thread metadata)',
        'params': [_p('shaft_d', 4, 0.5, 50, 'Diámetro de caña'), _p('length', 12, 1, 500, 'Longitud bajo cabeza'),
                   _p('head_d', 7, 1, 100, 'Diámetro de cabeza'), _p('head_h', 4, 0.5, 50, 'Altura de cabeza')],
        'frame': 'Origin at the centre of the underside of the head; the shaft runs towards -Z.',
        'symmetry': ['axis'],
    },
    'block': {
        'label': 'Bloque macizo / Solid block',
        'params': [_p('length', 20, 0.1, 2000, 'X'), _p('width', 20, 0.1, 2000, 'Y'), _p('height', 20, 0.1, 2000, 'Z')],
        'frame': 'Origin at the centre of the bottom face; +Z up.',
    },
}


def catalogue():
    return {name: {'label': f['label'], 'frame': f['frame'], 'params': f['params'], 'symmetry_axes': f.get('symmetry', []),
                   **({'threads': {k: {'nominal_d_mm': t['nominal'], 'coarse_pitch_mm': t['pitch'], 'clearance_hole_mm': dict(zip(('fine', 'medium', 'coarse'), t['clearance']))} for k, t in THREADS.items()}} if name == 'screw' else {}),
                   'references': reference_names(name, {p['name']: p['default'] for p in f['params']})}
            for name, f in FAMILIES.items()}


def resolve_params(family, raw, variables, units, thread=None):
    """Merge defaults, evaluate expressions, convert to mm and check bounds. Returns the mm values.

    thread (screw only): declared metric thread. When shaft_d is not given it defaults to the nominal diameter; a shaft_d that
    differs from the nominal diameter by more than 0.25 mm is refused. The thread is returned under values['thread']."""
    if family not in FAMILIES:
        raise ValueError(f'Unknown part family {family!r}; available: {", ".join(FAMILIES)}')
    if thread is not None:
        if family != 'screw':
            raise ValueError(f'{family}: thread metadata applies to the screw family only')
        thread = resolve_thread(thread, raw)
        if 'shaft_d' not in raw:
            raw = dict(raw, shaft_d=thread['nominal_d'] / UNITS[units])
    spec = {p['name']: p for p in FAMILIES[family]['params']}
    unknown = set(raw) - set(spec)
    if unknown:
        raise ValueError(f'{family}: unknown parameter(s) {sorted(unknown)}')
    factor = UNITS[units]
    values = {}
    for name, p in spec.items():
        given = raw.get(name, p['default'])
        number = evaluate(given, variables)
        if p.get('integer'):
            if number != int(number):
                raise ValueError(f'{family}.{name} must be a whole number')
            values[name] = int(number)
            continue
        number *= factor
        if number < p['min'] - 1e-9 or number > p['max'] + 1e-9:
            raise ValueError(f'{family}.{name}={number:g} mm is outside [{p["min"]}, {p["max"]}] mm')
        values[name] = number
    _validate(family, values)
    if thread is not None:
        if abs(values['shaft_d'] - thread['nominal_d']) > 0.25:
            raise ValueError(f"screw: shaft_d {values['shaft_d']:g} mm does not match thread {thread['designation']} (nominal {thread['nominal_d']:g} mm)")
        values['thread'] = thread
    return values


def _validate(family, v):
    if family == 'enclosure':
        if v['mount_dx'] + v['mount_hole_d'] >= v['inner_length'] or v['mount_dy'] + v['mount_hole_d'] >= v['inner_width']:
            raise ValueError('enclosure: the mounting holes must lie inside the interior floor')
    if family == 'standoff' and v['bore_d'] >= v['outer_d']:
        raise ValueError('standoff: bore_d must be smaller than outer_d')
    if family == 'plate':
        if v['holes'] not in (0, 2, 4):
            raise ValueError('plate: holes must be 0, 2 or 4')
        if v['holes'] and (v['hole_dx'] + v['hole_d'] >= v['length'] or (v['holes'] == 4 and v['hole_dy'] + v['hole_d'] >= v['width'])):
            raise ValueError('plate: the hole pattern must lie inside the plate')
    if family == 'bracket':
        if abs(v['hole_x']) + v['hole_d'] / 2 >= v['length'] / 2 or v['hole_d'] >= v['width']:
            raise ValueError('bracket: the hole must lie inside the base')
        if v['hole_x'] - v['hole_d'] / 2 <= -v['length'] / 2 + v['thickness']:
            raise ValueError('bracket: the hole would cut the upright')
    if family == 'screw' and v['head_d'] <= v['shaft_d']:
        raise ValueError('screw: head_d must be larger than shaft_d')


def reference_names(family, params):
    return sorted(family_references(family, {**{p['name']: p['default'] for p in FAMILIES[family]['params']}, **params}))


def family_references(family, v):
    refs = {}
    if family == 'enclosure':
        top = v['floor'] + v['inner_height']
        refs['bottom'] = _plane((0, 0, 0), (0, 0, -1), 'outer bottom face')
        refs['floor_top'] = _plane((0, 0, v['floor']), (0, 0, 1), 'interior floor face, z = floor')
        refs['rim'] = _plane((0, 0, top), (0, 0, 1), 'top edge of the walls, z = floor + inner_height')
        for i, (x, y) in enumerate(_pattern(v['mount_dx'], v['mount_dy'], 4), 1):
            refs[f'mount_{i}'] = _hole_axis((x, y, 0), (0, 0, 1), f'floor hole {i} axis', v['mount_hole_d'], v['floor'])
            refs[f'mount_{i}_center'] = _point((x, y, 0), f'floor hole {i} centre on the outer bottom face')
        L, W = v['inner_length'] + 2 * v['wall'], v['inner_width'] + 2 * v['wall']
        for i, (x, y) in enumerate(_pattern(L, W, 4), 1):
            refs[f'corner_{i}'] = _point((x, y, 0), f'outer bottom corner {i}')
    elif family == 'standoff':
        refs['bottom'] = _plane((0, 0, 0), (0, 0, -1), 'bottom face')
        refs['top'] = _plane((0, 0, v['height']), (0, 0, 1), 'top face, z = height')
        refs['axis'] = _axis((0, 0, 0), (0, 0, 1), 'cylinder axis')
        if v['bore_d'] > 0:
            refs['axis']['hole'] = {'diameter': float(v['bore_d']), 'extent': [0.0, float(v['height'])]}
        refs['bottom_center'] = _point((0, 0, 0), 'centre of the bottom face')
        refs['top_center'] = _point((0, 0, v['height']), 'centre of the top face')
    elif family == 'plate':
        refs['bottom'] = _plane((0, 0, 0), (0, 0, -1), 'bottom face')
        refs['top'] = _plane((0, 0, v['thickness']), (0, 0, 1), 'top face, z = thickness')
        for i, (x, y) in enumerate(_pattern(v['hole_dx'], v['hole_dy'], v['holes']), 1):
            refs[f'hole_{i}'] = _hole_axis((x, y, 0), (0, 0, 1), f'hole {i} axis', v['hole_d'], v['thickness'])
            refs[f'hole_{i}_center'] = _point((x, y, 0), f'hole {i} centre on the bottom face')
        for i, (x, y) in enumerate(_pattern(v['length'], v['width'], 4), 1):
            refs[f'corner_{i}'] = _point((x, y, 0), f'bottom corner {i}')
            refs[f'top_corner_{i}'] = _point((x, y, v['thickness']), f'top corner {i}')
    elif family == 'bracket':
        refs['bottom'] = _plane((0, 0, 0), (0, 0, -1), 'base bottom face')
        refs['top'] = _plane((0, 0, v['thickness']), (0, 0, 1), 'base top face')
        refs['back'] = _plane((-v['length'] / 2, 0, 0), (-1, 0, 0), 'outer face of the upright')
        refs['hole_1'] = _hole_axis((v['hole_x'], 0, 0), (0, 0, 1), 'base hole axis', v['hole_d'], v['thickness'])
        refs['hole_1_center'] = _point((v['hole_x'], 0, 0), 'base hole centre on the bottom face')
        for i, (x, y) in enumerate(_pattern(v['length'], v['width'], 4), 1):
            refs[f'corner_{i}'] = _point((x, y, 0), f'base bottom corner {i}')
    elif family == 'screw':
        refs['under_head'] = _plane((0, 0, 0), (0, 0, -1), 'underside of the head')
        refs['tip'] = _plane((0, 0, -v['length']), (0, 0, -1), 'shaft end')
        refs['axis'] = _axis((0, 0, 0), (0, 0, -1), 'shaft axis, insertion direction')
        refs['head_center'] = _point((0, 0, 0), 'centre of the underside of the head')
        refs['tip_center'] = _point((0, 0, -v['length']), 'centre of the shaft end')
    elif family == 'block':
        refs['bottom'] = _plane((0, 0, 0), (0, 0, -1), 'bottom face')
        refs['top'] = _plane((0, 0, v['height']), (0, 0, 1), 'top face')
        for i, (x, y) in enumerate(_pattern(v['length'], v['width'], 4), 1):
            refs[f'corner_{i}'] = _point((x, y, 0), f'bottom corner {i}')
            refs[f'top_corner_{i}'] = _point((x, y, v['height']), f'top corner {i}')
    return refs


def datum_references():
    return {'origin.xy': _plane((0, 0, 0), (0, 0, 1), 'local XY datum plane'),
            'origin.yz': _plane((0, 0, 0), (1, 0, 0), 'local YZ datum plane'),
            'origin.xz': _plane((0, 0, 0), (0, 1, 0), 'local XZ datum plane'),
            'origin.x': _axis((0, 0, 0), (1, 0, 0), 'local X datum axis'),
            'origin.y': _axis((0, 0, 0), (0, 1, 0), 'local Y datum axis'),
            'origin.z': _axis((0, 0, 0), (0, 0, 1), 'local Z datum axis')}


def build_family(family, v):
    import cadquery as cq
    if family == 'enclosure':
        L, W = v['inner_length'] + 2 * v['wall'], v['inner_width'] + 2 * v['wall']
        H = v['floor'] + v['inner_height']
        body = cq.Workplane('XY').box(L, W, H, centered=(True, True, False))
        pocket = cq.Workplane('XY').workplane(offset=v['floor']).box(v['inner_length'], v['inner_width'], v['inner_height'] + 1, centered=(True, True, False))
        body = body.cut(pocket)
        holes = cq.Workplane('XY').workplane(offset=-1).pushPoints(_pattern(v['mount_dx'], v['mount_dy'], 4)).circle(v['mount_hole_d'] / 2).extrude(v['floor'] + 2)
        shape = body.cut(holes)
    elif family == 'standoff':
        shape = cq.Workplane('XY').circle(v['outer_d'] / 2).extrude(v['height'])
        if v['bore_d'] > 0:
            shape = shape.cut(cq.Workplane('XY').workplane(offset=-1).circle(v['bore_d'] / 2).extrude(v['height'] + 2))
    elif family == 'plate':
        shape = cq.Workplane('XY').box(v['length'], v['width'], v['thickness'], centered=(True, True, False))
        if v['holes']:
            shape = shape.cut(cq.Workplane('XY').workplane(offset=-1).pushPoints(_pattern(v['hole_dx'], v['hole_dy'], v['holes']))
                              .circle(v['hole_d'] / 2).extrude(v['thickness'] + 2))
    elif family == 'bracket':
        base = cq.Workplane('XY').box(v['length'], v['width'], v['thickness'], centered=(True, True, False))
        upright = cq.Workplane('XY').center(-v['length'] / 2 + v['thickness'] / 2, 0).box(v['thickness'], v['width'], v['height'], centered=(True, True, False))
        shape = base.union(upright)
        shape = shape.cut(cq.Workplane('XY').workplane(offset=-1).center(v['hole_x'], 0).circle(v['hole_d'] / 2).extrude(v['thickness'] + 2))
    elif family == 'screw':
        head = cq.Workplane('XY').circle(v['head_d'] / 2).extrude(v['head_h'])
        shaft = cq.Workplane('XY').circle(v['shaft_d'] / 2).extrude(-v['length'])
        shape = head.union(shaft)
    elif family == 'block':
        shape = cq.Workplane('XY').box(v['length'], v['width'], v['height'], centered=(True, True, False))
    else:
        raise ValueError(f'Unknown part family {family!r}')
    solids = shape.solids().vals()
    if len(solids) != 1 or not solids[0].isValid() or solids[0].Volume() <= 0:
        raise ValueError(f'{family}: the CAD kernel did not produce one valid solid')
    return solids[0]


def recipe_references(recipe):
    """References of a Heron recipe: per primitive top/bottom planes and cylinder axes, named p<i>.<name>."""
    from scipy.spatial.transform import Rotation
    refs = {}
    for i, part in enumerate(recipe['parts']):
        rotation = Rotation.from_euler('xyz', part.get('rotation', (0, 0, 0)), degrees=True)
        position = np.array(part.get('position', (0, 0, 0)), float)
        kind = part.get('kind', 'box')
        half = (part.get('size', (40, 30, 5))[2] if kind == 'box' else part.get('height', 10)) / 2
        if kind == 'sphere':
            continue
        z = rotation.apply((0, 0, 1))
        derived = f'primitive {i} ({kind}, {part.get("operation", "union")})'
        refs[f'p{i}.top'] = _plane(position + z * half, z, derived + ' +Z face')
        refs[f'p{i}.bottom'] = _plane(position - z * half, -z, derived + ' -Z face')
        if kind == 'cylinder':
            refs[f'p{i}.axis'] = _axis(position, z, derived + ' axis')
    return refs


def materialized(solid, ref, tol=1e-5):
    """True when the solid has a planar face on the plane with that outward normal, or a cylindrical face on the axis."""
    from OCP.BRepAdaptor import BRepAdaptor_Surface
    origin, direction = np.array(ref['origin']), np.array(ref['direction'])
    if ref['kind'] == 'point':
        for vertex in solid.Vertices():
            if np.linalg.norm(np.array(vertex.toTuple()) - origin) < tol:
                return True
        for face in solid.Faces():  # a hole centre lies on the axis of a cylindrical face
            if face.geomType() == 'CYLINDER':
                axis = BRepAdaptor_Surface(face.wrapped).Cylinder().Axis()
                point = np.array((axis.Location().X(), axis.Location().Y(), axis.Location().Z()))
                d = np.array((axis.Direction().X(), axis.Direction().Y(), axis.Direction().Z()))
                if np.linalg.norm(np.cross(point - origin, d)) < tol:
                    return True
        return False
    for face in solid.Faces():
        kind = face.geomType()
        if ref['kind'] == 'plane' and kind == 'PLANE':
            center = np.array(face.Center().toTuple())
            normal = np.array(face.normalAt(face.Center()).toTuple())
            if normal @ direction > 1 - 1e-9 and abs(direction @ (center - origin)) < tol:
                return True
        if ref['kind'] == 'axis' and kind == 'CYLINDER':
            axis = BRepAdaptor_Surface(face.wrapped).Cylinder().Axis()
            point = np.array((axis.Location().X(), axis.Location().Y(), axis.Location().Z()))
            d = np.array((axis.Direction().X(), axis.Direction().Y(), axis.Direction().Z()))
            if abs(d @ direction) > 1 - 1e-9 and np.linalg.norm(np.cross(point - origin, direction)) < tol:
                return True
    return False


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(',', ':')).encode()).hexdigest()

