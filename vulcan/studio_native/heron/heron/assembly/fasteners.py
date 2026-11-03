"""Thread clearance findings: declared screw threads against the holes their shaft passes through.

The screw solid is a nominal cylinder; the declared thread (designation and pitch) is metadata. For every threaded
screw the check looks, at the solved poses, for hole axes (references carrying ``hole`` metadata: diameter and axial
extent) that are collinear with the screw axis and overlap the shaft span. The hole diameter is compared with the
ISO 273 clearance series of the designation. This is independent from geometric penetration (which stays in the
interference report): a hole can be too small for the declared thread and still be reported by interference only when
it actually cuts into the nominal cylinder.

States: ``too_small`` (hole below the fine clearance series, so the declared thread does not pass), ``tap_drill``
(hole within 0.1 mm of the tapping drill, nominal diameter minus pitch: it reads as a threaded hole, not a clearance
hole) and ``clearance_ok`` with the series reached (``fine``, ``medium``, ``coarse`` or ``loose``).
"""
from __future__ import annotations

import numpy as np

AXIS_TOL = 1e-4   # mm, collinearity of the two axes
ANGLE_TOL = 1e-6  # rad


def _series(diameter, thread):
    if diameter >= thread['clearance_coarse'] + 1e-9:
        return 'loose'
    if diameter >= thread['clearance_medium'] - 1e-9:
        return 'coarse' if diameter >= thread['clearance_coarse'] - 1e-9 else 'medium'
    return 'fine'


def check(parts, poses, component_rows):
    """parts: id -> resolved component (with 'refs' and the family 'thread'); poses: id -> (R, t)."""
    from .solver import world
    findings, screws = [], 0
    for sid, part in parts.items():
        thread = part.get('thread')
        if not thread:
            continue
        screws += 1
        axis_ref = part['refs']['axis']
        origin, direction = world(axis_ref, poses[sid])
        length = part['revision']['params_mm']['length']
        for hid, other in parts.items():
            if hid == sid:
                continue
            for name, ref in other['refs'].items():
                hole = ref.get('hole')
                if ref['kind'] != 'axis' or not hole:
                    continue
                point, d = world(ref, poses[hid])
                if abs(abs(d @ direction) - 1) > ANGLE_TOL * 10 or np.linalg.norm(np.cross(point - origin, direction)) > AXIS_TOL:
                    continue
                # axial spans along the screw direction (shaft runs from 0 to length along `direction`)
                shift = (point - origin) @ direction
                lo, hi = sorted((shift + (d @ direction) * hole['extent'][0], shift + (d @ direction) * hole['extent'][1]))
                overlap = min(hi, length) - max(lo, 0.0)
                if overlap <= 1e-6:
                    continue
                diameter = hole['diameter']
                base = {'screw': sid, 'thread': thread['designation'], 'pitch_mm': thread['pitch'], 'hole_component': hid, 'hole_reference': name,
                        'hole_diameter_mm': diameter, 'overlap_mm': float(overlap), 'minimum_clearance_mm': thread['clearance_fine'],
                        'iso273_mm': {'fine': thread['clearance_fine'], 'medium': thread['clearance_medium'], 'coarse': thread['clearance_coarse']}}
                tap = thread['nominal_d'] - thread['pitch']
                if diameter < thread['clearance_fine'] - 1e-9:
                    if abs(diameter - tap) <= 0.1 + 1e-9:
                        base.update(state='tap_drill', message=f"{hid}.{name} (Ø{diameter:g} mm) matches the tapping drill of {thread['designation']}x{thread['pitch']:g} "
                                    f"(Ø{tap:g} mm): it reads as a threaded hole, not a clearance hole.")
                    else:
                        base.update(state='too_small', message=f"Clearance hole too small for {thread['designation']}: {hid}.{name} is Ø{diameter:g} mm, "
                                    f"the fine clearance series needs at least Ø{thread['clearance_fine']:g} mm (nominal Ø{thread['nominal_d']:g} mm).")
                else:
                    series = _series(diameter, thread)
                    base.update(state='clearance_ok', series=series,
                                message=f"{hid}.{name} (Ø{diameter:g} mm) clears {thread['designation']} ({series} series).")
                findings.append(base)
    return {'checked_screws': screws, 'findings': findings,
            'note': 'Thread metadata is declared; the solid stays a nominal cylinder. Clearance is judged against the ISO 273 series, not against modelled threads, '
                    'and is separate from penetration of the nominal cylinder in the interference report.'}
