"""
core/card_pose.py - Fit an ACNH villager's upper-body pose to its amiibo card.

bpy-free (numpy only) so it is unit-testable outside Blender.

Model
-----
* The rig is given as rest matrices (armature space), parent indices and the
  current pose-basis matrices, plus the armature object's world matrix.
* Only ``POSED_BONES`` are solved (Luis, 2026-09-25: hip, shoulders, elbows,
  neck - nothing else). Every other bone keeps its current basis, so legs,
  feet, face, wrists, tail, collar... are left exactly as they were.
* The card is treated as a pinhole render about the image centre (fitted
  distance; kappa = 1/distance, 0 = orthographic ``uv = s * R[:2] @ X + t``),
  image y pointing DOWN (pixel convention). ``R`` is a rotation relative
  to a "front view" of the armature; yaw is free (walled past 75 deg),
  actual elevation fixed when supplied, roll kept near zero.
* Solve = Levenberg-Marquardt over camera (Euler 3, log-scale 1, t 2, distance) and one
  rotation-vector per posed bone, applied on top of the current basis.
  Residuals: 2D keypoint error (normalised by image height) + a weak prior
  pulling each bone back to its reference basis. Twist follows the direction
  to the next anatomical joint, not Blender's display-bone axis.
* Optional hand keypoints and surface-plane visibility observations constrain
  wing orientation without adding any hand/wrist degrees of freedom.
* Depth ambiguity (wing in front of / behind the body) is handled by
  multi-start: several yaws x several random arm initialisations, lowest
  total cost wins.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field

import numpy as np

# Bones we are allowed to rotate. Order matters only for the parameter layout.
POSED_BONES = ("Spine_1", "Neck", "Arm_1_L", "Arm_2_L", "Arm_1_R", "Arm_2_R")

# Card keypoint name -> bone whose HEAD is that joint.
KEYPOINT_BONES = {
    "pelvis": "Spine_1",
    "chest": "Spine_3",
    "neck": "Neck",
    "head": "Head",
    "crown": "Feel",
    "face": "Mouth",
    "shoulder_L": "Arm_1_L",
    "elbow_L": "Arm_2_L",
    "wrist_L": "Wrist_L",
    "shoulder_R": "Arm_1_R",
    "elbow_R": "Arm_2_R",
    "wrist_R": "Wrist_R",
    # Observation only: these bones are never rotated by the solver. The
    # off-axis hand heads reveal the wing plane, which joint centres cannot.
    "hand_L": "Hand_L",
    "hand_R": "Hand_R",
    "hip_L": "Leg_1_L",
    "knee_L": "Leg_2_L",
    "ankle_L": "Ankle_L",
    "toe_L": "Toe_L",
    "hip_R": "Leg_1_R",
    "knee_R": "Leg_2_R",
    "ankle_R": "Ankle_R",
    "toe_R": "Toe_R",
}

# Prior strengths (sqrt-weights on radians, in "image-height" residual units:
# 1 px on a 322 px card ~= 0.0031).
PRIOR_SWING = 0.010      # ~1 rad of swing costs about as much as 3 px of error
PRIOR_TWIST = 0.060      # twist is unobservable -> hold it
PRIOR_NECK = 0.020       # the neck should not do the arms' job
PRIOR_HIP = 0.050        # a mild lean is allowed; the separate wall limits bends
# Camera elevation may be fixed per card; a free elevation is weakly regularized.
# Roll stays near zero, yaw is regularized past YAW_LIMIT.
PRIOR_CAM_PITCH = 0.003
# When a pitch is given (card series share one camera rig), it is held
# exactly instead: a free pitch trades off against hip lean / neck pitch and
# lands anywhere between 40 deg below and 50 deg above.
PRIOR_CAM_PITCH_FIXED = 2.0
# Default elevation for amiibo cards (None = free). Calibrated on Keaton #193.
CARD_PITCH_DEG = 30.0
PRIOR_CAM_ROLL = 0.030
YAW_LIMIT = math.radians(75)
YAW_WALL = 0.5
# Perspective: cards are real renders (big head close to the lens). Distance
# is fitted as sqrt(1/D); a light prior keeps it from collapsing to fisheye.
PRIOR_PERSPECTIVE = 0.010
KAPPA_MAX = 1.0 / 35.0    # camera no closer than ~3 villager heights
KAPPA_WALL = 5.0


# ----------------------------------------------------------------------------
# Small rotation helpers
# ----------------------------------------------------------------------------

def rotvec_to_matrix(w) -> np.ndarray:
    w = np.asarray(w, dtype=float)
    th = float(np.linalg.norm(w))
    if th < 1e-12:
        return np.eye(3)
    k = w / th
    K = np.array([[0.0, -k[2], k[1]], [k[2], 0.0, -k[0]], [-k[1], k[0], 0.0]])
    return np.eye(3) + math.sin(th) * K + (1.0 - math.cos(th)) * (K @ K)


def matrix_to_quaternion(R) -> np.ndarray:
    """3x3 rotation -> (w, x, y, z), w >= 0."""
    R = np.asarray(R, dtype=float)
    tr = np.trace(R)
    if tr > 0:
        S = math.sqrt(tr + 1.0) * 2
        q = [0.25 * S, (R[2, 1] - R[1, 2]) / S, (R[0, 2] - R[2, 0]) / S,
             (R[1, 0] - R[0, 1]) / S]
    elif R[0, 0] > R[1, 1] and R[0, 0] > R[2, 2]:
        S = math.sqrt(1.0 + R[0, 0] - R[1, 1] - R[2, 2]) * 2
        q = [(R[2, 1] - R[1, 2]) / S, 0.25 * S, (R[0, 1] + R[1, 0]) / S,
             (R[0, 2] + R[2, 0]) / S]
    elif R[1, 1] > R[2, 2]:
        S = math.sqrt(1.0 + R[1, 1] - R[0, 0] - R[2, 2]) * 2
        q = [(R[0, 2] - R[2, 0]) / S, (R[0, 1] + R[1, 0]) / S, 0.25 * S,
             (R[1, 2] + R[2, 1]) / S]
    else:
        S = math.sqrt(1.0 + R[2, 2] - R[0, 0] - R[1, 1]) * 2
        q = [(R[1, 0] - R[0, 1]) / S, (R[0, 2] + R[2, 0]) / S,
             (R[1, 2] + R[2, 1]) / S, 0.25 * S]
    q = np.array(q)
    q /= np.linalg.norm(q)
    return q if q[0] >= 0 else -q


def _rot4(R3) -> np.ndarray:
    M = np.eye(4)
    M[:3, :3] = R3
    return M


# ----------------------------------------------------------------------------
# Rig + forward kinematics
# ----------------------------------------------------------------------------

@dataclass
class Rig:
    names: list
    parents: list            # parent index or -1
    rest: np.ndarray         # (N,4,4) bone.matrix_local (armature space)
    basis: np.ndarray        # (N,4,4) pose_bone.matrix_basis (current)
    world: np.ndarray = field(default_factory=lambda: np.eye(4))  # armature matrix_world

    def __post_init__(self):
        self.rest = np.asarray(self.rest, dtype=float)
        self.basis = np.asarray(self.basis, dtype=float)
        self.world = np.asarray(self.world, dtype=float)
        n = len(self.names)
        if (len(set(self.names)) != n or len(self.parents) != n
                or self.rest.shape != (n, 4, 4) or self.basis.shape != (n, 4, 4)
                or self.world.shape != (4, 4)
                or not all(np.isfinite(a).all() for a in (self.rest, self.basis, self.world))):
            raise ValueError("Invalid rig matrices or bone inventory")
        if any(p < -1 or p >= n for p in self.parents):
            raise ValueError("Invalid rig parent index")
        self.index = {n: i for i, n in enumerate(self.names)}
        # parent-first evaluation order
        order, seen, visiting = [], set(), set()

        def visit(i):
            if i in seen:
                return
            if i in visiting:
                raise ValueError("Cyclic rig hierarchy")
            visiting.add(i)
            p = self.parents[i]
            if p >= 0:
                visit(p)
            seen.add(i)
            visiting.remove(i)
            order.append(i)

        for i in range(len(self.names)):
            visit(i)
        self.order = order
        self.rel = np.empty_like(self.rest)
        for i, p in enumerate(self.parents):
            self.rel[i] = self.rest[i] if p < 0 else np.linalg.inv(self.rest[p]) @ self.rest[i]

    def subset_order(self, needed) -> list:
        """Evaluation order restricted to ``needed`` bones and their ancestors."""
        keep = set()
        for i in needed:
            while i >= 0 and i not in keep:
                keep.add(i)
                i = self.parents[i]
        return [i for i in self.order if i in keep]

    def fk(self, basis_override=None, order=None) -> np.ndarray:
        """World-space bone head positions (N,3). With ``order`` (from
        :meth:`subset_order`) only those rows are valid - the rest are junk."""
        return self.transforms(basis_override, order)[:, :3, 3]

    def transforms(self, basis_override=None, order=None):
        """World transforms, also used for observed surface orientations."""
        basis_override = basis_override or {}
        pose = np.zeros_like(self.rest)
        for i in (self.order if order is None else order):
            b = basis_override.get(i, self.basis[i])
            p = self.parents[i]
            local = self.rel[i] @ b
            pose[i] = local if p < 0 else pose[p] @ local
        return self.world @ pose

    def front_camera_base(self) -> np.ndarray:
        """Rows (r1 image-right, r2 image-down, r3 view dir) for a straight
        front view of the armature (armature +Z = forward, +Y = up)."""
        W = self.world[:3, :3]
        right = W @ np.array([1.0, 0.0, 0.0])
        up = W @ np.array([0.0, 1.0, 0.0])
        right /= np.linalg.norm(right)
        up /= np.linalg.norm(up)
        r2 = -up
        r3 = np.cross(right, r2)
        return np.stack([right, r2, r3])


# ----------------------------------------------------------------------------
# Solver
# ----------------------------------------------------------------------------

@dataclass
class FitResult:
    bone_basis: dict          # bone name -> new 4x4 basis matrix
    cam_rows: np.ndarray      # (3,3) r1 image-right, r2 image-down, r3 view dir
    scale: float              # px per world unit at the reference depth z0
    t: np.ndarray             # (2,) px offset (orthographic part)
    cost: float
    errors_px: dict           # keypoint -> reprojection error in px
    projected: dict           # keypoint -> fitted (u, v) px
    image_size: tuple
    kappa: float = 0.0        # 1 / camera distance (0 = orthographic)
    z0: float = 0.0           # reference depth (r3 . chest) the scale refers to

    @property
    def rms_px(self) -> float:
        e = np.array(list(self.errors_px.values()))
        return float(np.sqrt(np.mean(e ** 2))) if len(e) else 0.0


def _project_points(P, Rc, s, t, kappa, z0, W, H):
    """Pinhole about the image centre; kappa=0 reduces to ``s*R[:2]@X + t``."""
    ortho = s * (P @ Rc[:2].T) + t
    d = 1.0 + kappa * (P @ Rc[2] - z0)
    d = np.maximum(d, 0.05)[:, None]            # never behind the camera
    centre = np.array([W / 2, H / 2])
    return centre + (ortho - centre) / d


# Parameter layout: camera elevation/yaw/roll, log s, tx, ty, sqrt(kappa), then bones.
_NCAM = 7
_REF_BONE = "Spine_3"     # depth reference for the perspective scale


class _Problem:
    def __init__(self, rig: Rig, keypoints: dict, image_size, weights=None,
                 posed=POSED_BONES, pitch=None, perspective=True, surfaces=None):
        self.rig = rig
        self.pitch = pitch      # radians, +down; None = (almost) free
        self.perspective = perspective
        self.W, self.H = float(image_size[0]), float(image_size[1])
        if not np.isfinite([self.W, self.H]).all() or min(self.W, self.H) <= 0:
            raise ValueError("image_size must contain two positive finite dimensions")
        weights = weights or {}
        self.kp_names, kp_idx, tgt, w = [], [], [], []
        for name, uv in keypoints.items():
            bone = KEYPOINT_BONES.get(name)
            if bone is None or bone not in rig.index or uv is None:
                continue
            uv = np.asarray(uv, dtype=float)
            weight = float(weights.get(name, 1.0))
            if uv.shape != (2,) or not np.isfinite(uv).all() or not np.isfinite(weight) or weight < 0:
                raise ValueError(f"Invalid keypoint or weight: {name}")
            if weight == 0:
                continue
            self.kp_names.append(name)
            kp_idx.append(rig.index[bone])
            tgt.append(uv)
            w.append(weight)
        if len(self.kp_names) < 4:
            raise ValueError(f"need >= 4 usable keypoints, got {self.kp_names}")
        self.kp_idx = np.array(kp_idx)
        self.target = np.array(tgt, dtype=float)
        self.w = np.array(w)[:, None]
        self.ref = rig.index.get(_REF_BONE, 0)
        self.fk_order = rig.subset_order(list(self.kp_idx) + [self.ref])
        self.surfaces = []
        for label, spec in (surfaces or {}).items():
            normal = np.asarray(spec['normal'], dtype=float)
            facing = float(spec.get('min_facing', .65))
            weight = float(spec.get('weight', .1))
            bone = rig.index.get(spec['bone'])
            if (bone is None or normal.shape != (3,) or not np.isfinite(normal).all()
                    or np.linalg.norm(normal) < 1e-9 or not 0 <= facing <= 1
                    or not math.isfinite(weight) or weight < 0):
                raise ValueError(f'Invalid surface observation: {label}')
            self.surfaces.append((bone, normal / np.linalg.norm(normal), facing, weight))
        self.surface_order = rig.subset_order([s[0] for s in self.surfaces])
        self.posed = [rig.index[b] for b in posed if b in rig.index]
        self.posed_names = [rig.names[i] for i in self.posed]
        self.n = _NCAM + 3 * len(self.posed)
        self.prior_axes, self.prior_swing = [], []
        downstream = {"Spine_1": "Spine_2", "Neck": "Head",
                      "Arm_1_L": "Arm_2_L", "Arm_1_R": "Arm_2_R",
                      "Arm_2_L": "Wrist_L", "Arm_2_R": "Wrist_R"}
        for name in self.posed_names:
            swing = {"Neck": PRIOR_NECK, "Spine_1": PRIOR_HIP}.get(name, PRIOR_SWING)
            i = rig.index[name]
            child = rig.index.get(downstream.get(name))
            axis = (np.linalg.inv(rig.rest[i]) @ rig.rest[child])[:3, 3] if child is not None else np.array([0., 1., 0.])
            axis = axis / max(np.linalg.norm(axis), 1e-12)
            self.prior_axes.append(axis)
            self.prior_swing.append(swing)
        self.prior_axes = np.asarray(self.prior_axes).reshape(-1, 3)
        self.prior_swing = np.asarray(self.prior_swing)[:, None]

    def unpack(self, x, R_base):
        pitch = x[0] if self.pitch is None else self.pitch
        # True elevation/yaw/roll: a rotvec component is not an Euler angle.
        Rc = (rotvec_to_matrix([0, 0, x[2]]) @ rotvec_to_matrix([pitch, 0, 0])
              @ rotvec_to_matrix([0, x[1], 0]) @ R_base)
        s = math.exp(float(np.clip(x[3], -20, 20)))
        t = x[4:6]
        kappa = x[6] ** 2 if self.perspective else 0.0
        om = x[_NCAM:].reshape(-1, 3)
        override = {i: self.rig.basis[i] @ _rot4(rotvec_to_matrix(om[k]))
                    for k, i in enumerate(self.posed)}
        return Rc, s, t, kappa, om, override

    def project(self, x, R_base, with_z0=False):
        Rc, s, t, kappa, om, override = self.unpack(x, R_base)
        P_all = self.rig.fk(override, self.fk_order)
        z0 = float(P_all[self.ref] @ Rc[2])
        uv = _project_points(P_all[self.kp_idx], Rc, s, t, kappa, z0, self.W, self.H)
        return (uv, z0) if with_z0 else uv

    def residual(self, x, R_base):
        uv = self.project(x, R_base)
        r_kp = (self.w * (uv - self.target) / self.H).ravel()
        om = x[_NCAM:].reshape(-1, 3)
        twist = self.prior_axes * np.sum(om * self.prior_axes, axis=1)[:, None]
        r_prior = (self.prior_swing * (om - twist) + PRIOR_TWIST * twist).ravel()
        yaw_excess = max(0.0, abs(x[1]) - YAW_LIMIT)
        if self.pitch is None:
            r_pitch = PRIOR_CAM_PITCH * x[0]
        else:
            r_pitch = PRIOR_CAM_PITCH_FIXED * (x[0] - self.pitch)
        r_cam = np.array([r_pitch, PRIOR_CAM_ROLL * x[2],
                          YAW_WALL * yaw_excess, PRIOR_PERSPECTIVE * x[6],
                          KAPPA_WALL * max(0.0, x[6] ** 2 - KAPPA_MAX)])
        hip_limit = [0.5 * max(0.0, np.linalg.norm(om[k]) - math.radians(25))
                     for k, name in enumerate(self.posed_names) if name == "Spine_1"]
        r_surface = []
        if self.surfaces:
            Rc, _, _, _, _, override = self.unpack(x, R_base)
            matrices = self.rig.transforms(override, self.surface_order)
            for bone, normal, facing, weight in self.surfaces:
                n = np.linalg.solve(matrices[bone, :3, :3].T, normal)
                n /= np.linalg.norm(n)
                # A two-sided feather plane: do not invent front/back evidence.
                r_surface.append(weight * max(0., facing - abs(n @ Rc[2])))
        return np.concatenate([r_kp, r_prior, r_cam, hip_limit, r_surface])

    def init_scale_offset(self, x, R_base):
        """Closed-form s, t for the current rotation/pose (orthographic LSQ)."""
        x = x.copy()
        x[3] = 0.0
        x[4:6] = 0.0
        k = x[6]
        x[6] = 0.0
        persp, self.perspective = self.perspective, False
        q = self.project(x, R_base)         # s=1, t=0, ortho
        self.perspective = persp
        A = np.zeros((2 * len(q), 3))
        A[0::2, 0] = q[:, 0]
        A[1::2, 0] = q[:, 1]
        A[0::2, 1] = 1.0
        A[1::2, 2] = 1.0
        b = self.target.ravel()
        ww = np.repeat(self.w.ravel(), 2)
        sol, *_ = np.linalg.lstsq(A * ww[:, None], b * ww, rcond=None)
        s = max(sol[0], 1e-6)
        x[3] = math.log(s)
        x[4:6] = sol[1:3]
        x[6] = k
        return x


def _levenberg_marquardt(f, x0, iters=80, eps=1e-6):
    x = x0.copy()
    r = f(x)
    cost = float(r @ r)
    mu = 1e-3
    for _ in range(iters):
        J = np.empty((len(r), len(x)))
        for j in range(len(x)):
            xp = x.copy()
            xp[j] += eps
            J[:, j] = (f(xp) - r) / eps
        g = J.T @ r
        A = J.T @ J
        improved = False
        for _ in range(10):
            try:
                dx = -np.linalg.solve(A + mu * np.diag(np.diag(A) + 1e-9), g)
            except np.linalg.LinAlgError:
                mu *= 10
                continue
            xn = x + dx
            rn = f(xn)
            cn = float(rn @ rn)
            if cn < cost:
                x, r, cost = xn, rn, cn
                mu = max(mu / 3, 1e-9)
                improved = True
                break
            mu *= 4
        if not improved or float(np.linalg.norm(dx)) < 1e-7:
            break
    return x, cost


def fit_card_pose(rig: Rig, keypoints: dict, image_size, weights=None,
                  yaws_deg=(-40, -20, 0, 20, 40), restarts=3,
                  seed=0, posed=POSED_BONES, pitch_deg=None,
                  perspective=True, surfaces=None) -> FitResult:
    """Fit camera + posed-bone rotations so the rig's joints project onto the
    card keypoints. ``keypoints``: name -> (u, v) in card pixels, y down.
    ``pitch_deg``: camera elevation (+ = looking down); None leaves it free.
    ``perspective``: also fit the camera distance (pinhole); False = ortho."""
    if restarts < 1 or not len(yaws_deg):
        raise ValueError("At least one solver start is required")
    if pitch_deg is not None and (not math.isfinite(pitch_deg) or abs(pitch_deg) >= 89):
        raise ValueError("camera_pitch_deg must be finite and between -89 and 89")
    pitch = None if pitch_deg is None else math.radians(pitch_deg)
    prob = _Problem(rig, keypoints, image_size, weights, posed, pitch, perspective, surfaces)
    R_base = rig.front_camera_base()
    rng = np.random.default_rng(seed)
    arm_slots = [k for k, n in enumerate(prob.posed_names) if n.startswith("Arm_")]

    # Coarse pass over every start (few iterations), then polish the best few.
    f = lambda x: prob.residual(x, R_base)  # noqa: E731
    starts = []
    for yaw in yaws_deg:
        for rs in range(restarts):
            x0 = np.zeros(prob.n)
            x0[1] = math.radians(yaw)          # camera yaw (about image-down axis)
            x0[0] = pitch or 0.0                # camera pitch (+ = looking down)
            x0[6] = 0.12 if perspective else 0.0   # kappa ~ 1/70 units
            if rs > 0:
                om = x0[_NCAM:].reshape(-1, 3)
                for k in arm_slots:
                    om[k] = rng.normal(0.0, 0.7, 3) * np.array([1.0, 0.2, 1.0])
            x0 = prob.init_scale_offset(x0, R_base)
            starts.append(_levenberg_marquardt(f, x0, iters=15))
    starts.sort(key=lambda xc: xc[1])
    x, cost = min((_levenberg_marquardt(f, xs) for xs, _ in starts[:3]),
                  key=lambda xc: xc[1])
    Rc, s, t, kappa, om, override = prob.unpack(x, R_base)
    uv, z0 = prob.project(x, R_base, with_z0=True)
    errs = np.linalg.norm(uv - prob.target, axis=1)
    return FitResult(
        bone_basis={rig.names[i]: override[i] for i in prob.posed},
        cam_rows=Rc,
        scale=s,
        t=np.asarray(t, dtype=float),
        cost=cost,
        errors_px={n: float(e) for n, e in zip(prob.kp_names, errs)},
        projected={n: (float(a), float(b)) for n, (a, b) in zip(prob.kp_names, uv)},
        image_size=(prob.W, prob.H),
        kappa=float(kappa),
        z0=z0,
    )


def project_all(rig: Rig, res: FitResult, basis_override=None) -> dict:
    """Project every keypoint-bone head with the fitted camera (for overlays)."""
    idx = rig.index
    override = {idx[n]: m for n, m in (res.bone_basis if basis_override is None else basis_override).items()}
    P = rig.fk(override)
    W, H = res.image_size
    out = {}
    for kp, bone in KEYPOINT_BONES.items():
        if bone in idx:
            u, v = _project_points(P[idx[bone]][None], res.cam_rows, res.scale,
                                   res.t, res.kappa, res.z0, W, H)[0]
            out[kp] = (float(u), float(v))
    return out


def blender_camera(res: FitResult, ortho_distance=100.0):
    """Blender camera reproducing the fit.

    Returns ``(matrix_world 4x4, kind, value)``: kind 'ORTHO' -> value is
    ortho_scale; kind 'PERSP' -> value is the focal length in *pixels of the
    larger image side* (lens_mm = value / max(W, H) * sensor_mm with
    sensor_fit AUTO). Blender camera: local X right, local Y up, looks -Z.
    """
    r1, r2, r3 = res.cam_rows
    W, H = res.image_size
    a = (W / 2 - res.t[0]) / res.scale
    b = (H / 2 - res.t[1]) / res.scale
    M = np.eye(4)
    M[:3, 0] = r1
    M[:3, 1] = -r2
    M[:3, 2] = -r3
    if res.kappa > 1e-6:
        D = 1.0 / res.kappa
        axis_point = a * r1 + b * r2 + res.z0 * r3
        M[:3, 3] = axis_point - D * r3
        return M, "PERSP", res.scale * D
    M[:3, 3] = a * r1 + b * r2 - ortho_distance * r3
    return M, "ORTHO", max(W, H) / res.scale
