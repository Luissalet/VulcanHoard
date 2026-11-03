"""Read-only physical comparisons; sampled surface distances are not identity proofs."""
from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, Field

Units = Literal['auto', 'mm', 'cm', 'm', 'in']


class CompareArgs(BaseModel):
    model_a: int = Field(ge=1, description='Reference library model id.')
    model_b: int = Field(ge=1, description='Candidate library model id.')
    units_a: Units = Field('auto', description='File units when declared; otherwise auto assumes mm and reports that assumption.')
    units_b: Units = Field('auto', description='Explicit units override file metadata; no geometric size guessing.')
    alignment: Literal['none', 'rigid'] = Field('rigid', description='Optional estimated rotation and translation of B into A. Never scale or reflect.')
    tolerance_mm: float = Field(0.1, gt=0, allow_inf_nan=False)
    samples: int = Field(2048, ge=64, le=20000, description='Samples per direction: vertices, face centres and seeded area samples.')
    seed: int = Field(7, ge=0, le=2147483647)


def _sha(path):
    with path.open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def read_model(services, model_id, units='auto', expected_sha=None):
    """Read current indexed geometry in mm without repairing or changing its file."""
    import numpy as np
    import trimesh

    model = services.models.get(model_id)
    if model is None:
        raise ValueError('Modelo no encontrado: ' + str(model_id))
    path = Path(model['path'])
    if not path.is_file():
        raise ValueError('El archivo del modelo ya no está disponible: ' + path.name)
    sha = _sha(path)
    if sha != (expected_sha or model.get('sha256') or sha):
        raise ValueError('El archivo ha cambiado desde el índice o la comparación; vuelve a escanear y comparar: ' + path.name)
    mesh = trimesh.load(str(path), file_type=path.suffix.lstrip('.').lower(), force='mesh', process=False)
    if not hasattr(mesh, 'faces') or not len(mesh.faces) or not np.isfinite(mesh.vertices).all() or mesh.area <= 0:
        raise ValueError('El archivo no contiene una superficie triangular finita con área positiva: ' + path.name)
    mesh.merge_vertices()
    declared = mesh.units
    effective = (declared or 'mm') if units == 'auto' else units
    factor = float(trimesh.units.unit_conversion(effective, 'mm'))
    mesh.apply_scale(factor)
    if _sha(path) != sha:
        raise ValueError('El archivo cambió durante su lectura; vuelve a comparar: ' + path.name)
    source = {key: model[key] for key in ('id', 'name', 'path')}
    source.update(sha256=sha, file_units=declared, requested_units=units, effective_units=effective,
                  scale_to_mm=factor, units_assumed=units == 'auto' and not declared,
                  units_overridden=units != 'auto' and bool(declared) and effective != declared)
    return mesh, source


def _measure(mesh):
    import numpy as np
    closed = bool(mesh.is_watertight)
    winding = bool(mesh.is_winding_consistent)
    return {'triangles': int(len(mesh.faces)), 'vertices': int(len(mesh.vertices)),
            'bounds_mm': np.asarray(mesh.bounds).tolist(), 'extents_mm': np.asarray(mesh.extents).tolist(),
            'area_mm2': float(mesh.area), 'volume_mm3': abs(float(mesh.volume)) if closed and winding else None,
            'watertight': closed, 'winding_consistent': winding, 'bodies': int(mesh.body_count)}


def _sample(mesh, count, seed):
    import numpy as np
    import trimesh
    rng = np.random.default_rng(seed)
    vertices = mesh.vertices[rng.choice(len(mesh.vertices), min(len(mesh.vertices), count // 4), replace=False)]
    centres = mesh.triangles_center[rng.choice(len(mesh.faces), min(len(mesh.faces), count // 4), replace=False)]
    area, _ = trimesh.sample.sample_surface(mesh, count - len(vertices) - len(centres), seed=seed)
    return np.vstack((vertices, centres, area))


def _direction(points, target, tolerance):
    import numpy as np
    import trimesh
    # Keep proximity's temporary triangle arrays bounded while its spatial index is reused.
    results = [trimesh.proximity.closest_point(target, chunk) for chunk in np.array_split(points, max(1, (len(points) + 255) // 256))]
    closest, distances, faces = [np.concatenate([row[i] for row in results]) for i in range(3)]
    outside = distances > tolerance
    worst = np.argsort(-distances, kind='stable')[:8]
    locations = [{'point_mm': points[i].tolist(), 'closest_mm': closest[i].tolist(),
                  'distance_mm': float(distances[i]), 'target_face': int(faces[i])}
                 for i in worst if outside[i]]
    return {'samples': len(points), 'max_sampled_mm': float(distances.max()),
            'mean_mm': float(distances.mean()), 'rms_mm': float(np.sqrt(np.mean(distances ** 2))),
            'p95_mm': float(np.percentile(distances, 95)), 'outside_count': int(outside.sum()),
            'outside_fraction': float(outside.mean()), 'locations': locations,
            'location_selection': 'up to 8 most distant samples above tolerance'}


def _distances(a, b, pa, pb, tolerance):
    ab = _direction(pa, b, tolerance)
    ba = _direction(pb, a, tolerance)
    return {'a_to_b': ab, 'b_to_a': ba,
            'max_sampled_mm': max(ab['max_sampled_mm'], ba['max_sampled_mm']),
            'outside_count': ab['outside_count'] + ba['outside_count'],
            'within_sampled_tolerance': ab['outside_count'] + ba['outside_count'] == 0}


def compare_meshes(a, b, args: CompareArgs):
    """Meshes are in mm. Estimate only a proper rigid transform, keeping raw results."""
    import numpy as np
    import trimesh

    pa, pb = _sample(a, args.samples, args.seed), _sample(b, args.samples, args.seed + 1)
    raw = _distances(a, b, pa, pb, args.tolerance_mm)
    transform = np.eye(4)
    aligned = b.copy()
    registration_cost = None
    outcome = 'not_requested'
    if args.alignment == 'rigid':
        outcome = 'estimated_rigid'
        transform, registration_cost = trimesh.registration.mesh_other(
            b, a, samples=min(args.samples, 2048), scale=False, reflection=False,
            seed=args.seed, icp_first=15, icp_final=50)
        rotation = transform[:3, :3]
        if not np.isfinite(transform).all() or not np.allclose(rotation.T @ rotation, np.eye(3), atol=1e-6) or not np.isclose(np.linalg.det(rotation), 1, atol=1e-6):
            raise ValueError('No se pudo obtener una transformación rígida propia, sin escala ni espejo.')
        aligned.apply_transform(transform)
        transformed_points = trimesh.transform_points(pb, transform)
        fitted = _distances(a, aligned, pa, transformed_points, args.tolerance_mm)
        # ICP is local. An already better placement must never be replaced by a worse fit.
        raw_rms = raw['a_to_b']['rms_mm'] ** 2 + raw['b_to_a']['rms_mm'] ** 2
        fitted_rms = fitted['a_to_b']['rms_mm'] ** 2 + fitted['b_to_a']['rms_mm'] ** 2
        if fitted_rms > raw_rms + 1e-12:
            transform, aligned, fitted = np.eye(4), b.copy(), raw
            registration_cost = None
            outcome = 'kept_original_better_fit'
    else:
        fitted = raw
    angle = float(np.degrees(np.arccos(np.clip((np.trace(transform[:3, :3]) - 1) / 2, -1, 1))))
    return {'unit': 'mm', 'tolerance_mm': args.tolerance_mm, 'samples_per_direction': args.samples,
            'seed': args.seed, 'sampling': 'vertices + face centres + seeded surface-area samples',
            'reference': _measure(a), 'candidate': _measure(b), 'candidate_aligned': _measure(aligned),
            'raw': raw, 'aligned': fitted,
            'alignment': {'requested': args.alignment, 'matrix_b_to_a': transform.tolist(),
                          'rotation_degrees': angle, 'translation_mm': transform[:3, 3].tolist(),
                          'estimated': args.alignment == 'rigid', 'outcome': outcome, 'registration_cost': registration_cost,
                          'scale_allowed': False, 'reflection_allowed': False},
            'limits': ['Las distancias y la fracción fuera de tolerancia describen las muestras, no toda la superficie.',
                       'No encontrar diferencias no demuestra identidad ni una distancia máxima universal.',
                       'El registro rígido es una estimación local; las simetrías pueden admitir varias colocaciones.',
                       'Estanqueidad y volumen no certifican ausencia de autointersecciones, imprimibilidad ni tolerancias de fabricación.']}


def compare_models(services, args: CompareArgs):
    a, source_a = read_model(services, args.model_a, args.units_a)
    b, source_b = read_model(services, args.model_b, args.units_b)
    result = compare_meshes(a, b, args)
    result.update(source_a=source_a, source_b=source_b)
    return result
