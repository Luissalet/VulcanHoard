"""Evaluate an assembly revision: parts, references, solve, interference, exports and their round trip."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

import numpy as np

from . import families, fasteners, interference, solver
from .spec import UNITS, Assembly, evaluate

MANIFEST_FORMAT = 'heron-assembly-manifest'
LIMITS = ('Relations are exact rigid-body constraints between references derived from the part definitions; '
          'they say nothing about strength, fit classes or manufacturing tolerances. Face references are not '
          'promised to be stable after arbitrary edits: references are recomputed by name from the parameters and '
          'a name the new definition no longer produces is reported as invalidated. Screws are nominal cylinders '
          'without modelled threads (a declared thread is metadata for clearance checks). Remaining degrees of freedom keep their previous or seeded pose, which is not a choice '
          'of solution.')


def sha256(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def resolve_component(component, variables, builds):
    source = component['source']
    if source['kind'] == 'family':
        params = families.resolve_params(source['family'], source.get('params', {}), variables, component['units'], source.get('thread'))
        solid = families.build_family(source['family'], params)
        refs = {**families.family_references(source['family'], params), **families.datum_references()}
        symmetry = families.FAMILIES[source['family']].get('symmetry', [])
        revision = {'kind': 'family', 'family': source['family'], 'family_version': families.FAMILY_VERSION, 'params_mm': params,
                    'digest': families.digest({'v': families.FAMILY_VERSION, 'family': source['family'], 'params': params})}
        return {'solid': solid, 'mesh': None, 'refs': refs, 'symmetry': symmetry, 'revision': revision, 'thread': params.get('thread')}
    if source['kind'] == 'build':
        import cadquery as cq
        if component['units'] != 'mm':
            raise ValueError(f"{component['id']}: Heron builds are in mm")
        info = builds.get(source['build_id'])
        if not info:
            raise ValueError(f"{component['id']}: build {source['build_id']} is not a finished Heron build")
        if sha256(info['step_path']) != info['sha256']:
            raise ValueError(f"{component['id']}: build {source['build_id']} STEP changed since verification")
        shape = cq.importers.importStep(info['step_path'])
        solids = shape.solids().vals()
        if not solids:
            raise ValueError(f"{component['id']}: build has no solid")
        solid = solids[0] if len(solids) == 1 else cq.Compound.makeCompound(solids)
        refs = {**families.recipe_references(info['recipe']), **families.datum_references()}
        revision = {'kind': 'build', 'build_id': source['build_id'], 'step_sha256': info['sha256'], 'recipe_digest': families.digest(info['recipe'])}
        return {'solid': solid, 'mesh': None, 'refs': refs, 'symmetry': [], 'revision': revision}
    import trimesh
    factor = UNITS[component['units']]
    vertices = np.asarray(source['vertices'], float) * factor
    faces = np.asarray(source['faces'], int)
    if faces.min() < 0 or faces.max() >= len(vertices):
        raise ValueError(f"{component['id']}: mesh face index out of range")
    mesh = trimesh.Trimesh(vertices, faces, process=False)
    revision = {'kind': 'mesh', 'digest': families.digest({'v': vertices.round(9).tolist(), 'f': faces.tolist()}),
                'watertight': bool(mesh.is_watertight), 'winding_consistent': bool(mesh.is_winding_consistent)}
    return {'solid': None, 'mesh': mesh, 'refs': families.datum_references(), 'symmetry': [], 'revision': revision}


def prevalidate(spec):
    """Schema, expressions and part-family bounds, without the CAD kernel: refuse before a revision is created."""
    model = Assembly.model_validate(spec)
    spec = model.model_dump(mode='json')
    for component in spec['components']:
        if component['source']['kind'] == 'family':
            try:
                families.resolve_params(component['source']['family'], component['source'].get('params', {}), spec['variables'], component['units'],
                                        component['source'].get('thread'))
            except ValueError as exc:
                raise ValueError(f"{component['id']}: {exc}") from exc
    for relation in spec['relations']:
        for key in ('value', 'min'):
            if relation.get(key) is not None:
                number = evaluate(relation[key], spec['variables'])
                if relation['type'] == 'angle' and key == 'value' and not 0 <= number <= 180:
                    raise ValueError(f"{relation['id']}: angle must be between 0 and 180 degrees, got {number:g}")
    return spec


# Reference kinds accepted at each end (a, b) of a positioning relation.
KINDS = {'coincident': (('plane',), ('plane',)), 'distance': (('plane',), ('plane',)), 'coaxial': (('axis',), ('axis',)),
         'angle': (('plane', 'axis'), ('plane', 'axis')), 'point_on_plane': (('point',), ('plane',)),
         'point_on_axis': (('point',), ('axis',)), 'point_coincident': (('point',), ('point',))}


def concrete_value(relation, variables):
    """Numeric value of a positioning relation: signed offset (distance, point_on_plane), degrees (angle) or zero."""
    if relation['type'] in ('distance', 'angle') or (relation['type'] == 'point_on_plane' and relation.get('value') is not None):
        number = evaluate(relation['value'], variables)
        if relation['type'] == 'angle' and not 0 <= number <= 180:
            raise ValueError(f"{relation['id']}: angle must be between 0 and 180 degrees, got {number:g}")
        return number
    return 0.0


def _ref_split(text):
    component, ref = text.split('.', 1)
    return component, ref


def evaluate_revision(spec, *, builds=None, previous=None, initial_poses=None, folder=None, assembly_id='assembly', revision=1):
    """Return the result record; write exports into folder when given. previous: the result of the base revision."""
    builds = builds or {}
    model = Assembly.model_validate(spec)
    spec = model.model_dump(mode='json')
    variables = spec['variables']
    before = {key: sha256(info['step_path']) for key, info in builds.items()}
    parts = {}
    for component in spec['components']:
        parts[component['id']] = resolve_component(component, variables, builds)
    previous_refs = {cid: set(info['references']) for cid, info in (previous or {}).get('components', {}).items()}
    # Relations: concrete values, reference existence and kind.
    geometric, fixed, excluded, all_rel = [], {}, [], []
    kinds = KINDS
    for relation in spec['relations']:
        row = dict(relation)
        if relation['type'] == 'fixed':
            fixed[relation['component']] = solver.pose_from_spec(next(c for c in spec['components'] if c['id'] == relation['component'])['pose'])
            all_rel.append(row)
            continue
        if relation['type'] == 'clearance':
            row['min_value'] = evaluate(relation['min'], variables)
            all_rel.append(row)
            continue
        problem = None
        ends = []
        for side in ('a', 'b'):
            cid, ref = _ref_split(relation[side])
            allowed = kinds[relation['type']][0 if side == 'a' else 1]
            ends.append((cid, ref))
            if ref not in parts[cid]['refs']:
                if ref in previous_refs.get(cid, set()):
                    problem = {'state': 'invalidated', 'reason': f'{relation[side]} existed in the base revision but the new part definition no longer produces it'}
                else:
                    problem = {'state': 'invalid_reference', 'reason': f'{relation[side]} is not a reference of {cid}; available: {", ".join(sorted(parts[cid]["refs"]))}'}
                break
            if parts[cid]['refs'][ref]['kind'] not in allowed:
                problem = {'state': 'invalid_reference', 'reason': f'{relation["type"]} needs {" or ".join(allowed)} references at {side}; {relation[side]} is {"an" if parts[cid]["refs"][ref]["kind"] == "axis" else "a"} {parts[cid]["refs"][ref]["kind"]}'}
                break
        if problem:
            excluded.append({'id': relation['id'], **problem})
            all_rel.append(row)
            continue
        if relation['type'] == 'coaxial' and relation['orientation'] not in ('aligned', 'opposed', 'either'):
            raise ValueError(f"{relation['id']}: invalid orientation")
        value = concrete_value(relation, variables)
        geometric.append({'id': relation['id'], 'type': relation['type'], 'a': ends[0], 'b': ends[1], 'value': value, 'orientation': relation['orientation']})
        all_rel.append(row)
    # Initial poses: previous solved poses keep remaining freedoms where they were (minimal change).
    initial = {}
    for component in spec['components']:
        cid = component['id']
        if initial_poses and cid in initial_poses:
            initial[cid] = solver.pose_from_matrix(initial_poses[cid])
        elif previous and cid in previous.get('poses', {}):
            initial[cid] = solver.pose_from_matrix(previous['poses'][cid]['matrix'])
        else:
            initial[cid] = solver.pose_from_spec(component['pose'])
    initial.update(fixed)
    sizes = [p['solid'].BoundingBox().DiagonalLength for p in parts.values() if p['solid'] is not None]
    scale = max([1.0] + [float(s) for s in sizes])
    components = {cid: {'refs': p['refs'], 'symmetry': p['symmetry']} for cid, p in parts.items()}
    solved = solver.run(components, geometric, initial, fixed, scale)
    poses = solved['poses']
    # Interference at the reported poses (not for an ambiguous result: no pose was chosen).
    if solved['status'] == 'ambiguous':
        clash = {'verdict': 'not_evaluated', 'pairs': [], 'note': 'Ambiguous assembly: no pose was chosen, so interference was not evaluated.'}
    else:
        volumes = {cid: (float(p['solid'].Volume()) if p['solid'] is not None else float(abs(p['mesh'].volume)) if p['mesh'].is_watertight else 0.0) for cid, p in parts.items()}
        clash = interference.check({cid: {'solid': p['solid'], 'mesh': p['mesh'], 'volume': volumes[cid]} for cid, p in parts.items()},
                                   poses, [r for r in all_rel], spec['checks'])
    fastener_report = fasteners.check(parts, poses, None) if solved['status'] != 'ambiguous' else {'checked_screws': 0, 'findings': [], 'note': 'Ambiguous assembly: no pose was chosen, so thread clearance was not evaluated.'}
    relation_rows = {r['id']: r for r in solved['relations']}
    report_relations = []
    for relation in all_rel:
        row = {'id': relation['id'], 'type': relation['type']}
        if relation['type'] == 'fixed':
            row.update(component=relation['component'], state='satisfied')
        elif relation['type'] == 'clearance':
            pair = next((p for p in clash.get('pairs', []) for c in p.get('requested_clearance', []) if c['relation'] == relation['id']), None)
            verdict = next((c['verdict'] for c in (pair or {}).get('requested_clearance', []) if c['relation'] == relation['id']), 'not_evaluated')
            row.update(a=relation['a'], b=relation['b'], min_mm=relation['min_value'], measured_gap_mm=(pair or {}).get('min_distance_mm'),
                       state={'met': 'satisfied', 'violated': 'violated'}.get(verdict, verdict))
        else:
            row.update(a=relation['a'], b=relation['b'])
            if relation['id'] in relation_rows:
                row.update(relation_rows[relation['id']])
            else:
                row.update(next(e for e in excluded if e['id'] == relation['id']))
            if relation['type'] in ('distance', 'point_on_plane') and relation.get('value') is not None:
                row['value_mm'] = evaluate(relation['value'], variables)
            if relation['type'] == 'angle':
                row['value_deg'] = evaluate(relation['value'], variables)
        report_relations.append(row)
    component_rows = {}
    for component in spec['components']:
        cid, part = component['id'], parts[component['id']]
        names = set(part['refs'])
        refs_out = {}
        for name, ref in part['refs'].items():
            point, direction = solver.world(ref, poses[cid])
            refs_out[name] = {'kind': ref['kind'], 'derived_from': ref['derived_from'], 'local_origin': ref['origin'], 'local_direction': ref['direction'],
                              'world_origin': solver._round(point), 'world_direction': solver._round(direction)}
            if ref.get('hole'):
                refs_out[name]['hole_diameter_mm'] = ref['hole']['diameter']
            if part['solid'] is not None and not name.startswith('origin.'):
                refs_out[name]['materialized'] = families.materialized(part['solid'], ref)
        row = {'id': cid, 'name': component['name'] or cid, 'units': component['units'], 'source': _source_summary(component['source']),
               'revision': part['revision'], 'pose': solver.pose_record(poses[cid]), 'references': refs_out,
               'kind': 'solid' if part['solid'] is not None else 'mesh'}
        if part['solid'] is not None:
            row['volume_mm3'] = float(part['solid'].Volume())
        if previous and cid in previous.get('components', {}):
            old = previous['components'][cid]['references']
            row['reference_changes'] = {
                'survived': sorted(n for n in names & set(old)),
                'invalidated': sorted(set(old) - names),
                'added': sorted(names - set(old)),
                'moved_in_part': sorted(n for n in names & set(old) if np.linalg.norm(np.array(old[n]['local_origin']) - np.array(part['refs'][n]['origin'])) > 1e-9
                                        or np.linalg.norm(np.array(old[n]['local_direction']) - np.array(part['refs'][n]['direction'])) > 1e-9)}
        component_rows[cid] = row
    mounting = []
    if previous:
        for relation in geometric:
            if relation['type'] != 'coaxial':
                continue
            for cid, ref in (relation['a'], relation['b']):
                old = previous.get('components', {}).get(cid, {}).get('references', {}).get(ref)
                if old:
                    now = component_rows[cid]['references'][ref]
                    lateral = np.array(now['world_origin']) - np.array(old['world_origin'])
                    lateral -= (lateral @ np.array(now['world_direction'])) * np.array(now['world_direction'])
                    mounting.append({'reference': f'{cid}.{ref}', 'relation': relation['id'], 'axis_shift_mm': float(np.linalg.norm(lateral)),
                                     'direction_change_deg': float(np.degrees(np.arccos(np.clip(np.array(now['world_direction']) @ np.array(old['world_direction']), -1, 1))))})
    after = {key: sha256(info['step_path']) for key, info in builds.items()}
    result = {
        'ok': True, 'assembly_id': assembly_id, 'revision': revision, 'title': spec['title'], 'units': 'mm',
        'status': solved['status'], 'complete': not excluded,
        'components': component_rows, 'relations': report_relations, 'excluded_relations': excluded,
        'remaining_dof': solved['remaining_dof'], 'symmetric_dof': solved['symmetric_dof'], 'conflicts': solved['conflicts'],
        'ambiguity': solved['ambiguity'], 'redundant_equations': solved.get('redundant_but_consistent', 0),
        'poses': {cid: solver.pose_record(p) for cid, p in poses.items()}, 'interference': clash, 'fasteners': fastener_report,
        'mounting_axes': mounting, 'originals': {'builds_before': before, 'builds_after': after, 'unchanged': before == after},
        'tolerances': {'linear_mm': solver.LINEAR_TOL, 'angular_rad': solver.ANGULAR_TOL, **spec['checks']},
        'limits': LIMITS,
    }
    if folder is not None:
        result['files'], result['roundtrip'] = export(Path(folder), spec, parts, poses, result)
    return result


def _source_summary(source):
    if source['kind'] == 'mesh':
        return {'kind': 'mesh', 'vertices': len(source['vertices']), 'faces': len(source['faces'])}
    return source


def manifest(spec, result):
    return {'format': MANIFEST_FORMAT, 'version': 1, 'assembly_id': result['assembly_id'], 'revision': result['revision'],
            'title': spec['title'], 'units': 'mm', 'status': result['status'], 'spec': spec,
            'components': [{'id': cid, 'name': row['name'], 'kind': row['kind'], 'revision': row['revision'], 'pose': row['pose'],
                            'step_file': f'components/{cid}.step' if row['kind'] == 'solid' else None, 'glb_node': cid}
                           for cid, row in result['components'].items()],
            'relations': result['relations'], 'interference_verdict': result['interference']['verdict'], 'limits': LIMITS}


def _location(pose):
    import cadquery as cq
    from OCP.gp import gp_Trsf
    R, t = pose
    trsf = gp_Trsf()
    trsf.SetValues(*R[0], t[0], *R[1], t[1], *R[2], t[2])
    return cq.Location(trsf)


def _matrix_of(location):
    trsf = location.wrapped.Transformation()
    m = np.eye(4)
    for i in range(3):
        for j in range(4):
            m[i, j] = trsf.Value(i + 1, j + 1)
    return m


def export(folder, spec, parts, poses, result):
    import cadquery as cq
    import trimesh
    import xml.etree.ElementTree as ET
    folder.mkdir(parents=True, exist_ok=True)
    (folder / 'components').mkdir(exist_ok=True)
    assembly = cq.Assembly(name='assembly')
    scene = trimesh.Scene()
    world_solids = []
    for cid, part in parts.items():
        matrix = np.array(result['poses'][cid]['matrix'])
        if part['solid'] is not None:
            cq.exporters.export(cq.Workplane().add(part['solid']), str(folder / 'components' / f'{cid}.step'))
            assembly.add(part['solid'], name=cid, loc=_location(poses[cid]))
            local = interference.tessellate(part['solid'], spec['checks']['mesh_tolerance'])
            world_solids.append(interference.place_solid(part['solid'], poses[cid]))
        else:
            local = part['mesh']
        scene.add_geometry(local, node_name=cid, geom_name=cid, transform=matrix)
    assembly.export(str(folder / 'assembly.step'))
    (folder / 'assembly.glb').write_bytes(scene.export(file_type='glb'))
    if world_solids:
        compound = cq.Compound.makeCompound(world_solids)
        for name, direction in (('isometric', (-1, -1, 1)), ('front', (0, -1, 0)), ('top', (0, 0, 1))):
            view = folder / f'{name}.svg'
            cq.exporters.export(cq.Workplane().add(compound), str(view), opt={'width': 800, 'height': 600, 'marginLeft': 100, 'marginTop': 75,
                                                                              'projectionDir': direction, 'showAxes': False, 'showHidden': False})
            svg = ET.parse(view)
            svg.getroot().set('viewBox', '0 0 800 600')
            svg.getroot().set('preserveAspectRatio', 'xMidYMid meet')
            svg.write(view, encoding='utf-8', xml_declaration=True)
    (folder / 'manifest.json').write_text(json.dumps(manifest(spec, result), ensure_ascii=False, indent=1, allow_nan=False), encoding='utf-8')
    roundtrip = verify_roundtrip(folder, result)
    files = {}
    for path in sorted(folder.rglob('*')):
        if path.is_file() and path.suffix in ('.step', '.glb', '.json', '.svg') and path.name not in ('input.json', 'result.json'):
            files[path.relative_to(folder).as_posix()] = {'bytes': path.stat().st_size, 'sha256': sha256(path)}
    return files, roundtrip


def verify_roundtrip(folder, result, tol=1e-6):
    """Reimport the exports and compare component names, poses and volumes with the solved record."""
    import cadquery as cq
    import trimesh
    out = {}
    expected = {cid: np.array(row['pose']['matrix']) for cid, row in result['components'].items()}
    solids = {cid for cid, row in result['components'].items() if row['kind'] == 'solid'}
    imported = cq.Assembly.importStep(str(folder / 'assembly.step'))
    step_rows = {}
    for child in imported.children:
        volume = sum(s.Volume() for s in child.obj.Solids()) if child.obj is not None else 0.0
        step_rows[child.name] = {'matrix': _matrix_of(child.loc), 'volume': volume}
    step_ok = set(step_rows) == solids and all(
        np.abs(step_rows[c]['matrix'] - expected[c]).max() <= tol and abs(step_rows[c]['volume'] - result['components'][c]['volume_mm3']) <= 1e-6 * max(1, result['components'][c]['volume_mm3'])
        for c in solids)
    out['step'] = {'ok': bool(step_ok), 'components': sorted(step_rows), 'max_pose_error': float(max([np.abs(step_rows[c]['matrix'] - expected[c]).max() for c in solids if c in step_rows] or [0])),
                   'note': 'Reimported with the CAD kernel; names, placements and solid volumes compared.'}
    scene = trimesh.load(str(folder / 'assembly.glb'), force='scene')
    glb_rows = {}
    for node in scene.graph.nodes_geometry:
        matrix, geometry = scene.graph.get(node)
        glb_rows[node] = np.array(matrix)
    glb_ok = set(glb_rows) == set(expected) and all(np.abs(glb_rows[c] - expected[c]).max() <= 1e-5 for c in expected)
    out['glb'] = {'ok': bool(glb_ok), 'components': sorted(glb_rows), 'max_pose_error': float(max([np.abs(glb_rows[c] - expected[c]).max() for c in expected if c in glb_rows] or [0])),
                  'note': 'Reimported as a scene; node names and transforms compared (glTF stores single precision).'}
    out['ok'] = out['step']['ok'] and out['glb']['ok']
    return out


def revision_check(spec, base, component_id, source, *, builds=None):
    """Swap one component for another revision at the pose stored in the assembly frame.

    No re-solve, best fit or registration is applied: a feature that moved shows up as a relation residual and,
    when it does, as interference. Both revisions are checked against every other component.
    """
    builds = builds or {}
    model = Assembly.model_validate(spec)
    spec = model.model_dump(mode='json')
    if component_id not in base['components']:
        raise ValueError(f'Unknown component {component_id!r}')
    original = next(c for c in spec['components'] if c['id'] == component_id)
    candidate = dict(original, source=source)
    Assembly.model_validate(dict(spec, components=[candidate if c['id'] == component_id else c for c in spec['components']]))
    before = {key: sha256(info['step_path']) for key, info in builds.items()}
    variables = spec['variables']
    parts = {c['id']: resolve_component(c, variables, builds) for c in spec['components']}
    revised = resolve_component(candidate, variables, builds)
    poses = {cid: solver.pose_from_matrix(row['matrix']) for cid, row in base['poses'].items()}
    volumes = lambda p: float(p['solid'].Volume()) if p['solid'] is not None else float(abs(p['mesh'].volume)) if p['mesh'].is_watertight else 0.0
    relations = []
    for relation in spec['relations']:
        row = dict(relation)
        if relation['type'] == 'clearance':
            row['min_value'] = evaluate(relation['min'], variables)
        relations.append(row)

    def evaluate_with(part):
        local = dict(parts, **{component_id: part})
        checked = {cid: {'solid': p['solid'], 'mesh': p['mesh'], 'volume': volumes(p)} for cid, p in local.items()}
        clash = interference.check(checked, poses, relations, spec['checks'])
        pairs = [p for p in clash['pairs'] if component_id in (p['a'], p['b'])]
        components = {cid: {'refs': p['refs'], 'symmetry': p['symmetry']} for cid, p in local.items()}
        problem = solver.Problem(components, [], poses, {}, 1.0)
        residuals = []
        for relation in spec['relations']:
            if relation['type'] in ('fixed', 'clearance') or component_id not in (relation['a'].split('.')[0], relation['b'].split('.')[0]):
                continue
            ends = [_ref_split(relation['a']), _ref_split(relation['b'])]
            missing = [f'{c}.{r}' for c, r in ends if r not in local[c]['refs']]
            if missing:
                residuals.append({'id': relation['id'], 'state': 'invalidated', 'reason': f'{", ".join(missing)} is not produced by this revision'})
                continue
            if relation['orientation'] == 'either':
                residuals.append({'id': relation['id'], 'state': 'not_evaluated', 'reason': "orientation 'either' has no single residual"})
                continue
            value = concrete_value(relation, variables)
            concrete_relation = {'id': relation['id'], 'type': relation['type'], 'a': ends[0], 'b': ends[1], 'value': value, 'orientation': relation['orientation']}
            lin, ang = problem.measure(concrete_relation, poses)
            ok = lin <= solver.LINEAR_TOL and ang <= solver.ANGULAR_TOL
            residuals.append({'id': relation['id'], 'state': 'satisfied' if ok else 'violated', 'residual_mm': lin, 'residual_deg': float(np.degrees(ang))})
        return {'revision': part['revision'], 'verdict': clash['verdict'], 'pairs': pairs, 'relations': residuals}

    a, b = evaluate_with(parts[component_id]), evaluate_with(revised)
    pen = lambda r: {(p['a'], p['b']) for p in r['pairs'] if p['kind'] == 'penetration'}
    after = {key: sha256(info['step_path']) for key, info in builds.items()}
    return {'ok': True, 'component': component_id, 'realigned': False,
            'pose_used': base['poses'][component_id], 'revision_a': a, 'revision_b': b,
            'new_penetrations': sorted(pen(b) - pen(a)), 'resolved_penetrations': sorted(pen(a) - pen(b)),
            'originals': {'builds_before': before, 'builds_after': after, 'unchanged': before == after},
            'note': 'Both revisions are placed at the pose stored in the assembly frame. No re-solve, best fit or registration '
                    'is applied, so moved features appear as relation residuals and interference is not hidden.',
            'limits': LIMITS}
