"""Forward kinematics of a glTF node tree for the animated-asset tools: rest and animated local transforms, world
matrices at any clip time, quaternion helpers (x, y, z, w like glTF). Pure numpy; reads clips through
asset_animation.Channel so every consumer samples exactly like the inspection does."""
from __future__ import annotations

import math

import numpy as np


def qmul(a, b):
    ax, ay, az, aw = a
    bx, by, bz, bw = b
    return np.array([aw * bx + ax * bw + ay * bz - az * by, aw * by - ax * bz + ay * bw + az * bx,
                     aw * bz + ax * by - ay * bx + az * bw, aw * bw - ax * bx - ay * by - az * bz])


def qinv(q):
    q = np.asarray(q, dtype=float)
    return np.array([-q[0], -q[1], -q[2], q[3]]) / max(float(np.dot(q, q)), 1e-300)


def qnorm(q):
    q = np.asarray(q, dtype=float)
    n = np.linalg.norm(q)
    return q / n if n > 0 else np.array([0.0, 0.0, 0.0, 1.0])


def qmat(q):
    x, y, z, w = qnorm(q)
    return np.array([[1 - 2 * (y * y + z * z), 2 * (x * y - z * w), 2 * (x * z + y * w)],
                     [2 * (x * y + z * w), 1 - 2 * (x * x + z * z), 2 * (y * z - x * w)],
                     [2 * (x * z - y * w), 2 * (y * z + x * w), 1 - 2 * (x * x + y * y)]])


def mat_q(m):
    """Unit quaternion (x, y, z, w) of a rotation matrix (scale removed by the caller)."""
    m = np.asarray(m, dtype=float)
    t = np.trace(m)
    if t > 0:
        s = math.sqrt(t + 1.0) * 2
        q = [(m[2, 1] - m[1, 2]) / s, (m[0, 2] - m[2, 0]) / s, (m[1, 0] - m[0, 1]) / s, 0.25 * s]
    elif m[0, 0] > m[1, 1] and m[0, 0] > m[2, 2]:
        s = math.sqrt(1.0 + m[0, 0] - m[1, 1] - m[2, 2]) * 2
        q = [0.25 * s, (m[0, 1] + m[1, 0]) / s, (m[0, 2] + m[2, 0]) / s, (m[2, 1] - m[1, 2]) / s]
    elif m[1, 1] > m[2, 2]:
        s = math.sqrt(1.0 + m[1, 1] - m[0, 0] - m[2, 2]) * 2
        q = [(m[0, 1] + m[1, 0]) / s, 0.25 * s, (m[1, 2] + m[2, 1]) / s, (m[0, 2] - m[2, 0]) / s]
    else:
        s = math.sqrt(1.0 + m[2, 2] - m[0, 0] - m[1, 1]) * 2
        q = [(m[0, 2] + m[2, 0]) / s, (m[1, 2] + m[2, 1]) / s, 0.25 * s, (m[1, 0] - m[0, 1]) / s]
    return qnorm(q)


def qangle(a, b):
    """Angle in degrees between two rotations (q and -q are the same)."""
    return math.degrees(2 * math.acos(min(1.0, abs(float(np.dot(qnorm(a), qnorm(b)))))))


def slerp(a, b, t):
    a, b = qnorm(a), qnorm(b)
    d = float(np.dot(a, b))
    if d < 0:
        b, d = -b, -d
    if d > 0.9995:
        return qnorm(a + t * (b - a))
    th = math.acos(d)
    return (math.sin((1 - t) * th) * a + math.sin(t * th) * b) / math.sin(th)


def compose(t, r, s):
    m = np.eye(4)
    m[:3, :3] = qmat(r) * np.asarray(s, dtype=float)[None, :]
    m[:3, 3] = t
    return m


def decompose(m):
    m = np.asarray(m, dtype=float).reshape(4, 4)
    t = m[:3, 3].copy()
    s = np.linalg.norm(m[:3, :3], axis=0)
    rot = m[:3, :3] / np.where(s == 0, 1, s)[None, :]
    if np.linalg.det(rot) < 0:  # a mirror: put it on x scale
        s[0], rot[:, 0] = -s[0], -rot[:, 0]
    return t, mat_q(rot), s


class Rig:
    """The node tree of a glTF document with its rest pose; evaluates any clip at any time (local and world)."""

    def __init__(self, doc):
        self.doc = doc
        self.nodes = doc.gltf.get('nodes', [])
        self.names = [n.get('name') or f'node_{i}' for i, n in enumerate(self.nodes)]
        self.parent = {}
        for i, node in enumerate(self.nodes):
            for child in node.get('children', []):
                self.parent[child] = i
        self.rest = []
        for node in self.nodes:
            if 'matrix' in node:
                self.rest.append(decompose(np.array(node['matrix'], dtype=float).reshape(4, 4).T))
            else:
                self.rest.append((np.array(node.get('translation', [0, 0, 0]), dtype=float), qnorm(node.get('rotation', [0, 0, 0, 1])),
                                  np.array(node.get('scale', [1, 1, 1]), dtype=float)))
        roots = [i for i in range(len(self.nodes)) if i not in self.parent]
        self.order, stack = [], list(reversed(roots))
        while stack:
            i = stack.pop()
            self.order.append(i)
            stack.extend(reversed(self.nodes[i].get('children', [])))

    def channels(self, clip):
        from .asset_animation import Channel
        anim = self.doc.gltf['animations'][clip]
        out = {}
        for c in range(len(anim.get('channels', []))):
            ch = Channel(self.doc, clip, c)
            if ch.node is not None and ch.path in ('translation', 'rotation', 'scale'):
                out[(ch.node, ch.path)] = ch
        return out

    def span(self, clip):
        chans = self.channels(clip).values()
        return (min((float(c.times[0]) for c in chans), default=0.0), max((float(c.times[-1]) for c in chans), default=0.0))

    def local(self, chans=None, t=0.0):
        out = []
        for i, (rt, rr, rs) in enumerate(self.rest):
            c = chans or {}
            tt = c[(i, 'translation')].at(t) if (i, 'translation') in c else rt
            rr2 = c[(i, 'rotation')].at(t) if (i, 'rotation') in c else rr
            ss = c[(i, 'scale')].at(t) if (i, 'scale') in c else rs
            out.append((np.asarray(tt, dtype=float), qnorm(rr2), np.asarray(ss, dtype=float)))
        return out

    def world(self, chans=None, t=0.0, local=None):
        local = local or self.local(chans, t)
        out = [None] * len(self.nodes)
        for i in self.order:
            m = compose(*local[i])
            p = self.parent.get(i)
            out[i] = m if p is None else out[p] @ m
        return out

    def joints(self):
        """Node indices used as joints by any skin, in skin order without repeats."""
        seen = []
        for skin in self.doc.gltf.get('skins', []):
            for j in skin.get('joints', []):
                if j not in seen:
                    seen.append(j)
        return seen

    def joint_positions(self, chans=None, t=0.0):
        """{joint name: world position in metres}."""
        world = self.world(chans, t)
        return {self.names[j]: world[j][:3, 3].copy() for j in self.joints()}


def world_rotation(matrix):
    m = np.asarray(matrix, dtype=float)[:3, :3]
    s = np.linalg.norm(m, axis=0)
    rot = m / np.where(s == 0, 1, s)[None, :]
    if np.linalg.det(rot) < 0:
        rot[:, 0] = -rot[:, 0]
    return mat_q(rot)
