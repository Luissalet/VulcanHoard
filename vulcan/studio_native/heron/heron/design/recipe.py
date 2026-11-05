"""Import the sketch extrusion of a Vulcan library CAD recipe as a fully constrained Heron part.

The library recipe dimensions sketch points with parameter expressions ("$leg_x"). Here every edge of the outline
becomes a constraint between its two points: horizontal or vertical when the edge is axis-aligned, plus its signed
horizontal and vertical distances written with the same parameters, so the sketch has zero degrees of freedom and a
parameter change regenerates it. Only the first extrude node is imported; anything else is listed as not imported.
"""
from __future__ import annotations

import math
import re

from ..assembly.spec import evaluate
from .sketch import _area

MAX_POINTS = 200


def _expr(raw):
    if isinstance(raw, bool) or not isinstance(raw, (int, float, str)):
        raise ValueError('Profile coordinates must be numbers or parameter expressions')
    return float(raw) if not isinstance(raw, str) else re.sub(r'\$([A-Za-z_]\w*)', r'\1', raw.strip())


def _find_extrude(node, path='root'):
    if not isinstance(node, dict):
        return None, None
    if node.get('type') == 'extrude':
        return node, path
    for key in ('base', 'child'):
        found = _find_extrude(node.get(key), f'{path}.{key}')
        if found[0]:
            return found
    for key in ('children',):
        for i, child in enumerate(node.get(key) or []):
            found = _find_extrude(child, f'{path}.{key}[{i}]')
            if found[0]:
                return found
    return None, None


def _name(text):
    text = re.sub(r'[^a-z0-9_]', '_', str(text).lower())
    return text if re.match(r'^[a-z]', text) else 'p_' + text


def import_recipe(recipe):
    """(part spec, import report) for a library recipe whose tree contains an extrude node."""
    if not isinstance(recipe, dict):
        raise ValueError('Expected a library CAD recipe object')
    node, path = _find_extrude(recipe.get('root'))
    if node is None:
        raise ValueError('The recipe has no extrude node to import (revolve and primitives stay in the library recipe)')
    parameters = {}
    for name, entry in (recipe.get('params') or {}).items():
        raw = entry.get('value') if isinstance(entry, dict) else entry
        parameters[_name(name)] = _expr(raw)
    values = {}
    for name, raw in parameters.items():
        values[name] = evaluate(raw, values)
    loops = [('o', node['profile'])] + [(f'h{i}', loop) for i, loop in enumerate(node.get('holes') or [])]
    if sum(len(loop) for _, loop in loops) > MAX_POINTS:
        raise ValueError(f'At most {MAX_POINTS} sketch points can be imported')
    points, lines, constraints, profile, holes = {}, {}, [], [], []
    evaluated = {}
    for key, loop in loops:
        ids = [f'{key}_{i}' for i in range(len(loop))]
        for pid, (u, v) in zip(ids, loop):
            points[pid] = [_expr(u), _expr(v)]
            evaluated[pid] = (evaluate(points[pid][0], values), evaluate(points[pid][1], values))
        names = []
        for i, pid in enumerate(ids):
            lid = f'{key}_e{i}'
            lines[lid] = [pid, ids[(i + 1) % len(ids)]]
            names.append(lid)
        if key == 'o':
            profile = names
            constraints.append({'id': 'o_origin', 'type': 'fixed', 'point': ids[0], 'at': points[ids[0]]})
        else:
            holes.append(names)
            for axis, kind in ((0, 'distance_x'), (1, 'distance_y')):
                constraints.append({'id': f'{key}_place_{"xy"[axis]}', 'type': kind, 'a': 'o_0', 'b': ids[0],
                                    'value': f'({points[ids[0]][axis]}) - ({points["o_0"][axis]})'})
        for i in range(len(ids) - 1):  # the closing edge is implied by the others
            a, b, lid = ids[i], ids[i + 1], names[i]
            du = evaluated[b][0] - evaluated[a][0]
            dv = evaluated[b][1] - evaluated[a][1]
            if abs(du) < 1e-9 and abs(dv) < 1e-9:
                raise ValueError(f'{path}: points {i} and {i + 1} of loop {key} coincide')
            if abs(dv) < 1e-9:
                constraints.append({'id': f'{lid}_h', 'type': 'horizontal', 'line': lid})
            else:
                constraints.append({'id': f'{lid}_dy', 'type': 'distance_y', 'a': a, 'b': b, 'value': f'({points[b][1]}) - ({points[a][1]})'})
            if abs(du) < 1e-9:
                constraints.append({'id': f'{lid}_v', 'type': 'vertical', 'line': lid})
            else:
                constraints.append({'id': f'{lid}_dx', 'type': 'distance_x', 'a': a, 'b': b, 'value': f'({points[b][0]}) - ({points[a][0]})'})
    height = _expr(node['height'])
    area = abs(_area([evaluated[f'o_{i}'] for i in range(len(node['profile']))]))
    for i, loop in enumerate(node.get('holes') or []):
        area -= abs(_area([evaluated[f'h{i}_{j}'] for j in range(len(loop))]))
    volume = area * evaluate(height, values)
    title = str(recipe.get('name') or recipe.get('title') or 'Imported sketch')[:100] + ' (Heron)'
    spec = {'title': title, 'parameters': parameters,
            'sketch': {'points': points, 'lines': lines, 'profile': profile, 'holes': holes, 'constraints': constraints},
            'extrude': {'depth': height},
            'checks': [{'id': 'depth_matches_recipe', 'measure': 'bbox_z', 'value': height}]}
    skipped = []
    if path != 'root':
        skipped.append(f'nodes around {path} (booleans, transforms, patterns) stay in the library recipe; add holes as Heron hole features')
    if node.get('axis', 'z') != 'z':
        skipped.append(f"the library extrudes along {node['axis']}; Heron places the sketch in XY and extrudes along +z")
    report = {'source_node': path, 'parameters': list(parameters), 'points': len(points), 'constraints': len(constraints),
              'expected_volume_mm3': volume, 'not_imported': skipped, 'math': 'exact' if math.isfinite(volume) else 'invalid'}
    return spec, report
