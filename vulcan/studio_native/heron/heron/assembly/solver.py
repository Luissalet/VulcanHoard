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
            out[c] = (Rotation.from_rotvec(x[6 * i:6 * i + 3]).as_matrix(), np.array(x[6 * i + 3:6 * i + 6]))
        return out

    def relation_residual(self, relation, poses):
        (ca, ra), (cb, rb) = relation['a'], relation['b']
        pa, da = world(self.components[ca]['refs'][ra], poses[ca])
        pb, db = world(self.components[cb]['refs'][rb], poses[cb])
        sign = 1.0 if relation['orientation'] == 'opposed' else -1.0
        orientation = da + sign * db
        if relation['type'] == 'coaxial':
            delta = pb - pa
            position = delta - (delta @ da) * da
        else:
            position = np.array([da @ (pb - pa) - relation['value']])
        return orientation, position

    def residual(self, x, relations=None):
        poses = self.poses(x)
        parts = []
        for relation in (self.relations if relations is None else relations):
            orientation, position = self.relation_residual(relation, poses)
            parts += [orientation * self.scale, position]
        return np.concatenate(parts) if parts else np.zeros(0)

    def measure(self, relation, poses):
        orientation, position = self.relation_residual(relation, poses)
        angle = 2 * np.arcsin(min(1.0, np.linalg.norm(orientation) / 2))
        return float(np.linalg.norm(position)), float(angle)

    def consistent(self, x, relations):
        poses = self.poses(x)
        return all(lin <= LINEAR_TOL and ang <= ANGULAR_TOL for lin, ang in (self.measure(r, poses) for r in relations))

    def least_squares(self, x0, relations):
        if not relations or not self.free:
            return x0
        fun = lambda x: self.residual(x, relations)
        if not len(fun(x0)):
            return x0
        result = least_squares(fun, x0, method='trf', xtol=1e-13, ftol=1e-13, gtol=1e-13, max_nfev=200)
        return result.x

    def seed(self, relations, poses=None):
        """Constructive start: align each newly reached component's reference with its partner, in declaration order."""
        poses = dict(poses or self.initial)
        placed = set(self.fixed)
        progress = True
        while progress:
            progress = False
            for relation in relations:
                (ca, ra), (cb, rb) = relation['a'], relation['b']
                for here, href, there, tref in ((ca, ra, cb, rb), (cb, rb, ca, ra)):
                    if here in placed and there not in placed:
                        p_here, d_here = world(self.components[here]['refs'][href], poses[here])
                        target = -d_here if relation['orientation'] == 'opposed' else d_here
                        R, t = poses[there]
                        _, d_there = world(self.components[there]['refs'][tref], (R, t))
                        R = _rotation_between(d_there, target) @ R
                        origin = R @ np.asarray(self.components[there]['refs'][tref]['origin'])
                        goal = p_here
                        if relation['type'] != 'coaxial':
                            # n_a . (p_b - p_a) = value
                            goal = p_here + relation['value'] * d_here if here == ca else p_here - relation['value'] * target
                        poses[there] = (R, goal - origin)
                        placed.add(there)
                        progress = True
        return self.pack(poses)

    def solve(self, relations, x_start, focus=None):
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
    equations = sum(2 + (2 if r['type'] == 'coaxial' else 1) for r in relations)
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
