"""Deterministic rigid-body solve for explicit relations between semantic references.

Unknowns are one rotation vector and one translation per non-fixed component. Every relation contributes
exact residuals (orientation of normals/axes and positional offset). The result is classified as:

* ``solved``: all relations hold and no motion is left except rotations about declared symmetry axes;
* ``under_constrained``: relations hold, the remaining motions are listed per component (null space of the
  relation Jacobian), and those components keep their seeded or previous pose — that is not a chosen solution;
* ``incompatible``: no pose satisfies all relations. Relations are added in declaration order; the first one
  that cannot be satisfied is the failing relation and its conflict set lists the accepted relations whose
  removal would make it satisfiable. Poses are those of the accepted relations;
* ``ambiguous``: an ``orientation: either`` relation is satisfiable both ways. Nothing is chosen: the
  candidates are returned and the poses stay at their initial values.

Relation types: ``coincident``/``distance`` (planes), ``coaxial`` (axes), ``angle`` (angle in degrees between two
reference directions: plane normals or axis directions as listed), and point relations ``point_on_plane``
(``a`` point, ``b`` plane, optional signed offset along the plane normal), ``point_on_axis`` and
``point_coincident``. Residuals and their Jacobian are analytic; the Jacobian of the free-motion analysis
(``jacobian``) stays numeric on purpose, as an independent check.
"""
from __future__ import annotations

import itertools

import numpy as np
from scipy.optimize import least_squares
from scipy.spatial.transform import Rotation

LINEAR_TOL = 1e-5   # mm
ANGULAR_TOL = 1e-6  # rad
STARTS = [Rotation.from_euler(axis, angle, degrees=True) for axis, angle in
          (('x', 180), ('y', 180), ('z', 180), ('x', 90), ('x', -90), ('y', 90), ('y', -90), ('z', 90), ('z', -90))]


def _skew(v):
    return np.array(((0.0, -v[2], v[1]), (v[2], 0.0, -v[0]), (-v[1], v[0], 0.0)))


_E = [_skew(np.eye(3)[i]) for i in range(3)]


def world(ref, pose):
    R, t = pose
    return R @ np.asarray(ref['origin']) + t, R @ np.asarray(ref['direction'])


def _rotation_between(u, v):
    u, v = u / np.linalg.norm(u), v / np.linalg.norm(v)
    c = float(np.clip(u @ v, -1, 1))
    if c > 1 - 1e-12:
        return np.eye(3)
    if c < -1 + 1e-12:
        helper = np.array((1.0, 0, 0)) if abs(u[0]) < 0.9 else np.array((0, 1.0, 0))
        axis = np.cross(u, helper)
        return Rotation.from_rotvec(axis / np.linalg.norm(axis) * np.pi).as_matrix()
    axis = np.cross(u, v)
    return Rotation.from_rotvec(axis / np.linalg.norm(axis) * np.arccos(c)).as_matrix()


def _rot(w):
    theta = float(np.sqrt(w @ w))
    K = _skew(w)
    if theta < 1e-6:
        return np.eye(3) + K + 0.5 * K @ K
    Kn = K / theta
    return np.eye(3) + np.sin(theta) * Kn + (1 - np.cos(theta)) * Kn @ Kn


def _skew_batch(v):
    out = np.zeros(v.shape[:-1] + (3, 3))
    out[..., 0, 1], out[..., 0, 2] = -v[..., 2], v[..., 1]
    out[..., 1, 0], out[..., 1, 2] = v[..., 2], -v[..., 0]
    out[..., 2, 0], out[..., 2, 1] = -v[..., 1], v[..., 0]
    return out


def rotations(w, derivatives=False):
    """Rotation matrices of rotation vectors w (n, 3); optionally dR/dw_i for i = 0..2 as (n, 3, 3, 3), exact.

    dR/dw_i = [J_l(w) e_i]x R with the left Jacobian of SO(3) J_l = I + b K + c K^2 (K = [w]x)."""
    theta = np.linalg.norm(w, axis=1)
    K = _skew_batch(w)
    KK = K @ K
    a = np.sinc(theta / np.pi)                      # sin(t) / t
    b = 0.5 * np.sinc(theta / (2 * np.pi)) ** 2     # (1 - cos t) / t^2
    R = np.eye(3) + a[:, None, None] * K + b[:, None, None] * KK
    if not derivatives:
        return R
    small = theta < 1e-2
    safe = np.where(small, 1.0, theta)
    c = np.where(small, 1 / 6 - theta ** 2 / 120 + theta ** 4 / 5040, (theta - np.sin(theta)) / safe ** 3)
    Jl = np.eye(3) + b[:, None, None] * K + c[:, None, None] * KK
    return R, np.stack([_skew_batch(Jl[:, :, i]) @ R for i in range(3)], axis=1)


class Compiled:
    """Relations of one solve as arrays grouped by type, so residuals and the exact Jacobian are a handful of vectorised operations."""

    ROWS = {'coincident': 4, 'distance': 4, 'coaxial': 6, 'angle_vec': 3, 'angle': 1, 'point_on_plane': 1, 'point_on_axis': 3, 'point_coincident': 3}

    def __init__(self, problem, relations):
        self.problem = problem
        free = problem.free
        names = list(free) + sorted({c for r in relations for c in (r['a'][0], r['b'][0]) if c not in problem.index})
        position = {c: i for i, c in enumerate(names)}
        self.nfree = len(free)
        self.fixed_R = np.array([problem.fixed[c][0] for c in names[len(free):]]).reshape(-1, 3, 3)
        self.fixed_t = np.array([problem.fixed[c][1] for c in names[len(free):]]).reshape(-1, 3)
        n = len(relations)
        self.ia = np.array([position[r['a'][0]] for r in relations], int)
        self.ib = np.array([position[r['b'][0]] for r in relations], int)
        arrays = [problem.arrays(*r['a']) + problem.arrays(*r['b']) for r in relations]
        self.oa, self.da, self.ob, self.db = (np.array([a[k] for a in arrays]).reshape(n, 3) for k in range(4))
        self.value = np.array([r['value'] for r in relations], float)
        self.sign = np.array([(1.0 if r['orientation'] == 'opposed' else -1.0) for r in relations])
        kinds = ['angle_vec' if is_vector_angle(r) else r['type'] for r in relations]
        for k, r in enumerate(relations):
            if kinds[k] == 'angle_vec':
                self.sign[k] = -1.0 if abs(r['value']) < 1e-9 else 1.0
        self.groups = {}
        rows = np.array([self.ROWS[k] for k in kinds], int)
        self.offset = np.concatenate([[0], np.cumsum(rows)])[:-1]
        self.total = int(rows.sum())
        for kind in self.ROWS:
            where = np.array([k for k, v in enumerate(kinds) if v == kind], int)
            if len(where):
                span = self.offset[where][:, None] + np.arange(self.ROWS[kind])[None, :]
                self.groups[kind] = (where, span)

    def frames(self, x, derivatives=False):
        w = np.asarray(x, float).reshape(-1, 6)
        if derivatives:
            R, dR = rotations(w[:, :3], True)
        else:
            R, dR = rotations(w[:, :3]), None
        return np.concatenate([R, self.fixed_R]), np.concatenate([w[:, 3:], self.fixed_t]), dR

    def evaluate(self, R, t, derivatives=False):
        """Residual vector (orientation rows scaled, then position rows, per relation) and optionally per-group derivatives."""
        sc, I3 = self.problem.scale, np.eye(3)
        Ra, Rb = R[self.ia], R[self.ib]
        Pa = np.einsum('nij,nj->ni', Ra, self.oa) + t[self.ia]
        Da = np.einsum('nij,nj->ni', Ra, self.da)
        Pb = np.einsum('nij,nj->ni', Rb, self.ob) + t[self.ib]
        Db = np.einsum('nij,nj->ni', Rb, self.db)
        out = np.zeros(self.total)
        derived = {}
        for kind, (where, span) in self.groups.items():
            pa, da, pb, db = Pa[where], Da[where], Pb[where], Db[where]
            s, v = self.sign[where], self.value[where]
            m = len(where)
            zero = np.zeros((m, self.ROWS[kind], 3))
            d = {'Pa': zero, 'Da': zero, 'Pb': zero, 'Db': zero}
            if kind in ('coincident', 'distance', 'coaxial', 'angle_vec'):
                ori = da + s[:, None] * db
                pieces = [ori * sc]
                eye = np.broadcast_to(I3, (m, 3, 3))
                top_a, top_b = sc * eye, s[:, None, None] * sc * eye
                if kind in ('coincident', 'distance'):
                    pieces.append((np.einsum('ni,ni->n', da, pb - pa) - v)[:, None])
                    pa_rows, da_rows = -da[:, None, :], (pb - pa)[:, None, :]
                    pb_rows = da[:, None, :]
                    if derivatives:
                        d['Da'] = np.concatenate([top_a, da_rows], axis=1)
                        d['Db'] = np.concatenate([top_b, np.zeros((m, 1, 3))], axis=1)
                        d['Pa'] = np.concatenate([np.zeros((m, 3, 3)), pa_rows], axis=1)
                        d['Pb'] = np.concatenate([np.zeros((m, 3, 3)), pb_rows], axis=1)
                elif kind == 'coaxial':
                    delta = pb - pa
                    pieces.append(delta - np.einsum('ni,ni->n', delta, da)[:, None] * da)
                    if derivatives:
                        Pm = eye - np.einsum('ni,nj->nij', da, da)
                        dd = -(np.einsum('ni,ni->n', da, delta)[:, None, None] * eye + np.einsum('ni,nj->nij', da, delta))
                        d['Da'] = np.concatenate([top_a, dd], axis=1)
                        d['Db'] = np.concatenate([top_b, np.zeros((m, 3, 3))], axis=1)
                        d['Pa'] = np.concatenate([np.zeros((m, 3, 3)), -Pm], axis=1)
                        d['Pb'] = np.concatenate([np.zeros((m, 3, 3)), Pm], axis=1)
                elif derivatives:
                    d['Da'], d['Db'] = top_a, top_b
                block = np.concatenate(pieces, axis=1)
            elif kind == 'angle':
                block = (np.einsum('ni,ni->n', da, db) - np.cos(np.radians(v)))[:, None] * sc
                if derivatives:
                    d['Da'], d['Db'] = sc * db[:, None, :], sc * da[:, None, :]
            elif kind == 'point_on_plane':
                block = (np.einsum('ni,ni->n', db, pa - pb) - v)[:, None]
                if derivatives:
                    d['Pa'], d['Pb'], d['Db'] = db[:, None, :], -db[:, None, :], (pa - pb)[:, None, :]
            elif kind == 'point_on_axis':
                delta = pa - pb
                block = delta - np.einsum('ni,ni->n', delta, db)[:, None] * db
                if derivatives:
                    eye = np.broadcast_to(I3, (m, 3, 3))
                    Pm = eye - np.einsum('ni,nj->nij', db, db)
                    d['Pa'], d['Pb'] = Pm, -Pm
                    d['Db'] = -(np.einsum('ni,ni->n', db, delta)[:, None, None] * eye + np.einsum('ni,nj->nij', db, delta))
            else:  # point_coincident
                block = pb - pa
                if derivatives:
                    eye = np.broadcast_to(I3, (m, 3, 3))
                    d['Pa'], d['Pb'] = -eye, eye
            out[span] = block
            derived[kind] = d
        return (out, derived) if derivatives else out

    def residual(self, x):
        R, t, _ = self.frames(x)
        return self.evaluate(R, t)

    def jacobian(self, x):
        R, t, dR = self.frames(x, True)
        out, derived = self.evaluate(R, t, True)
        jac = np.zeros((self.total, 6 * self.nfree))
        for kind, (where, span) in self.groups.items():
            d = derived[kind]
            for end, idx, o, dv in (('a', self.ia, self.oa, self.da), ('b', self.ib, self.ob, self.db)):
                comp = idx[where]
                free = comp < self.nfree
                if not free.any():
                    continue
                sel, c = np.nonzero(free)[0], comp[free]
                MP = np.einsum('nkij,nj->nik', dR[c], o[where][free])
                MD = np.einsum('nkij,nj->nik', dR[c], dv[where][free])
                JP, JD = d['P' + end][free], d['D' + end][free]
                rot = JP @ MP + JD @ MD
                rows = span[sel]
                cols = 6 * c
                jac[rows[:, :, None], cols[:, None, None] + np.arange(3)[None, None, :]] += rot
                jac[rows[:, :, None], cols[:, None, None] + 3 + np.arange(3)[None, None, :]] += JP
        return jac


DIRECTIONAL = ('coincident', 'distance', 'coaxial', 'angle')
POINT_TYPES = ('point_on_plane', 'point_on_axis', 'point_coincident')


def is_vector_angle(relation):
    """An angle of 0 or 180 degrees is the same condition as aligned/opposed directions (a vector residual); other angles use the dot product."""
    return relation['type'] == 'angle' and (abs(relation['value']) < 1e-9 or abs(relation['value'] - 180.0) < 1e-9)


def independent_equations(relation):
    t = relation['type']
    if t in ('coincident', 'distance'):
        return 3
    if t == 'coaxial':
        return 4
    if t == 'angle':
        return 2 if is_vector_angle(relation) else 1
    return {'point_on_plane': 1, 'point_on_axis': 2, 'point_coincident': 3}[t]


class Problem:
    def __init__(self, components, relations, initial, fixed, scale):
        self.components = components          # id -> {'refs': {...}, 'symmetry': [...]}
        self.relations = relations            # concrete (no 'either') geometric relations
        self.fixed = fixed                    # id -> (R, t)
        self.free = [c for c in components if c not in fixed]
        self.index = {c: i for i, c in enumerate(self.free)}
        self.initial = initial
        self.scale = scale

    def pack(self, poses):
        x = np.zeros(6 * len(self.free))
        for c, i in self.index.items():
            R, t = poses[c]
            x[6 * i:6 * i + 3] = Rotation.from_matrix(R).as_rotvec()
            x[6 * i + 3:6 * i + 6] = t
        return x

    def poses(self, x):
        out = dict(self.fixed)
        for c, i in self.index.items():
            out[c] = (_rot(x[6 * i:6 * i + 3]), np.array(x[6 * i + 3:6 * i + 6]))
        return out

    def arrays(self, comp, name):
        cache = self.components[comp].setdefault('_arrays', {})
        if name not in cache:
            ref = self.components[comp]['refs'][name]
            cache[name] = (np.asarray(ref['origin'], float), np.asarray(ref['direction'], float))
        return cache[name]

    def terms(self, relation, poses):
        """(orientation residual, position residual) of one relation, unscaled; used for reporting and consistency checks."""
        (ca, ra), (cb, rb) = relation['a'], relation['b']
        oa, da = self.arrays(ca, ra)
        ob, db = self.arrays(cb, rb)
        Ra, ta = poses[ca]
        Rb, tb = poses[cb]
        Pa, Da, Pb, Db = Ra @ oa + ta, Ra @ da, Rb @ ob + tb, Rb @ db
        kind = relation['type']
        nothing = np.zeros(0)
        if kind in ('coincident', 'distance', 'coaxial') or is_vector_angle(relation):
            if kind == 'angle':
                sign = -1.0 if abs(relation['value']) < 1e-9 else 1.0
            else:
                sign = 1.0 if relation['orientation'] == 'opposed' else -1.0
            if kind == 'coaxial':
                d = Pb - Pa
                pos = d - (d @ Da) * Da
            elif kind == 'angle':
                pos = nothing
            else:
                pos = np.array([Da @ (Pb - Pa) - relation['value']])
            return Da + sign * Db, pos
        if kind == 'angle':
            return np.array([Da @ Db - np.cos(np.radians(relation['value']))]), nothing
        if kind == 'point_on_plane':
            return nothing, np.array([Db @ (Pa - Pb) - relation['value']])
        if kind == 'point_on_axis':
            d = Pa - Pb
            return nothing, d - (d @ Db) * Db
        return nothing, Pb - Pa

    def compiled(self, relations):
        key = tuple(id(r) for r in relations)
        cache = self.__dict__.setdefault('_compiled', {})
        if key not in cache:
            if len(cache) > 64:
                cache.clear()
            cache[key] = (Compiled(self, relations), relations)  # keep the list alive so ids stay unique
        return cache[key][0]

    def residual(self, x, relations=None):
        relations = self.relations if relations is None else relations
        return self.compiled(relations).residual(x) if relations else np.zeros(0)

    def analytic_jacobian(self, x, relations=None):
        """Exact derivative of ``residual`` with respect to the packed rotation vectors and translations."""
        relations = self.relations if relations is None else relations
        return self.compiled(relations).jacobian(x) if relations else np.zeros((0, 6 * len(self.free)))

    def measure(self, relation, poses):
        """(linear residual in mm, angular residual in rad) of one relation."""
        if relation['type'] == 'angle' and not is_vector_angle(relation):
            (ca, ra), (cb, rb) = relation['a'], relation['b']
            Da, Db = poses[ca][0] @ self.arrays(ca, ra)[1], poses[cb][0] @ self.arrays(cb, rb)[1]
            return 0.0, float(abs(np.arccos(np.clip(Da @ Db, -1, 1)) - np.radians(relation['value'])))
        orientation, position = self.terms(relation, poses)
        angle = 2 * np.arcsin(min(1.0, np.linalg.norm(orientation) / 2)) if len(orientation) else 0.0
        return float(np.linalg.norm(position)) if len(position) else 0.0, float(angle)

    def consistent(self, x, relations):
        poses = self.poses(x)
        return all(lin <= LINEAR_TOL and ang <= ANGULAR_TOL for lin, ang in (self.measure(r, poses) for r in relations))

    def least_squares(self, x0, relations):
        """Staged least squares. A loose run (relative cost change 1e-5, at most 60 iterations) shows where a start is heading: when it
        ends in a local minimum with a residual left, or has not reached the relations after the budget, the start is abandoned
        (there is no point polishing it); a start that reaches the relations is polished to rounding level."""
        if not relations or not self.free:
            return x0
        if not len(self.residual(x0, relations)):
            return x0
        fun, jac = (lambda x: self.residual(x, relations)), (lambda x: self.analytic_jacobian(x, relations))
        with np.errstate(all='ignore'):  # exact trust-region solves divide 0/0 on rank-deficient Jacobians; the result is checked below
            loose = least_squares(fun, x0, jac=jac, method='trf', xtol=1e-9, ftol=1e-5, gtol=1e-9, max_nfev=60)
        if not self.nearly_consistent(loose.x, relations):
            return loose.x
        return self.polish(loose.x, fun, jac)

    @staticmethod
    def polish(x, fun, jac, steps=8):
        """Minimum-norm Gauss-Newton steps (rank deficiency from redundant relations is harmless) until the residual is at rounding level."""
        r = fun(x)
        for _ in range(steps):
            if not len(r) or np.abs(r).max() < 1e-12:
                break
            step = np.linalg.lstsq(jac(x), -r, rcond=1e-10)[0]
            trial = x + step
            r_trial = fun(trial)
            if float(r_trial @ r_trial) >= float(r @ r):
                break
            x, r = trial, r_trial
        return x

    def nearly_consistent(self, x, relations, factor=1e3):
        poses = self.poses(x)
        return all(lin <= LINEAR_TOL * factor and ang <= ANGULAR_TOL * factor for lin, ang in (self.measure(r, poses) for r in relations))

    def seed(self, relations, poses=None):
        """Constructive start: align each newly reached component's reference with its partner, in declaration order."""
        poses = dict(poses or self.initial)
        placed = set(self.fixed)
        progress = True
        while progress:
            progress = False
            for relation in relations:
                kind = relation['type']
                if kind in ('point_on_plane', 'point_on_axis'):
                    continue  # no unique placement from these; least squares refines from the current pose
                (ca, ra), (cb, rb) = relation['a'], relation['b']
                if kind == 'angle':
                    # The dot product is stationary at 0 and 180 degrees, so start from a direction that already makes the angle:
                    # turn the newly reached component about its own origin, keeping the plane of the current directions.
                    for here, href, there, tref in ((ca, ra, cb, rb), (cb, rb, ca, ra)):
                        if here in placed and there not in placed:
                            _, d_here = world(self.components[here]['refs'][href], poses[here])
                            R, t = poses[there]
                            _, d_there = world(self.components[there]['refs'][tref], (R, t))
                            perp = d_there - (d_there @ d_here) * d_here
                            if np.linalg.norm(perp) < 1e-9:
                                perp = np.cross(d_here, np.array((1.0, 0, 0)) if abs(d_here[0]) < 0.9 else np.array((0, 1.0, 0)))
                            perp = perp / np.linalg.norm(perp)
                            angle = np.radians(relation['value'])
                            poses[there] = (_rotation_between(d_there, np.cos(angle) * d_here + np.sin(angle) * perp) @ R, t)
                            placed.add(there)
                            progress = True
                    continue
                for here, href, there, tref in ((ca, ra, cb, rb), (cb, rb, ca, ra)):
                    if here in placed and there not in placed:
                        p_here, d_here = world(self.components[here]['refs'][href], poses[here])
                        R, t = poses[there]
                        if kind == 'point_coincident':
                            origin = R @ np.asarray(self.components[there]['refs'][tref]['origin'])
                            poses[there] = (R, p_here - origin)
                            placed.add(there)
                            progress = True
                            continue
                        target = -d_here if relation['orientation'] == 'opposed' else d_here
                        _, d_there = world(self.components[there]['refs'][tref], (R, t))
                        R = _rotation_between(d_there, target) @ R
                        origin = R @ np.asarray(self.components[there]['refs'][tref]['origin'])
                        goal = p_here
                        if kind != 'coaxial':
                            # n_a . (p_b - p_a) = value
                            goal = p_here + relation['value'] * d_here if here == ca else p_here - relation['value'] * target
                        poses[there] = (R, goal - origin)
                        placed.add(there)
                        progress = True
        return self.pack(poses)

    def solve(self, relations, x_start, focus=None):
        """Solve only the components the relations touch (the others cannot change the outcome), then scatter the result back."""
        touched = {c for r in relations for c in (r['a'][0], r['b'][0]) if c in self.index}
        if len(touched) == len(self.free):
            return self._solve(relations, x_start, focus)
        start = self.poses(x_start)
        frozen = dict(self.fixed, **{c: start[c] for c in self.free if c not in touched})
        sub = Problem(self.components, self.relations, self.initial, frozen, self.scale)
        x_sub, ok = sub._solve(relations, sub.pack(start), focus)
        x = np.array(x_start, float)
        for c, j in sub.index.items():
            i = self.index[c]
            x[6 * i:6 * i + 6] = x_sub[6 * j:6 * j + 6]
        return x, ok

    def _solve(self, relations, x_start, focus=None):
        """Least squares from x_start, then from the constructive seed, then deterministic rotated restarts of the
        focus components (the two ends of the relation being added) to leave saddle points of the orientation terms."""
        attempts = [x_start, self.seed(relations, self.poses(x_start)), self.seed(relations)]
        best, best_cost = None, np.inf
        for x0 in attempts:
            x = self.least_squares(x0, relations)
            if self.consistent(x, relations):
                return x, True
            cost = float(np.sum(self.residual(x, relations) ** 2))
            if cost < best_cost:
                best, best_cost = x, cost
        targets = [c for c in (focus or []) if c in self.index]
        for c in targets:
            for start in STARTS:
                x0 = best.copy()
                i = self.index[c]
                x0[6 * i:6 * i + 3] = (start * Rotation.from_rotvec(x0[6 * i:6 * i + 3])).as_rotvec()
                x = self.least_squares(x0, relations)
                if self.consistent(x, relations):
                    return x, True
                cost = float(np.sum(self.residual(x, relations) ** 2))
                if cost < best_cost:
                    best, best_cost = x, cost
        return best, False

    def jacobian(self, x, relations):
        """Jacobian with respect to world twists (rotation about the component origin, then translation)."""
        poses = self.poses(x)
        base = self.residual(x, relations)
        J = np.zeros((len(base), 6 * len(self.free)))
        h = 1e-6
        for c, i in self.index.items():
            R, t = poses[c]
            for k in range(6):
                columns = []
                for s in (h, -h):
                    trial = dict(poses)
                    w = np.zeros(3)
                    dt = np.zeros(3)
                    if k < 3:
                        w[k] = s
                    else:
                        dt[k - 3] = s
                    trial[c] = (Rotation.from_rotvec(w).as_matrix() @ R, t + dt)
                    columns.append(self.residual(self.pack(trial), relations))
                J[:, 6 * i + k] = (columns[0] - columns[1]) / (2 * h)
        return J

    def freedom(self, x, relations):
        """Remaining motions: null space of the Jacobian, with symmetry spins separated out."""
        n = 6 * len(self.free)
        if not n:
            return [], []
        J = self.jacobian(x, relations) if relations else np.zeros((0, n))
        poses = self.poses(x)
        symmetric, spins = [], []
        for c, i in self.index.items():
            for name in self.components[c].get('symmetry', []):
                point, direction = world(self.components[c]['refs'][name], poses[c])
                twist = np.zeros(n)
                twist[6 * i:6 * i + 3] = direction
                twist[6 * i + 3:6 * i + 6] = np.cross(direction, poses[c][1] - point)
                if not len(J) or np.linalg.norm(J @ twist) < 1e-6:
                    spins.append(twist)
                    symmetric.append({'component': c, 'axis_reference': name, 'axis_point': _round(point), 'axis_direction': _round(direction),
                                      'note': 'Rotation about a symmetry axis of this part; it does not change the geometry.'})
        rows = [J] + ([np.array(spins)] if spins else [])
        stacked = np.vstack(rows) if any(len(r) for r in rows) else np.zeros((0, n))
        if len(stacked):
            _, s, vt = np.linalg.svd(stacked, full_matrices=True)
            rank = int(np.sum(s > 1e-7 * max(1.0, s[0] if len(s) else 1.0)))
            null = vt[rank:].T
        else:
            null = np.eye(n)
        remaining = []
        for c, i in self.index.items():
            block = null[6 * i:6 * i + 6]
            if not block.size or np.linalg.norm(block) < 1e-9:
                continue
            u, s, _ = np.linalg.svd(block, full_matrices=False)
            span = u[:, s > 1e-7]
            if not span.shape[1]:
                continue
            coupled = sorted({other for other, j in self.index.items() if other != c and np.linalg.norm(null[6 * j:6 * j + 6] @ (null[6 * i:6 * i + 6].T @ span)) > 1e-7})
            remaining.append({'component': c, 'dof': int(span.shape[1]), 'motions': _describe(span, poses[c][1]), 'coupled_with': coupled})
        return remaining, symmetric


def _round(v):
    return [round(float(a), 6) + 0.0 for a in v]


def _describe(span, origin):
    """Split a component's motion subspace into pure translations and rotations about named axes."""
    rot, trans = span[:3], span[3:]
    motions = []
    if np.linalg.norm(rot) < 1e-9:
        q, _ = np.linalg.qr(trans)
        return [{'type': 'translation', 'direction': _round(q[:, k])} for k in range(span.shape[1])]
    _, s, vt = np.linalg.svd(rot, full_matrices=True)
    rank = int(np.sum(s > 1e-7))
    pure = vt[rank:].T  # combinations without rotation
    if pure.size:
        q, _ = np.linalg.qr(trans @ pure)
        motions += [{'type': 'translation', 'direction': _round(q[:, k])} for k in range(pure.shape[1])]
    turning = vt[:rank].T
    for k in range(turning.shape[1]):
        w, v = rot @ turning[:, k], trans @ turning[:, k]
        norm = np.linalg.norm(w)
        w, v = w / norm, v / norm
        point = origin + np.cross(w, v)
        pitch = float(w @ v)
        motion = {'type': 'rotation' if abs(pitch) < 1e-6 else 'screw', 'axis_direction': _round(w), 'axis_point': _round(point)}
        if motion['type'] == 'screw':
            motion['pitch_mm_per_rad'] = round(pitch, 6)
        motions.append(motion)
    return motions


def concrete(relations, choice):
    """Replace orientation 'either' by the given choices, in order."""
    out, k = [], 0
    for relation in relations:
        relation = dict(relation)
        if relation['orientation'] == 'either':
            relation['orientation'] = choice[k]
            k += 1
        out.append(relation)
    return out


def run(components, relations, initial, fixed, scale):
    """Solve; returns status, poses, per-relation residuals, remaining DOF and conflict/ambiguity details."""
    either = [r['id'] for r in relations if r['orientation'] == 'either']
    choices = list(itertools.product(('opposed', 'aligned'), repeat=len(either)))
    outcomes = []
    for choice in choices:
        concrete_relations = concrete(relations, choice)
        problem = Problem(components, concrete_relations, initial, fixed, scale)
        x0 = problem.pack(initial)
        x, ok = problem.solve(concrete_relations, x0)
        outcomes.append((choice, problem, concrete_relations, x, ok))
    feasible = [o for o in outcomes if o[4]]
    if len(feasible) > 1:
        problem = feasible[0][1]
        return {'status': 'ambiguous', 'poses': dict(initial),
                'relations': [{'id': r['id'], 'state': 'not_evaluated'} for r in relations],
                'ambiguity': {'relations': either, 'note': 'More than one orientation satisfies the relations; none was chosen. Set orientation explicitly.',
                              'candidates': [{'orientations': dict(zip(either, o[0])),
                                              'poses': {c: pose_record(p) for c, p in o[1].poses(o[3]).items()}} for o in feasible]},
                'remaining_dof': [], 'symmetric_dof': [], 'conflicts': []}
    if feasible:
        choice, problem, concrete_relations, x, _ = feasible[0]
        determined = dict(zip(either, choice))
        accepted, failing = concrete_relations, []
    else:
        choice, problem, concrete_relations, x, _ = outcomes[0]
        determined = {}
        accepted, failing, x = [], [], problem.pack(initial)
        for relation in concrete_relations:
            trial = accepted + [relation]
            x_try, ok = problem.solve(trial, x, focus=[relation['a'][0], relation['b'][0]])
            if ok:
                accepted, x = trial, x_try
            else:
                failing.append(relation)
    poses = problem.poses(x)
    conflicts = []
    for relation in failing:
        involved = []
        for other in accepted:
            rest = [r for r in accepted if r is not other] + [relation]
            _, ok = problem.solve(rest, x)
            if ok:
                involved.append(other['id'])
        lin, ang = problem.measure(relation, poses)
        conflicts.append({'failing_relation': relation['id'], 'components': [relation['a'][0], relation['b'][0]],
                          'residual_mm': lin, 'residual_deg': float(np.degrees(ang)), 'conflicts_with': involved,
                          'note': 'Relations are accepted in declaration order; this one cannot hold together with the accepted ones listed.'})
    rows = []
    failing_ids = {r['id'] for r in failing}
    for relation in concrete_relations:
        lin, ang = problem.measure(relation, poses)
        row = {'id': relation['id'], 'state': 'violated' if relation['id'] in failing_ids else 'satisfied',
               'residual_mm': lin, 'residual_deg': float(np.degrees(ang)), 'orientation': relation['orientation']}
        if relation['id'] in determined:
            row['orientation_determined'] = determined[relation['id']]
        rows.append(row)
    remaining, symmetric = problem.freedom(x, accepted)
    status = 'incompatible' if failing else 'under_constrained' if remaining else 'solved'
    return {'status': status, 'poses': poses, 'relations': rows, 'remaining_dof': remaining, 'symmetric_dof': symmetric,
            'conflicts': conflicts, 'ambiguity': None, **({'redundant_but_consistent': redundancy(problem, x, accepted)} if not failing else {})}


def redundancy(problem, x, relations):
    """How many residual equations are implied by others (consistent over-definition, not an error)."""
    if not relations or not problem.free:
        return 0
    J = problem.jacobian(x, relations)
    s = np.linalg.svd(J, compute_uv=False)
    rank = int(np.sum(s > 1e-7 * max(1.0, s[0])))
    equations = sum(independent_equations(r) for r in relations)
    return max(0, equations - rank)


def pose_record(pose):
    R, t = pose
    matrix = np.eye(4)
    matrix[:3, :3], matrix[:3, 3] = R, t
    clean = lambda v: 0.0 if abs(v) < 1e-12 else float(v)
    return {'translation_mm': [clean(v) for v in t], 'rotation_deg_xyz': [clean(v) for v in Rotation.from_matrix(R).as_euler('xyz', degrees=True)],
            'matrix': [[clean(v) for v in row] for row in matrix]}


def pose_from_spec(pose):
    return Rotation.from_euler('xyz', pose['rotation_deg'], degrees=True).as_matrix(), np.array(pose['translation'], float)


def pose_from_matrix(matrix):
    m = np.asarray(matrix, float)
    return m[:3, :3], m[:3, 3]
