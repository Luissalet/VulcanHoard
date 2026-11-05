"""Geometric constraint solver for 2D sketches.

Unknowns are the point coordinates and circle radii. Every constraint contributes residual rows (mm for lengths,
radians for directions). The solve accepts constraints in declaration order: when they cannot all hold, the first
one that cannot be added is reported as failing with the accepted constraints it conflicts with, as in assemblies.
At the solution the Jacobian rank gives the remaining degrees of freedom, which points can still move, and which
constraints are implied by earlier ones (redundant but consistent).
"""
from __future__ import annotations

import ast
import math

import numpy as np

from .spec import CONSTRAINTS, chain, entity_kinds, value

LIN_TOL = 1e-6  # mm
ANG_TOL = 1e-8  # rad
EPS = 1e-12


def _wrap(a):
    return (a + math.pi) % (2 * math.pi) - math.pi


def names_in(raw):
    """Parameter names used by a value (number or expression)."""
    if not isinstance(raw, str):
        return []
    try:
        tree = ast.parse(raw, mode='eval')
    except SyntaxError:
        return []
    return sorted({n.id for n in ast.walk(tree) if isinstance(n, ast.Name)})


class System:
    def __init__(self, sketch, values, start=None):
        self.sketch, self.values = sketch, values
        self.points = list(sketch.points)
        self.circles = list(sketch.circles) + list(sketch.arcs)  # arcs carry a radius unknown like circles
        self.pi = {p: 2 * i for i, p in enumerate(self.points)}
        self.ri = {c: 2 * len(self.points) + i for i, c in enumerate(self.circles)}
        self.n = 2 * len(self.points) + len(self.circles)
        x = np.zeros(self.n)
        start = start or {}
        self.given = {p: np.array([value(u, values, f'point {p}'), value(v, values, f'point {p}')]) for p, (u, v) in sketch.points.items()}
        for p in sketch.points:
            given = (start.get('points') or {}).get(p)
            x[self.pi[p]:self.pi[p] + 2] = given if given is not None else self.given[p]
        for c, circle in sketch.circles.items():
            given = (start.get('radii') or {}).get(c)
            x[self.ri[c]] = given if given is not None else value(circle.radius, values, f'circle {c}')
            if x[self.ri[c]] <= 0:
                raise ValueError(f'circle {c}: the starting radius must be positive')
        for a, arc in sketch.arcs.items():
            given = (start.get('radii') or {}).get(a)
            ends = [np.linalg.norm(x[self.pi[p]:self.pi[p] + 2] - x[self.pi[arc.center]:self.pi[arc.center] + 2]) for p in (arc.start, arc.end)]
            x[self.ri[a]] = given if given is not None else float(np.mean(ends))
            if x[self.ri[a]] <= 0:
                raise ValueError(f'arc {a}: its start point sits on its centre')
        self.x0 = x
        self.implicit = [self._arc_row(a) for a in sketch.arcs]  # always held: both arc ends at the arc radius
        self.rows = [self._compile(c) for c in sketch.constraints]

    def _arc_row(self, a):
        arc = self.sketch.arcs[a]

        def f(x):
            C, r = self.P(x, arc.center), self.R(x, a)
            return [float(np.linalg.norm(self.P(x, arc.start) - C)) - r, float(np.linalg.norm(self.P(x, arc.end) - C)) - r], ['mm', 'mm']
        deps = sorted({self.pi[p] + k for p in (arc.center, arc.start, arc.end) for k in (0, 1)} | {self.ri[a]})
        return {'constraint': None, 'arc': a, 'f': f, 'deps': deps, 'units': ['mm', 'mm'], 'target': None}

    # -- geometry helpers ------------------------------------------------------------------------------
    def P(self, x, p):
        i = self.pi[p]
        return x[i:i + 2]

    def R(self, x, c):
        return x[self.ri[c]]

    def center_of(self, c):
        return (self.sketch.circles.get(c) or self.sketch.arcs[c]).center

    def C(self, x, c):
        return self.P(x, self.center_of(c))

    def line(self, x, lid):
        a, b = self.sketch.lines[lid]
        A, B = self.P(x, a), self.P(x, b)
        d = B - A
        L = math.hypot(d[0], d[1])
        return A, B, d / (L + EPS), L

    def deps(self, c):
        out = []
        for field, (name, kind) in entity_kinds(self.sketch, c).items():
            if name is None:
                continue
            if kind == 'point':
                out += [self.pi[name], self.pi[name] + 1]
            elif kind == 'line':
                for p in self.sketch.lines[name]:
                    out += [self.pi[p], self.pi[p] + 1]
            else:
                center = self.center_of(name)
                out += [self.pi[center], self.pi[center] + 1, self.ri[name]]
        return sorted(set(out))

    # -- constraints -----------------------------------------------------------------------------------
    def _compile(self, c):
        t, v = c.type, None
        if c.value is not None:
            v = value(c.value, self.values, f'constraint {c.id}')
            if t in ('distance', 'length', 'radius', 'diameter') and (v <= 0 if t != 'distance' else v < 0):
                raise ValueError(f'constraint {c.id}: {t} must be positive, got {v:g}')
            if t == 'angle':
                if abs(v) > 360:
                    raise ValueError(f'constraint {c.id}: angle must be within [-360, 360] degrees')
                v = math.radians(v)
        at = None
        if c.at is not None:
            at = np.array([value(c.at[0], self.values, f'constraint {c.id}'), value(c.at[1], self.values, f'constraint {c.id}')])
        cross = lambda u, w: u[0] * w[1] - u[1] * w[0]  # noqa: E731

        def f(x):
            if t == 'fixed':
                target = at if at is not None else self.given[c.point]
                return list(self.P(x, c.point) - target), ['mm', 'mm']
            if t == 'coincident':
                return list(self.P(x, c.a) - self.P(x, c.b)), ['mm', 'mm']
            if t in ('horizontal', 'vertical'):
                A, B, _, _ = self.line(x, c.line)
                return [B[1] - A[1] if t == 'horizontal' else B[0] - A[0]], ['mm']
            if t in ('parallel', 'perpendicular', 'angle'):
                _, _, ua, _ = self.line(x, c.a)
                _, _, ub, _ = self.line(x, c.b)
                if t == 'parallel':
                    return [cross(ua, ub)], ['rad']
                if t == 'perpendicular':
                    return [float(np.dot(ua, ub))], ['rad']
                return [_wrap(math.atan2(cross(ua, ub), float(np.dot(ua, ub))) - v)], ['rad']
            if t == 'tangent':
                if c.a in self.sketch.lines:
                    A, _, u, _ = self.line(x, c.a)
                    return [abs(cross(self.C(x, c.b) - A, u)) - self.R(x, c.b)], ['mm']
                gap = float(np.linalg.norm(self.C(x, c.a) - self.C(x, c.b)))
                if c.mode == 'internal':
                    return [gap - abs(self.R(x, c.a) - self.R(x, c.b))], ['mm']
                return [gap - (self.R(x, c.a) + self.R(x, c.b))], ['mm']
            if t == 'equal':
                if c.a in self.sketch.lines:
                    return [self.line(x, c.a)[3] - self.line(x, c.b)[3]], ['mm']
                return [self.R(x, c.a) - self.R(x, c.b)], ['mm']
            if t == 'symmetric':
                A, _, u, _ = self.line(x, c.axis)
                pa, pb = self.P(x, c.a), self.P(x, c.b)
                return [cross((pa + pb) / 2 - A, u), float(np.dot(pb - pa, u))], ['mm', 'mm']
            if t == 'midpoint':
                A, B, _, _ = self.line(x, c.line)
                return list(self.P(x, c.point) - (A + B) / 2), ['mm', 'mm']
            if t == 'point_on_line':
                A, _, u, _ = self.line(x, c.line)
                return [cross(self.P(x, c.point) - A, u)], ['mm']
            if t == 'point_on_circle':
                return [float(np.linalg.norm(self.P(x, c.point) - self.C(x, c.circle))) - self.R(x, c.circle)], ['mm']
            if t == 'concentric':
                return list(self.C(x, c.a) - self.C(x, c.b)), ['mm', 'mm']
            if t == 'distance':
                if c.b in self.sketch.lines:
                    A, _, u, _ = self.line(x, c.b)
                    return [abs(cross(self.P(x, c.a) - A, u)) - v], ['mm']
                return [float(np.linalg.norm(self.P(x, c.a) - self.P(x, c.b))) - v], ['mm']
            if t == 'distance_x':
                return [self.P(x, c.b)[0] - self.P(x, c.a)[0] - v], ['mm']
            if t == 'distance_y':
                return [self.P(x, c.b)[1] - self.P(x, c.a)[1] - v], ['mm']
            if t == 'length':
                return [self.line(x, c.line)[3] - v], ['mm']
            if t == 'radius':
                return [self.R(x, c.circle) - v], ['mm']
            if t == 'diameter':
                return [2 * self.R(x, c.circle) - v], ['mm']
            raise ValueError(f'Unknown constraint type {t}')

        units = f(self.x0)[1]
        return {'constraint': c, 'f': f, 'deps': self.deps(c), 'units': units, 'target': v}

    # -- numerics --------------------------------------------------------------------------------------
    def scale(self):
        pts = np.array([self.x0[self.pi[p]:self.pi[p] + 2] for p in self.points]) if self.points else np.zeros((1, 2))
        return max(1.0, float(np.ptp(pts, axis=0).max()))

    def residual(self, x, rows, weighted=True):
        out = []
        S = self.scale() if weighted else 1.0
        for row in rows:
            values, units = row['f'](x)
            out += [val * (S if u == 'rad' else 1.0) for val, u in zip(values, units)]
        return np.array(out, dtype=float)

    def jacobian(self, x, rows, weighted=True):
        """Central differences, only over the unknowns each constraint reads."""
        S = self.scale() if weighted else 1.0
        blocks = []
        for row in rows:
            k = len(row['units'])
            J = np.zeros((k, self.n))
            w = np.array([S if u == 'rad' else 1.0 for u in row['units']])
            for j in row['deps']:
                h = 1e-7 * max(1.0, abs(x[j]))
                xp, xm = x.copy(), x.copy()
                xp[j] += h
                xm[j] -= h
                J[:, j] = (np.array(row['f'](xp)[0]) - np.array(row['f'](xm)[0])) / (2 * h) * w
            blocks.append(J)
        return np.vstack(blocks) if blocks else np.zeros((0, self.n))

    def satisfied(self, x, rows):
        for row in rows:
            values, units = row['f'](x)
            for val, u in zip(values, units):
                if abs(val) > (ANG_TOL * 10 if u == 'rad' else LIN_TOL):
                    return False
        return True

    def solve(self, rows, x_start):
        """Least squares from x_start, then minimum-norm Gauss-Newton polishing (redundancy is harmless). Arc rows always hold."""
        rows = self.implicit + list(rows)
        if not rows:
            return x_start.copy(), True
        from scipy.optimize import least_squares
        x = x_start.copy()
        with np.errstate(all='ignore'):
            try:
                fit = least_squares(lambda z: self.residual(z, rows), x, jac=lambda z: self.jacobian(z, rows), method='trf',
                                    xtol=1e-14, ftol=1e-14, gtol=1e-14, max_nfev=400)
                x = fit.x
            except (ValueError, np.linalg.LinAlgError):
                pass
            for _ in range(12):
                r = self.residual(x, rows)
                if self.satisfied(x, rows):
                    break
                J = self.jacobian(x, rows)
                step = np.linalg.lstsq(J, -r, rcond=None)[0]
                if not np.all(np.isfinite(step)):
                    break
                x = x + step
        return x, self.satisfied(x, rows)

    def rank(self, J):
        if J.size == 0:
            return 0
        s = np.linalg.svd(J, compute_uv=False)
        return int(np.sum(s > 1e-7 * max(1.0, s[0])))

    def measure(self, x, row):
        values, units = row['f'](x)
        mm = [abs(v) for v, u in zip(values, units) if u == 'mm']
        rad = [abs(v) for v, u in zip(values, units) if u == 'rad']
        out = {}
        if mm:
            out['residual_mm'] = float(max(mm))
        if rad:
            out['residual_deg'] = float(math.degrees(max(rad)))
        return out


def solve(sketch, values, start=None):
    """Solve a sketch; returns status, solved geometry, per-constraint residuals, DOF, free entities, conflicts, redundancy."""
    system = System(sketch, values, start)
    rows = system.rows
    x, ok = system.solve(rows, system.x0)
    failing, accepted = [], rows
    if not ok:
        accepted, x = [], system.x0.copy()
        for row in rows:
            x_try, good = system.solve(accepted + [row], x)
            if good:
                accepted, x = accepted + [row], x_try
            else:
                failing.append(row)
    conflicts = []
    for row in failing[:5]:
        involved = []
        for other in accepted:
            rest = [r for r in accepted if r is not other] + [row]
            if system.solve(rest, x)[1]:
                involved.append(other['constraint'].id)
        conflicts.append({'failing_constraint': row['constraint'].id, 'type': row['constraint'].type, **system.measure(x, row),
                          'conflicts_with': involved,
                          'note': 'Constraints are accepted in declaration order; this one cannot hold together with the accepted ones listed.'})
    # rank, redundancy and free motions at the solution
    J = system.jacobian(x, system.implicit + accepted)
    rank = system.rank(J)
    dof = system.n - rank
    stacked = system.jacobian(x, system.implicit) if system.implicit else np.zeros((0, system.n))
    redundant, current = [], system.rank(stacked)
    for row in accepted:
        k = len(row['units'])
        stacked = np.vstack([stacked, system.jacobian(x, [row])])
        new = system.rank(stacked)
        if new - current < k:
            redundant.append({'id': row['constraint'].id, 'type': row['constraint'].type, 'equations': k, 'implied_equations': k - (new - current)})
        current = new
    free = []
    if dof:
        _, s, vt = np.linalg.svd(J if J.size else np.zeros((1, system.n)))
        null = vt[rank:].T if J.size else np.eye(system.n)
        for p in system.points:
            i = system.pi[p]
            sub = null[i:i + 2, :]
            sv = np.linalg.svd(sub, compute_uv=False) if sub.size else np.zeros(0)
            k = int(np.sum(sv > 1e-6))
            if k:
                entry = {'entity': p, 'kind': 'point', 'freedom': k}
                if k == 1:
                    u, _, _ = np.linalg.svd(sub)
                    d = u[:, 0]
                    entry['direction'] = [round(float(d[0]), 6), round(float(d[1]), 6)]
                free.append(entry)
        for c in system.circles:
            if np.linalg.norm(null[system.ri[c], :]) > 1e-6:
                arc = c in sketch.arcs
                free.append({'entity': c, 'kind': 'arc' if arc else 'circle', 'freedom': 1,
                             'note': 'arc radius not fixed by its end points or a dimension' if arc else 'radius not dimensioned'})
    failing_ids = {r['constraint'].id for r in failing}
    redundant_ids = {r['id'] for r in redundant}
    report = []
    for row in rows:
        c = row['constraint']
        entry = {'id': c.id, 'type': c.type, 'state': 'violated' if c.id in failing_ids else 'satisfied', **system.measure(x, row)}
        if c.value is not None:
            entry['value'] = round(math.degrees(row['target']), 9) if c.type == 'angle' else round(row['target'], 9)
            entry['parameters'] = names_in(c.value)
        if c.id in redundant_ids:
            entry['redundant'] = True
        report.append(entry)
    points = {p: [float(x[system.pi[p]]), float(x[system.pi[p] + 1])] for p in system.points}
    radii = {c: float(x[system.ri[c]]) for c in system.circles}
    geometry = check_profile(sketch, points, radii)
    status = 'incompatible' if failing else 'invalid_geometry' if geometry['errors'] else 'under_constrained' if dof else 'solved'
    return {'status': status, 'dof': int(dof), 'unknowns': system.n, 'equations': int(sum(len(r['units']) for r in system.implicit + rows)),
            'points': points, 'radii': radii, 'constraints': report, 'free': free, 'conflicts': conflicts,
            'redundant_but_consistent': redundant, 'geometry': geometry}


def _area(poly):
    return 0.5 * sum(poly[i][0] * poly[(i + 1) % len(poly)][1] - poly[(i + 1) % len(poly)][0] * poly[i][1] for i in range(len(poly)))


def _cross_segments(p1, p2, q1, q2):
    def orient(a, b, c):
        return (b[0] - a[0]) * (c[1] - a[1]) - (b[1] - a[1]) * (c[0] - a[0])
    d1, d2, d3, d4 = orient(q1, q2, p1), orient(q1, q2, p2), orient(p1, p2, q1), orient(p1, p2, q2)
    return (d1 * d2 < -1e-12) and (d3 * d4 < -1e-12)


def _inside(pt, poly):
    x, y, inside = pt[0], pt[1], False
    for i in range(len(poly)):
        (x1, y1), (x2, y2) = poly[i], poly[(i + 1) % len(poly)]
        if (y1 > y) != (y2 > y) and x < (x2 - x1) * (y - y1) / (y2 - y1) + x1:
            inside = not inside
    return inside


def _segment_distance(p, a, b):
    a, b, p = np.asarray(a), np.asarray(b), np.asarray(p)
    d = b - a
    t = 0.0 if not d.any() else max(0.0, min(1.0, float(np.dot(p - a, d) / np.dot(d, d))))
    return float(np.linalg.norm(p - (a + t * d)))


def arc_geometry(sketch, aid, points):
    """Centre, radius (from the start point), start angle, signed sweep (counter-clockwise positive) and mid point of a solved arc."""
    arc = sketch.arcs[aid]
    c, s, e = (np.asarray(points[p], dtype=float) for p in (arc.center, arc.start, arc.end))
    r = float(np.linalg.norm(s - c))
    a0, a1 = math.atan2(s[1] - c[1], s[0] - c[0]), math.atan2(e[1] - c[1], e[0] - c[0])
    sweep = -((a0 - a1) % (2 * math.pi)) if arc.clockwise else (a1 - a0) % (2 * math.pi)
    mid = c + r * np.array([math.cos(a0 + sweep / 2), math.sin(a0 + sweep / 2)])
    return {'center': c, 'radius': r, 'start_angle': a0, 'sweep': sweep, 'mid': [float(mid[0]), float(mid[1])]}


def edge_mid(sketch, edge, points):
    if edge in sketch.lines:
        p, q = sketch.lines[edge]
        return [(points[p][0] + points[q][0]) / 2, (points[p][1] + points[q][1]) / 2]
    return arc_geometry(sketch, edge, points)['mid']


def polygon(sketch, loop, points, per_turn=32):
    """A closed loop as a polygon: line starts, plus intermediate points along every arc."""
    out = []
    for edge in loop:
        if edge in sketch.lines:
            out.append(list(points[sketch.lines[edge][0]]))
            continue
        g = arc_geometry(sketch, edge, points)
        n = max(4, int(math.ceil(abs(g['sweep']) / (2 * math.pi) * per_turn)))
        out += [[float(g['center'][0] + g['radius'] * math.cos(g['start_angle'] + g['sweep'] * k / n)),
                 float(g['center'][1] + g['radius'] * math.sin(g['start_angle'] + g['sweep'] * k / n))] for k in range(n)]
    return out


def loop_area(sketch, loop, points):
    """Exact signed area of a loop: the chord polygon plus the circular segment of every arc."""
    starts = [points[p] for p in chain(sketch, loop)]
    area = _area(starts)
    for edge in loop:
        if edge in sketch.arcs:
            g = arc_geometry(sketch, edge, points)
            area += 0.5 * g['radius'] ** 2 * (g['sweep'] - math.sin(g['sweep']))
    return area


def loops(sketch, points):
    outer = polygon(sketch, sketch.profile, points)
    inner = [polygon(sketch, h, points) for h in sketch.holes if isinstance(h, list)]
    return outer, inner


def check_profile(sketch, points, radii):
    """The solved outline must be a simple closed polygon; inner loops and circles must lie inside it without crossing."""
    errors = []
    for aid in sketch.arcs:
        g = arc_geometry(sketch, aid, points)
        if g['radius'] < 1e-6 or abs(g['sweep']) < 1e-9:
            errors.append(f'arc {aid}: the solved arc has no radius or no sweep (its end points coincide)')
    if errors:
        return {'valid': False, 'errors': errors, 'area_mm2': 0.0, 'counter_clockwise': True}
    outer, inner = loops(sketch, points)
    rings = [('profile', outer)] + [(f'holes[{i}]', ring) for i, ring in enumerate(inner)]
    for name, ring in rings:
        if abs(_area(ring)) < 1e-6:
            errors.append(f'{name}: the solved loop encloses no area')
            continue
        n = len(ring)
        for i in range(n):
            for j in range(i + 1, n):
                if abs(i - j) in (1, n - 1):
                    continue
                if _cross_segments(ring[i], ring[(i + 1) % n], ring[j], ring[(j + 1) % n]):
                    errors.append(f'{name}: the solved outline crosses itself (edges {i} and {j}); a constraint flipped the shape')
                    break
            else:
                continue
            break
    edges = [(outer[i], outer[(i + 1) % len(outer)]) for i in range(len(outer))]
    for i, ring in enumerate(inner):
        if not all(_inside(p, outer) for p in ring):
            errors.append(f'holes[{i}]: the inner loop is not inside the outline')
    for i, hole in enumerate(sketch.holes):
        if isinstance(hole, str):
            center = points[sketch.circles[hole].center]
            r = radii[hole]
            if not _inside(center, outer) or min(_segment_distance(center, a, b) for a, b in edges) <= r + 1e-9:
                errors.append(f'hole circle {hole}: it must lie inside the outline without touching it')
    signed = loop_area(sketch, sketch.profile, points)
    area = abs(signed) - sum(abs(loop_area(sketch, h, points)) for h in sketch.holes if isinstance(h, list)) \
        - sum(math.pi * radii[h] ** 2 for h in sketch.holes if isinstance(h, str))
    return {'valid': not errors, 'errors': errors, 'area_mm2': float(area), 'counter_clockwise': signed > 0}


def dry_run(spec):
    """Validate a part spec and solve only its sketch (no kernel, nothing stored)."""
    from .spec import Part, resolve
    part = Part.model_validate(spec)
    values = resolve(part.parameters)
    result = solve(part.sketch, values)
    result['parameters'] = values
    return result


def drag(spec, point, to):
    """Pull one sketch point towards `to` while every constraint keeps holding (the point stops where the constraints let it).

    The constraints are solved with a weak extra pull on the dragged point, then polished with the constraints alone,
    so a fully determined point does not move and a sliding point ends at the nearest position it can reach. The
    returned spec has the new starting positions of the points that moved; nothing is stored."""
    import copy
    from scipy.optimize import least_squares
    from .spec import Part, resolve
    part = Part.model_validate(spec)
    if point not in part.sketch.points:
        raise ValueError(f'Unknown sketch point {point!r}')
    target = np.array([float(to[0]), float(to[1])])
    if not np.all(np.isfinite(target)) or np.max(np.abs(target)) > 1e6:
        raise ValueError('to must be two finite coordinates in mm')
    values = resolve(part.parameters)
    before = solve(part.sketch, values)
    if before['status'] == 'incompatible':
        raise ValueError('The sketch is incompatible; fix the failing constraint before dragging')
    system = System(part.sketch, values, {'points': before['points'], 'radii': before['radii']})
    rows = system.implicit + system.rows
    i = system.pi[point]
    weight = 1e-3
    x0 = system.x0.copy()
    with np.errstate(all='ignore'):
        fit = least_squares(lambda z: np.concatenate([system.residual(z, rows), weight * (z[i:i + 2] - target)]), x0, method='trf',
                            xtol=1e-12, ftol=1e-12, gtol=1e-12, max_nfev=300)
    x, ok = system.solve(system.rows, fit.x)
    if not ok:
        x = x0
    points = {p: [float(x[system.pi[p]]), float(x[system.pi[p] + 1])] for p in system.points}
    radii = {c: float(x[system.ri[c]]) for c in system.circles}
    out = copy.deepcopy(spec)
    moved = []
    for p, xy in points.items():
        if float(np.linalg.norm(np.array(xy) - np.array(before['points'][p]))) > 1e-9:
            out['sketch']['points'][p] = [round(xy[0], 9), round(xy[1], 9)]
            moved.append(p)
    for c, circle in (out['sketch'].get('circles') or {}).items():
        if abs(radii[c] - before['radii'][c]) > 1e-9:
            circle['radius'] = round(radii[c], 9)
    after = solve(Part.model_validate(out).sketch, values, {'points': points, 'radii': radii})
    reached = float(np.linalg.norm(np.array(after['points'][point]) - target))
    shift = float(np.linalg.norm(np.array(after['points'][point]) - np.array(before['points'][point])))
    note = ('the point is fixed by its constraints and did not move' if shift < 1e-6 else
            'the point reached the requested position' if reached < 1e-4 else
            'the point moved as far as its constraints allow (nearest reachable position)')
    return {'spec': out, 'point': point, 'requested': [float(target[0]), float(target[1])], 'position': after['points'][point],
            'moved_mm': shift, 'distance_to_request_mm': reached, 'moved_points': moved, 'note': note, 'solve': after}
