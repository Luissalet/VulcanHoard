"""Animated assets for delivery: skeleton and clip inspection against an animated destination profile, and a verified
preparation (rename/trim clips, drop empty channels, drop bones without influence, normalise or limit weights,
resample to the destination frame rate with a measured error).

Integrity, not artistry: the report measures bones, influences, weights, clip names and durations, loop continuity
(first/last pose difference per bone), non-uniform bone scale and channels that move nothing. A GLB with three clips
does not prove the animations are good.

Every preparation writes a NEW GLB (the original is hashed before and after), re-inspects it from disk, and is read
back by an independent consumer (three.js GLTFLoader + AnimationMixer in Node, ``scripts/anim_reimport.mjs``) that
plays each clip to its end: clip names, durations and the final pose of every animated bone are compared with this
module's own evaluation. A receipt with both hashes, the operations, the resample errors and the reimport is stored.
"""
from __future__ import annotations

import copy
import json
import math
import re
import shutil
import struct
import subprocess
import threading
import time
import uuid
from pathlib import Path

import numpy as np
from pydantic import BaseModel, ConfigDict, Field

from .hoard_link import atomic

FILE_NAME = 'anim_targets.json'
ID_RE = re.compile(r'^[a-z0-9][a-z0-9-]{1,47}$')
COMPONENTS = {'SCALAR': 1, 'VEC2': 2, 'VEC3': 3, 'VEC4': 4, 'MAT4': 16}
DTYPES = {5120: np.int8, 5121: np.uint8, 5122: np.int16, 5123: np.uint16, 5125: np.uint32, 5126: np.float32}
NORMALISE = {5120: 127.0, 5121: 255.0, 5122: 32767.0, 5123: 65535.0}
PATH_SIZE = {'translation': 3, 'rotation': 4, 'scale': 3}
COMPRESSED = {'KHR_draco_mesh_compression', 'EXT_meshopt_compression', 'KHR_mesh_quantization'}
NODE_TIMEOUT_S = 120
_lock = threading.RLock()


# ---------- profiles ----------
class AnimRules(BaseModel):
    """Measurable rules of an animated destination. None = not checked."""
    model_config = ConfigDict(extra='forbid')

    accepted_formats: list[str] = Field(default_factory=lambda: ['glb'], description='Delivery formats; GLB/glTF are read.')
    max_bones: int | None = Field(None, ge=1, le=1024, description='Joints of the largest skin.')
    max_influences: int | None = Field(None, ge=1, le=16, description='Bones with a non-zero weight on any one vertex.')
    require_normalized_weights: bool = Field(True, description='Every skinned vertex has weights summing to 1 (tolerance 1e-3).')
    require_named_clips: bool = Field(True, description='Every clip has a unique, non-empty name.')
    required_clips: list[str] = Field(default_factory=list, description='Clip names that must exist.')
    loop_clips: list[str] = Field(default_factory=list, description="Clips expected to loop (names, or '*' for all): first and last pose must match.")
    max_loop_error_deg: float | None = Field(None, gt=0, description='Largest first/last rotation difference of any bone in a looping clip (degrees).')
    max_loop_error_mm: float | None = Field(None, gt=0, description='Largest first/last translation difference of any bone in a looping clip (mm; glTF metres x 1000).')
    forbid_nonuniform_bone_scale: bool = Field(False, description='No joint carries a non-uniform scale (rest or animated).')
    forbid_empty_channels: bool = Field(False, description='No channel holds the rest value constant (it moves nothing).')
    forbid_unused_bones: bool = Field(False, description='No joint without any vertex influence.')
    target_fps: float | None = Field(None, gt=0, le=240, description='Key rate the runtime samples at; denser clips are resampled.')
    max_resample_error_deg: float = Field(0.5, gt=0, description='Allowed rotation error of a resampled clip (degrees).')
    max_resample_error_mm: float = Field(0.5, gt=0, description='Allowed translation error of a resampled clip (mm).')
    max_triangles: int | None = Field(None, ge=1)
    max_textures: int | None = Field(None, ge=0)
    max_texture_px: int | None = Field(None, ge=1)
    max_morph_targets: int | None = Field(None, ge=0, le=256, description='Morph targets (blend shapes) of the mesh with the most.')
    forbid_empty_morph_targets: bool = Field(False, description='No morph target that moves no vertex (under 0.001 mm).')
    max_root_drift_mm: float | None = Field(None, gt=0, description='Largest horizontal displacement of the root over a looping clip '
                                                                     '(in-place clips for runtimes that drive root motion themselves).')


class AnimProfile(BaseModel):
    model_config = ConfigDict(extra='forbid')

    id: str = Field(..., pattern=ID_RE.pattern)
    name: str = Field(..., min_length=1, max_length=80)
    description: str = Field('', max_length=600)
    rules: AnimRules = Field(default_factory=AnimRules)


BUILTINS = {
    'web-animated': {'id': 'web-animated', 'name': 'Web animado', 'description': 'Visor web con three.js o model-viewer: GLB con esqueleto ligero, '
                     'clips con nombre, bucles cerrados y 30 fps.',
                     'rules': {'max_bones': 64, 'max_influences': 4, 'require_normalized_weights': True, 'require_named_clips': True,
                               'loop_clips': ['idle', 'walk', 'run'], 'max_loop_error_deg': 0.5, 'max_loop_error_mm': 0.5,
                               'forbid_nonuniform_bone_scale': True, 'forbid_empty_channels': True, 'target_fps': 30,
                               'max_triangles': 50_000, 'max_textures': 4, 'max_texture_px': 2048}},
    'game-runtime': {'id': 'game-runtime', 'name': 'Motor de juego', 'description': 'Personaje para un motor en tiempo real: idle obligatorio, '
                     'bucles exactos, sin huesos sin influencia ni canales vacíos, 4 influencias por vértice y 30 fps.',
                     'rules': {'max_bones': 75, 'max_influences': 4, 'require_normalized_weights': True, 'require_named_clips': True,
                               'required_clips': ['idle'], 'loop_clips': ['idle', 'walk', 'run'], 'max_loop_error_deg': 0.1,
                               'max_loop_error_mm': 0.1, 'forbid_nonuniform_bone_scale': True, 'forbid_empty_channels': True,
                               'forbid_unused_bones': True, 'target_fps': 30, 'max_resample_error_deg': 0.25, 'max_resample_error_mm': 0.25,
                               'max_triangles': 100_000, 'max_textures': 8, 'max_texture_px': 4096, 'forbid_empty_morph_targets': True}},
}


def _store(data_dir):
    raw = atomic.read_json(Path(data_dir) / FILE_NAME, default=None)
    return raw if isinstance(raw, dict) and isinstance(raw.get('profiles'), dict) else {'profiles': {}}


def list_profiles(data_dir):
    with _lock:
        stored = _store(data_dir)['profiles']
        out = [{**copy.deepcopy(stored.get(k) or v), 'builtin': True, 'edited': k in stored} for k, v in BUILTINS.items()]
        out += [{**row, 'builtin': False, 'edited': False} for k, row in sorted(stored.items()) if k not in BUILTINS]
    for row in out:
        row['rules'] = AnimRules.model_validate(row['rules']).model_dump()
    return out


def get_profile(data_dir, profile_id):
    for row in list_profiles(data_dir):
        if row['id'] == profile_id:
            return row
    raise LookupError(f"Unknown animated profile {profile_id!r} (known: {', '.join(r['id'] for r in list_profiles(data_dir))})")


def save_profile(data_dir, data, clone_from=None):
    data = dict(data or {})
    if clone_from:
        base = {k: v for k, v in get_profile(data_dir, clone_from).items() if k not in ('builtin', 'edited')}
        data = {**base, **data, 'rules': {**base['rules'], **(data.get('rules') or {})}}
        if data.get('id') in (None, '', clone_from):
            raise ValueError('A clone needs a new id')
    else:
        try:
            current = {k: v for k, v in get_profile(data_dir, data.get('id', '')).items() if k not in ('builtin', 'edited')}
        except LookupError:
            current = {}
        data = {**current, **data, 'rules': {**current.get('rules', {}), **(data.get('rules') or {})}}
    clean = AnimProfile.model_validate(data).model_dump()
    with _lock:
        stored = _store(data_dir)
        stored['profiles'][clean['id']] = clean
        atomic.write_json_atomic(Path(data_dir) / FILE_NAME, stored)
    return get_profile(data_dir, clean['id'])


# ---------- glTF document: decode accessors, add new ones, repack and write a GLB ----------
class Doc:
    def __init__(self, gltf: dict, binary: bytes, base: Path | None = None):
        self.gltf, self.bin, self.base = gltf, binary, base

    @classmethod
    def load(cls, path):
        from .asset_inspection import read_gltf
        gltf, binary = read_gltf(Path(path))
        base = Path(path).parent
        if Path(path).suffix.lower() == '.gltf':  # external or data: buffers become one in-memory binary
            buffers = []
            for buf in gltf.get('buffers', []):
                uri = buf.get('uri') or ''
                if uri.startswith('data:'):
                    import base64
                    buffers.append(base64.b64decode(uri.split(',', 1)[1]))
                else:
                    buffers.append((base / uri).read_bytes())
            if len(buffers) > 1:
                raise ValueError('glTF files with several buffers are not supported; export a GLB')
            binary = buffers[0] if buffers else b''
        return cls(gltf, binary, base)

    def compressed(self):
        return sorted(COMPRESSED & set(self.gltf.get('extensionsUsed', [])))

    def raw(self, index):
        """(element bytes packed tightly, accessor) of an accessor."""
        acc = self.gltf['accessors'][index]
        if '_data' in acc:
            return acc['_data'], acc
        dtype = np.dtype(DTYPES[acc['componentType']])
        width = COMPONENTS[acc['type']] * dtype.itemsize
        if acc.get('sparse'):  # the dense base (or zeros) with the sparse values written in; repacking writes it dense
            base = self.raw_dense(acc, width) if 'bufferView' in acc else bytes(acc['count'] * width)
            array = np.frombuffer(base, dtype=dtype).reshape(acc['count'], -1).copy()
            sparse = acc['sparse']
            n = int(sparse['count'])
            iview = self.gltf['bufferViews'][sparse['indices']['bufferView']]
            istart = iview.get('byteOffset', 0) + sparse['indices'].get('byteOffset', 0)
            itype = np.dtype(DTYPES[sparse['indices']['componentType']])
            indices = np.frombuffer(self.bin[istart:istart + n * itype.itemsize], dtype=itype).astype(np.int64)
            vview = self.gltf['bufferViews'][sparse['values']['bufferView']]
            vstart = vview.get('byteOffset', 0) + sparse['values'].get('byteOffset', 0)
            array[indices] = np.frombuffer(self.bin[vstart:vstart + n * width], dtype=dtype).reshape(n, -1)
            return array.tobytes(), acc
        if 'bufferView' not in acc:
            return bytes(acc['count'] * width), acc
        return self.raw_dense(acc, width), acc

    def raw_dense(self, acc, width):
        """The bytes of a buffer-view-backed accessor, packed tightly."""
        view = self.gltf['bufferViews'][acc['bufferView']]
        start = view.get('byteOffset', 0) + acc.get('byteOffset', 0)
        stride = view.get('byteStride') or width
        if stride == width:
            return self.bin[start:start + width * acc['count']]
        return b''.join(self.bin[start + i * stride: start + i * stride + width] for i in range(acc['count']))

    def read(self, index):
        """An accessor as float64 rows (normalised integers scaled to [0, 1] / [-1, 1])."""
        data, acc = self.raw(index)
        array = np.frombuffer(data, dtype=DTYPES[acc['componentType']]).astype(np.float64)
        if acc.get('normalized') and acc['componentType'] in NORMALISE:
            array = np.maximum(array / NORMALISE[acc['componentType']], -1.0)
        size = COMPONENTS[acc['type']]
        return array.reshape(-1, size) if size > 1 else array

    def add(self, array, kind, component=5126, minmax=False):
        array = np.ascontiguousarray(np.asarray(array).astype(DTYPES[component]))
        acc = {'componentType': component, 'count': int(len(array)), 'type': kind, '_data': array.tobytes()}
        if minmax:
            flat = array.reshape(len(array), -1)
            acc['min'] = [float(v) for v in flat.min(axis=0)]
            acc['max'] = [float(v) for v in flat.max(axis=0)]
        self.gltf.setdefault('accessors', []).append(acc)
        return len(self.gltf['accessors']) - 1

    def referenced_accessors(self):
        used = set()
        for mesh in self.gltf.get('meshes', []):
            for prim in mesh.get('primitives', []):
                used.update(prim.get('attributes', {}).values())
                if 'indices' in prim:
                    used.add(prim['indices'])
                for target in prim.get('targets', []):
                    used.update(target.values())
        for skin in self.gltf.get('skins', []):
            if 'inverseBindMatrices' in skin:
                used.add(skin['inverseBindMatrices'])
        for anim in self.gltf.get('animations', []):
            for sampler in anim.get('samplers', []):
                used.update((sampler['input'], sampler['output']))
        return used

    def to_glb(self):
        """Repack every referenced accessor and embedded image into one tight binary buffer (unreferenced data is dropped)."""
        gltf = copy.deepcopy(self.gltf)
        used = sorted(self.referenced_accessors())
        remap = {old: new for new, old in enumerate(used)}
        chunks, views = [], []

        def push(data, target=None):
            offset = sum(len(c) for c in chunks)
            pad = (-offset) % 4
            if pad:
                chunks.append(b'\x00' * pad)
                offset += pad
            chunks.append(bytes(data))
            view = {'buffer': 0, 'byteOffset': offset, 'byteLength': len(data)}
            if target:
                view['target'] = target
            views.append(view)
            return len(views) - 1

        accessors = []
        for old in used:
            data, acc = self.raw(old)
            acc = {k: v for k, v in acc.items() if k not in ('_data', 'bufferView', 'byteOffset', 'sparse')}
            acc['bufferView'] = push(data)
            accessors.append(acc)
        for image in gltf.get('images', []):
            if 'bufferView' in image:
                view = self.gltf['bufferViews'][image['bufferView']]
                start = view.get('byteOffset', 0)
                image['bufferView'] = push(self.bin[start:start + view['byteLength']])
            elif image.get('uri') and not image['uri'].startswith('data:') and self.base and (self.base / image['uri']).is_file():
                uri = image.pop('uri')  # an external texture of a .gltf is embedded so the GLB stands alone
                image['mimeType'] = 'image/png' if uri.lower().endswith('.png') else 'image/jpeg'
                image['bufferView'] = push((self.base / uri).read_bytes())
        for mesh in gltf.get('meshes', []):
            for prim in mesh.get('primitives', []):
                prim['attributes'] = {k: remap[v] for k, v in prim.get('attributes', {}).items()}
                if 'indices' in prim:
                    prim['indices'] = remap[prim['indices']]
                prim['targets'] = [{k: remap[v] for k, v in t.items()} for t in prim.get('targets', [])] or None
                if prim['targets'] is None:
                    del prim['targets']
        for skin in gltf.get('skins', []):
            if 'inverseBindMatrices' in skin:
                skin['inverseBindMatrices'] = remap[skin['inverseBindMatrices']]
        for anim in gltf.get('animations', []):
            for sampler in anim.get('samplers', []):
                sampler['input'], sampler['output'] = remap[sampler['input']], remap[sampler['output']]
        binary = b''.join(chunks)
        binary += b'\x00' * ((-len(binary)) % 4)
        gltf['accessors'] = accessors
        gltf['bufferViews'] = views
        gltf['buffers'] = [{'byteLength': len(binary)}]
        text = json.dumps(gltf, separators=(',', ':'), ensure_ascii=False).encode('utf-8')
        text += b' ' * ((-len(text)) % 4)
        total = 12 + 8 + len(text) + 8 + len(binary)
        return (struct.pack('<4sII', b'glTF', 2, total) + struct.pack('<I4s', len(text), b'JSON') + text
                + struct.pack('<I4s', len(binary), b'BIN\x00') + binary)


# ---------- sampling ----------
def _qnorm(q):
    q = np.asarray(q, dtype=np.float64)
    n = np.linalg.norm(q, axis=-1, keepdims=True)
    return q / np.where(n == 0, 1, n)


def quat_angle_deg(a, b):
    """Angle between two rotations (q and -q are the same rotation)."""
    dot = abs(float(np.dot(_qnorm(a), _qnorm(b))))
    return math.degrees(2 * math.acos(min(1.0, dot)))


def _slerp(a, b, t):
    a, b = _qnorm(a), _qnorm(b)
    dot = float(np.dot(a, b))
    if dot < 0:
        b, dot = -b, -dot
    if dot > 0.9995:
        return _qnorm(a + t * (b - a))
    theta = math.acos(dot)
    return (math.sin((1 - t) * theta) * a + math.sin(t * theta) * b) / math.sin(theta)


class Channel:
    """One animation channel with its sampler data, evaluable at any time (clamped at the ends, like the spec)."""

    def __init__(self, doc, anim_index, channel_index):
        anim = doc.gltf['animations'][anim_index]
        channel = anim['channels'][channel_index]
        sampler = anim['samplers'][channel['sampler']]
        self.anim, self.index = anim_index, channel_index
        self.node = channel.get('target', {}).get('node')
        self.path = channel.get('target', {}).get('path')
        self.interpolation = sampler.get('interpolation', 'LINEAR')
        self.times = np.asarray(doc.read(sampler['input']), dtype=np.float64).reshape(-1)
        out = doc.read(sampler['output'])
        per_key = 3 if self.interpolation == 'CUBICSPLINE' else 1
        size = PATH_SIZE.get(self.path) or max(1, out.size // max(1, len(self.times) * per_key))  # morph weights: one per target
        out = np.asarray(out, dtype=np.float64).reshape(-1, size)
        if self.interpolation == 'CUBICSPLINE':
            out = out.reshape(-1, 3, size)  # in-tangent, value, out-tangent per key
            self.tangents_in, self.values, self.tangents_out = out[:, 0], out[:, 1], out[:, 2]
        else:
            self.values = out
        if len(self.values) != len(self.times):
            raise ValueError(f'animation {anim_index} channel {channel_index}: {len(self.times)} times but {len(self.values)} values')

    def at(self, t):
        times, values = self.times, self.values
        if t <= times[0] or len(times) == 1:
            value = values[0]
        elif t >= times[-1]:
            value = values[-1]
        else:
            k = int(np.searchsorted(times, t, side='right') - 1)
            t0, t1 = times[k], times[k + 1]
            u = (t - t0) / (t1 - t0) if t1 > t0 else 0.0
            if self.interpolation == 'STEP':
                value = values[k]
            elif self.interpolation == 'CUBICSPLINE':
                dt = t1 - t0
                u2, u3 = u * u, u * u * u
                value = ((2 * u3 - 3 * u2 + 1) * values[k] + (u3 - 2 * u2 + u) * dt * self.tangents_out[k]
                         + (-2 * u3 + 3 * u2) * values[k + 1] + (u3 - u2) * dt * self.tangents_in[k + 1])
            elif self.path == 'rotation':
                value = _slerp(values[k], values[k + 1], u)
            else:
                value = values[k] + u * (values[k + 1] - values[k])
        value = np.asarray(value, dtype=np.float64)
        return _qnorm(value) if self.path == 'rotation' else value

    def difference(self, a, b):
        """(degrees, mm, scale) difference between two values of this channel; glTF lengths are metres."""
        if self.path == 'rotation':
            return quat_angle_deg(a, b), 0.0, 0.0
        if self.path == 'translation':
            return 0.0, float(np.linalg.norm(np.asarray(a) - np.asarray(b))) * 1000.0, 0.0
        return 0.0, 0.0, float(np.max(np.abs(np.asarray(a) - np.asarray(b))))


def rest_value(node, path):
    if 'matrix' in node:
        return None
    return np.asarray(node.get(path, {'translation': [0, 0, 0], 'rotation': [0, 0, 0, 1], 'scale': [1, 1, 1]}.get(path)), dtype=np.float64)


def same_value(channel, a, b):
    """Two values of a channel are the same pose (1e-4 deg, 1e-3 mm, 1e-7 scale): float32 noise is not motion."""
    deg, mm, sc = channel.difference(a, b)
    return deg < 1e-4 and mm < 1e-3 and sc < 1e-6


def _constant(channel):
    first = channel.values[0]
    worst = [channel.difference(first, v) for v in channel.values[1:]] or [(0.0, 0.0, 0.0)]
    deg, mm, sc = (max(w[i] for w in worst) for i in range(3))
    if channel.interpolation == 'CUBICSPLINE' and (np.abs(channel.tangents_in).max() > 1e-9 or np.abs(channel.tangents_out).max() > 1e-9):
        return False
    return deg < 1e-4 and mm < 1e-3 and sc < 1e-7


# ---------- facts ----------
def _node_name(gltf, index):
    if index is None:
        return None
    return gltf['nodes'][index].get('name') or f'node_{index}'


def _parents(gltf):
    parents = {}
    for i, node in enumerate(gltf.get('nodes', [])):
        for child in node.get('children', []):
            parents[child] = i
    return parents


def _subtree(gltf, index):
    out, stack = [], [index]
    while stack:
        i = stack.pop()
        out.append(i)
        stack.extend(gltf['nodes'][i].get('children', []))
    return out


def skinned_sets(doc, primitive):
    """(joint indices, weights) per vertex over every JOINTS_n/WEIGHTS_n set of a primitive, or None."""
    attrs = primitive.get('attributes', {})
    sets = sorted(int(k.split('_')[1]) for k in attrs if k.startswith('JOINTS_') and f"WEIGHTS_{k.split('_')[1]}" in attrs)
    if not sets:
        return None
    joints = np.hstack([doc.read(attrs[f'JOINTS_{n}']).reshape(-1, 4) for n in sets]).astype(np.int64)
    weights = np.hstack([doc.read(attrs[f'WEIGHTS_{n}']).reshape(-1, 4) for n in sets])
    return joints, weights, sets


def skeleton_facts(doc):
    gltf = doc.gltf
    skins = gltf.get('skins', [])
    out = {'skins': [], 'max_bones': max((len(s.get('joints', [])) for s in skins), default=0), 'max_influences': 0,
           'max_weight_sum_error': 0.0, 'skinned_vertices': 0, 'unused_bones': [], 'removable_bones': [], 'nonuniform_scale_bones': [],
           'weights_readable': True}
    if not skins:
        return out
    used = [set() for _ in skins]
    for node in gltf.get('nodes', []):
        if 'mesh' not in node or 'skin' not in node:
            continue
        for prim in gltf['meshes'][node['mesh']].get('primitives', []):
            try:
                found = skinned_sets(doc, prim)
            except (ValueError, KeyError):
                out['weights_readable'] = False
                continue
            if not found:
                continue
            joints, weights, _ = found
            active = weights > 1e-6
            out['skinned_vertices'] += int(len(weights))
            out['max_influences'] = max(out['max_influences'], int(active.sum(axis=1).max(initial=0)))
            sums = weights.sum(axis=1)
            out['max_weight_sum_error'] = max(out['max_weight_sum_error'], float(np.abs(sums - 1).max(initial=0)))
            used[node['skin']].update(int(j) for j in np.unique(joints[active]))
    joint_nodes = set()
    for s, skin in enumerate(skins):
        joints = skin.get('joints', [])
        joint_nodes.update(joints)
        unused = [joints[j] for j in range(len(joints)) if j not in used[s]]
        out['skins'].append({'index': s, 'name': skin.get('name'), 'bones': len(joints), 'influential_bones': len(used[s]),
                             'unused': [_node_name(gltf, n) for n in unused]})
        out['unused_bones'] += [{'node': n, 'name': _node_name(gltf, n), 'skin': s} for n in unused]
    influential = {skin.get('joints', [])[j] for s, skin in enumerate(skins) for j in used[s] if j < len(skin.get('joints', []))}
    unused_nodes = {b['node'] for b in out['unused_bones']} - influential  # unused in one skin but weighted in another: not removable
    for bone in out['unused_bones']:
        tree = _subtree(gltf, bone['node'])
        clean = all(i not in influential and 'mesh' not in gltf['nodes'][i] and 'camera' not in gltf['nodes'][i] for i in tree)
        if clean and all(i in unused_nodes or i not in joint_nodes for i in tree) and bone['node'] not in {b['node'] for b in out['removable_bones']}:
            out['removable_bones'].append({**bone, 'subtree': [_node_name(gltf, i) for i in tree]})
    animated_scale = {}
    for a, anim in enumerate(gltf.get('animations', [])):
        for c, channel in enumerate(anim.get('channels', [])):
            target = channel.get('target', {})
            if target.get('path') == 'scale' and target.get('node') in joint_nodes:
                values = np.abs(Channel(doc, a, c).values)
                ratio = float((values.max(axis=1) / np.maximum(values.min(axis=1), 1e-12)).max()) if len(values) else 1.0
                animated_scale[target['node']] = max(animated_scale.get(target['node'], 1.0), ratio)
    for n in sorted(joint_nodes):
        scale = gltf['nodes'][n].get('scale')
        ratio = max(abs(s) for s in scale) / max(min(abs(s) for s in scale), 1e-12) if scale else 1.0  # a mirror (-1) is not a stretch
        ratio = max(ratio, animated_scale.get(n, 1.0))
        if ratio > 1 + 1e-4:
            out['nonuniform_scale_bones'].append({'node': n, 'name': _node_name(gltf, n), 'max_ratio': round(ratio, 6)})
    return out


def loops_expected(rules, name):
    wanted = rules.get('loop_clips') or []
    return '*' in wanted or (name or '') in wanted


def clip_facts(doc, rules=None):
    gltf = doc.gltf
    clips = []
    for a, anim in enumerate(gltf.get('animations', [])):
        channels = [Channel(doc, a, c) for c in range(len(anim.get('channels', [])))]
        start = min((float(ch.times[0]) for ch in channels if len(ch.times)), default=0.0)
        end = max((float(ch.times[-1]) for ch in channels if len(ch.times)), default=0.0)
        rows, loop = [], {'deg': 0.0, 'mm': 0.0, 'scale': 0.0, 'worst_bone': None}
        rate = 0.0
        for ch in channels:
            rest = rest_value(gltf['nodes'][ch.node], ch.path) if ch.node is not None and ch.path in PATH_SIZE else None
            constant = _constant(ch)
            matches_rest = bool(constant and rest is not None and same_value(ch, ch.values[0], rest))
            span = float(ch.times[-1] - ch.times[0]) if len(ch.times) > 1 else 0.0
            if span > 0:
                rate = max(rate, (len(ch.times) - 1) / span)
            deg, mm, sc = ch.difference(ch.at(start), ch.at(end)) if ch.path in PATH_SIZE else (0.0, 0.0, 0.0)
            if deg > loop['deg'] or mm > loop['mm']:
                loop['worst_bone'] = _node_name(gltf, ch.node)
            loop['deg'], loop['mm'], loop['scale'] = max(loop['deg'], deg), max(loop['mm'], mm), max(loop['scale'], sc)
            rows.append({'channel': ch.index, 'node': ch.node, 'bone': _node_name(gltf, ch.node), 'path': ch.path, 'keys': int(len(ch.times)),
                         'interpolation': ch.interpolation, 'constant': bool(constant), 'empty': matches_rest,
                         'loop_deg': round(deg, 6), 'loop_mm': round(mm, 6)})
        name = anim.get('name')
        clips.append({'index': a, 'name': name, 'duration_s': round(end - start, 6), 'start_s': start, 'end_s': end, 'channels': len(channels),
                      'key_rate_fps': round(rate, 3), 'empty_channels': sum(r['empty'] for r in rows),
                      'loop_expected': loops_expected(rules or {}, name), 'loop_error_deg': round(loop['deg'], 6),
                      'loop_error_mm': round(loop['mm'], 6), 'loop_worst_bone': loop['worst_bone'], 'channel_rows': rows})
    return clips


def _weight_channels(doc):
    """(clip index, Channel, mesh index) of every morph weight channel."""
    gltf = doc.gltf
    node_mesh = {i: n['mesh'] for i, n in enumerate(gltf.get('nodes', [])) if 'mesh' in n}
    for a, anim in enumerate(gltf.get('animations', [])):
        for c, channel in enumerate(anim.get('channels', [])):
            target = channel.get('target', {})
            if target.get('path') == 'weights' and target.get('node') in node_mesh:
                yield a, Channel(doc, a, c), node_mesh[target['node']]


def morph_facts(doc):
    """Morph targets per mesh: name, largest vertex displacement (mm), default weight, clips that drive it, empty or not."""
    gltf = doc.gltf
    animated = {}
    for a, ch, mesh in _weight_channels(doc):
        values = np.asarray(ch.values).reshape(len(ch.times), -1)
        for k in range(values.shape[1]):
            if np.abs(values[:, k]).max(initial=0) > 1e-6:
                animated.setdefault((mesh, k), set()).add(gltf['animations'][a].get('name') or f'clip_{a}')
    meshes = []
    for m, mesh in enumerate(gltf.get('meshes', [])):
        prims = mesh.get('primitives', [])
        count = max((len(p.get('targets', [])) for p in prims), default=0)
        if not count:
            continue
        names = (mesh.get('extras') or {}).get('targetNames') or []
        weights = mesh.get('weights') or []
        rows = []
        for k in range(count):
            moved, readable = 0.0, True
            for prim in prims:
                targets = prim.get('targets', [])
                if k < len(targets) and 'POSITION' in targets[k]:
                    try:
                        delta = doc.read(targets[k]['POSITION']).reshape(-1, 3)
                        moved = max(moved, float(np.linalg.norm(delta, axis=1).max(initial=0)))
                    except (ValueError, KeyError):
                        readable = False
            rows.append({'mesh': m, 'mesh_name': mesh.get('name'), 'index': k, 'name': names[k] if k < len(names) else f'target_{k}',
                         'max_displacement_mm': round(moved * 1000, 6), 'default_weight': float(weights[k]) if k < len(weights) else 0.0,
                         'animated_in': sorted(animated.get((m, k), [])), 'empty': readable and moved * 1000 < 1e-3})
        meshes.append({'mesh': m, 'name': mesh.get('name'), 'targets': count, 'rows': rows})
    return {'meshes': meshes, 'max_targets': max((x['targets'] for x in meshes), default=0),
            'empty': [{k: r[k] for k in ('mesh', 'mesh_name', 'index', 'name')} for x in meshes for r in x['rows'] if r['empty']]}


def _depth(rig, node):
    d = 0
    while node in rig.parent:
        node, d = rig.parent[node], d + 1
    return d


def root_motion_facts(doc):
    """Per clip: the top-most translated joint (or joint ancestor) and how far it travels, in the world's horizontal plane
    (glTF is Y-up) and vertically, measured through its parent's rest transform."""
    from .asset_animation_rig import Rig
    rig = Rig(doc)
    joints = set(rig.joints())
    holders = set(joints)
    for j in joints:  # ancestors of joints (an armature node can carry the motion)
        while j in rig.parent:
            j = rig.parent[j]
            holders.add(j)
    rest_world = rig.world()
    out = []
    for a, anim in enumerate(doc.gltf.get('animations', [])):
        chans = rig.channels(a)
        moving = [n for (n, p) in chans if p == 'translation' and (n in holders or not joints)]
        row = {'clip': a, 'name': anim.get('name'), 'node': None}
        if moving:
            root = min(moving, key=lambda n: (_depth(rig, n), n))
            ch = chans[(root, 'translation')]
            parent = rig.parent.get(root)
            frame = rest_world[parent] if parent is not None else np.eye(4)
            t0, t1 = rig.span(a)
            grid = np.unique(np.concatenate([ch.times, np.linspace(t0, t1, 61)]))
            world = np.array([(frame @ np.append(ch.at(t), 1.0))[:3] for t in grid])
            delta = world[-1] - world[0]
            horizontal = np.array([delta[0], 0.0, delta[2]])
            span = max(t1 - t0, 1e-9)
            row.update(node=root, bone=rig.names[root], horizontal_mm=float(np.linalg.norm(horizontal)) * 1000, vertical_mm=float(delta[1]) * 1000,
                       path_mm=float(np.linalg.norm(np.diff(world, axis=0), axis=1).sum()) * 1000,
                       speed_mps=float(np.linalg.norm(horizontal)) / span, direction=[round(float(v), 6) for v in
                                                                                       (horizontal / max(np.linalg.norm(horizontal), 1e-12))])
        out.append(row)
    return out


# ---------- report ----------
def inspect_doc(doc, profile, file_info):
    from .asset_inspection import gltf_facts
    rules = profile['rules']
    checks = []

    def check(rule, limit, observed, ok, note=''):
        checks.append({'rule': rule, 'limit': limit, 'observed': observed, 'status': 'not_evaluated' if ok is None else 'pass' if ok else 'fail', 'note': note})

    compressed = doc.compressed()
    skeleton = skeleton_facts(doc) if not compressed else None
    clips = clip_facts(doc, rules)
    fmt = file_info['format']
    check('accepted_formats', rules['accepted_formats'], fmt, fmt in rules['accepted_formats'] if rules['accepted_formats'] else True)
    note_c = f"compressed attributes ({', '.join(compressed)}) are not decoded" if compressed else ''
    if rules.get('max_bones') is not None:
        bones = max((len(s.get('joints', [])) for s in doc.gltf.get('skins', [])), default=0)
        check('max_bones', rules['max_bones'], bones, bones <= rules['max_bones'])
    if rules.get('max_influences') is not None:
        check('max_influences', rules['max_influences'], skeleton and skeleton['max_influences'],
              None if not skeleton or not skeleton['weights_readable'] else skeleton['max_influences'] <= rules['max_influences'], note_c)
    if rules.get('require_normalized_weights'):
        error = skeleton and skeleton['max_weight_sum_error']
        check('require_normalized_weights', 1e-3, error, None if not skeleton or not skeleton['weights_readable'] else error <= 1e-3, note_c)
    if rules.get('forbid_unused_bones'):
        check('forbid_unused_bones', 0, skeleton and [b['name'] for b in skeleton['unused_bones']], None if not skeleton else not skeleton['unused_bones'], note_c)
    if rules.get('forbid_nonuniform_bone_scale'):
        bad = skeleton and skeleton['nonuniform_scale_bones']
        check('forbid_nonuniform_bone_scale', True, bad and [b['name'] for b in bad], None if not skeleton else not bad, note_c)
    names = [c['name'] for c in clips]
    if rules.get('require_named_clips'):
        missing = [c['index'] for c in clips if not c['name']]
        dupes = sorted({n for n in names if n and names.count(n) > 1})
        check('require_named_clips', True, {'unnamed': missing, 'duplicated': dupes}, not missing and not dupes)
    if rules.get('required_clips'):
        absent = [n for n in rules['required_clips'] if n not in names]
        check('required_clips', rules['required_clips'], names, not absent, f"missing: {', '.join(absent)}" if absent else '')
    looping = [c for c in clips if c['loop_expected']]
    if rules.get('max_loop_error_deg') is not None or rules.get('max_loop_error_mm') is not None:
        if not looping:
            check('loop_continuity', rules.get('loop_clips'), [], None, 'no clip is expected to loop')
        for c in looping:
            ok = ((rules.get('max_loop_error_deg') is None or c['loop_error_deg'] <= rules['max_loop_error_deg'])
                  and (rules.get('max_loop_error_mm') is None or c['loop_error_mm'] <= rules['max_loop_error_mm']))
            check('loop_continuity', {'clip': c['name'], 'deg': rules.get('max_loop_error_deg'), 'mm': rules.get('max_loop_error_mm')},
                  {'deg': c['loop_error_deg'], 'mm': c['loop_error_mm'], 'worst_bone': c['loop_worst_bone']}, ok)
    if rules.get('forbid_empty_channels'):
        empty = {c['name'] or f"#{c['index']}": c['empty_channels'] for c in clips if c['empty_channels']}
        check('forbid_empty_channels', 0, empty, not empty)
    if rules.get('target_fps'):
        dense = {c['name'] or f"#{c['index']}": c['key_rate_fps'] for c in clips if c['key_rate_fps'] > rules['target_fps'] * 1.001}
        check('target_fps', rules['target_fps'], dense or {c['name']: c['key_rate_fps'] for c in clips}, not dense)
    facts = gltf_facts(Path(file_info['path']))
    if rules.get('max_triangles') is not None:
        check('max_triangles', rules['max_triangles'], facts['triangles'], facts['triangles'] <= rules['max_triangles'])
    if rules.get('max_textures') is not None:
        check('max_textures', rules['max_textures'], facts['images'], facts['images'] <= rules['max_textures'])
    if rules.get('max_texture_px') is not None:
        sizes = [max(s) for s in facts['image_sizes'] if s]
        check('max_texture_px', rules['max_texture_px'], max(sizes, default=0),
              None if any(s is None for s in facts['image_sizes']) else max(sizes, default=0) <= rules['max_texture_px'])
    morph = morph_facts(doc) if not compressed else None
    if rules.get('max_morph_targets') is not None:
        check('max_morph_targets', rules['max_morph_targets'], morph and morph['max_targets'], None if morph is None else morph['max_targets'] <= rules['max_morph_targets'], note_c)
    if rules.get('forbid_empty_morph_targets'):
        check('forbid_empty_morph_targets', 0, morph and [e['name'] for e in morph['empty']], None if morph is None else not morph['empty'], note_c)
    root = root_motion_facts(doc)
    by_clip = {r['clip']: r for r in root}
    for c in clips:
        c['root_motion'] = by_clip.get(c['index'])
    if rules.get('max_root_drift_mm') is not None:
        drift = {c['name'] or f"#{c['index']}": round(c['root_motion']['horizontal_mm'], 3) for c in looping if (c.get('root_motion') or {}).get('node') is not None}
        check('max_root_drift_mm', rules['max_root_drift_mm'], drift, all(v <= rules['max_root_drift_mm'] for v in drift.values()))
    plan = plan_for(rules, skeleton, clips, checks, compressed, morph)
    statuses = [c['status'] for c in checks]
    return {'kind': 'draft', 'profile': {k: profile[k] for k in ('id', 'name', 'rules')}, 'file': file_info,
            'summary': {'pass': statuses.count('pass'), 'fail': statuses.count('fail'), 'not_evaluated': statuses.count('not_evaluated'),
                        'compliant': 'fail' not in statuses},
            'checks': checks, 'skeleton': skeleton, 'clips': clips, 'morph': morph, 'root_motion': root,
            'materials': {'materials': facts['materials'], 'textures': facts['images'], 'texture_sizes': facts['image_sizes'], 'triangles': facts['triangles']},
            **plan, 'limits': 'Measures integrity and continuity (bones, weights, names, loops, keys), not artistic quality.'}


def suggestions_for(rules, clips, checks):
    """Operations that change the motion itself, offered but applied only when asked (close_loops, root_in_place)."""
    out = []
    bad = [c for c in checks if c['rule'] == 'loop_continuity' and c['status'] == 'fail']
    if bad:
        out.append({'op': 'close_loops', 'why': 'loop_continuity', 'clips': [c['limit']['clip'] for c in bad],
                    'max_correction_deg': max(c['observed']['deg'] for c in bad), 'max_correction_mm': max(c['observed']['mm'] for c in bad),
                    'note': 'spreads each channel\'s last-minus-first difference linearly over the clip so the last pose equals the first'})
    drifting = [c for c in clips if c['loop_expected'] and (c.get('root_motion') or {}).get('node') is not None and c['root_motion']['horizontal_mm'] > 1.0]
    limit = rules.get('max_root_drift_mm')
    if drifting:
        out.append({'op': 'root_in_place', 'why': 'max_root_drift_mm' if limit is not None else 'root_motion',
                    'clips': [c['name'] or c['index'] for c in drifting if limit is None or c['root_motion']['horizontal_mm'] > limit] or
                    [c['name'] or c['index'] for c in drifting],
                    'note': 'removes the steady horizontal travel of the root (kept as root_motion in the clip extras); sway stays'})
    return out


def plan_for(rules, skeleton, clips, checks, compressed, morph=None):
    failed = {c['rule'] for c in checks if c['status'] == 'fail'}
    operations, manual = [], []
    if compressed:
        manual.append(f"decompress {', '.join(compressed)} before preparing (gltf-transform or Blender); this app does not rewrite compressed data")
        return {'operations': [], 'manual': manual, 'suggested': []}
    if morph and morph['empty'] and 'forbid_empty_morph_targets' in failed:
        operations.append({'op': 'drop_empty_morph_targets', 'why': 'forbid_empty_morph_targets', 'targets': [e['name'] for e in morph['empty']]})
    if 'max_morph_targets' in failed:
        manual.append('reduce the morph targets (blend shapes) in the modelling tool; only targets that move nothing are removed here')
    if any(not c['name'] for c in clips) or 'require_named_clips' in failed:
        operations.append({'op': 'rename_clips', 'why': 'require_named_clips', 'renames': {str(c['index']): c['name'] or f"clip_{c['index']}" for c in clips if not c['name']}})
    if any(c['empty_channels'] for c in clips):
        operations.append({'op': 'drop_empty_channels', 'why': 'forbid_empty_channels', 'channels': sum(c['empty_channels'] for c in clips)})
    if skeleton and skeleton['removable_bones'] and ('forbid_unused_bones' in failed or 'max_bones' in failed):
        operations.append({'op': 'drop_unused_bones', 'why': 'forbid_unused_bones' if 'forbid_unused_bones' in failed else 'max_bones',
                           'bones': [b['name'] for b in skeleton['removable_bones']]})
    if 'max_influences' in failed:
        operations.append({'op': 'limit_influences', 'why': 'max_influences', 'keep': rules['max_influences']})
    elif 'require_normalized_weights' in failed:
        operations.append({'op': 'normalize_weights', 'why': 'require_normalized_weights'})
    if 'target_fps' in failed:
        operations.append({'op': 'resample', 'why': 'target_fps', 'fps': rules['target_fps'],
                           'clips': [c['name'] or c['index'] for c in clips if c['key_rate_fps'] > rules['target_fps'] * 1.001]})
    if 'loop_continuity' in failed:
        bad = [c for c in checks if c['rule'] == 'loop_continuity' and c['status'] == 'fail']
        manual.append('close the loop of ' + ', '.join(f"{c['limit']['clip']} (worst bone {c['observed']['worst_bone']}, {c['observed']['deg']:.3f} deg, "
                                                       f"{c['observed']['mm']:.3f} mm)" for c in bad) + ' in the animation tool, or trim the clip to a matching pose, '
                      'or close it here with close_loops (spreads the difference over the clip)')
    if 'max_root_drift_mm' in failed:
        manual.append('the root travels in a looping clip: make it in place here with root_in_place, or in the animation tool')
    if 'forbid_nonuniform_bone_scale' in failed:
        manual.append('apply or remove the non-uniform scale of the listed bones in the rig (runtimes skin it badly)')
    if 'required_clips' in failed:
        manual.append('add the missing clips, or rename an existing clip to the required name')
    if 'max_bones' in failed and not (skeleton and skeleton['removable_bones']):
        manual.append('reduce the rig: every joint has influence, so bones must be merged in the animation tool')
    for rule in ('max_triangles', 'max_textures', 'max_texture_px'):
        if rule in failed:
            manual.append(f'{rule}: reduce it in the animation tool; the geometry preparation (asset_prepare) rebuilds meshes without skins or clips')
    return {'operations': operations, 'manual': manual, 'suggested': suggestions_for(rules, clips, checks)}


# ---------- operations (each edits a Doc in memory and returns what it did) ----------
def _set_channel(doc, anim, channel_index, times, values, interpolation):
    """Give a channel its own new sampler with these keys (old samplers are compacted later)."""
    size = values.shape[1] if values.ndim > 1 else 1
    weights = anim['channels'][channel_index].get('target', {}).get('path') == 'weights'  # morph weights are always SCALAR
    kind = 'SCALAR' if weights else {1: 'SCALAR', 3: 'VEC3', 4: 'VEC4'}.get(size, 'SCALAR')
    out = values if kind != 'SCALAR' or size == 1 else values.reshape(-1)
    sampler = {'input': doc.add(np.asarray(times, dtype=np.float32), 'SCALAR', minmax=True),
               'output': doc.add(out, kind), 'interpolation': interpolation}
    anim['samplers'].append(sampler)
    anim['channels'][channel_index]['sampler'] = len(anim['samplers']) - 1


def _compact_samplers(anim):
    used = sorted({c['sampler'] for c in anim['channels']})
    remap = {old: new for new, old in enumerate(used)}
    anim['samplers'] = [anim['samplers'][i] for i in used]
    for c in anim['channels']:
        c['sampler'] = remap[c['sampler']]


def _clip_index(doc, key):
    for i, anim in enumerate(doc.gltf.get('animations', [])):
        if str(i) == str(key) or anim.get('name') == key:
            return i
    raise LookupError(f'No clip {key!r}')


def op_rename(doc, renames):
    done = []
    for key, new in (renames or {}).items():
        if not isinstance(new, str) or not new.strip() or len(new) > 80:
            raise ValueError(f'Clip name for {key!r} must be 1-80 characters')
        i = _clip_index(doc, key)
        done.append({'clip': i, 'from': doc.gltf['animations'][i].get('name'), 'to': new.strip()})
        doc.gltf['animations'][i]['name'] = new.strip()
    names = [a.get('name') for a in doc.gltf.get('animations', [])]
    if any(n and names.count(n) > 1 for n in names):
        raise ValueError('Renaming would leave two clips with the same name')
    return done


def _error(ch, new_times, new_values, interpolation, t0, t1, shift=0.0):
    """Largest difference between the original channel on [t0, t1] and its rewritten keys (sampled densely)."""
    probe = Channel.__new__(Channel)
    probe.path, probe.interpolation, probe.times, probe.values = ch.path, interpolation, np.asarray(new_times, dtype=np.float64), new_values
    inside = ch.times[(ch.times >= t0) & (ch.times <= t1)]
    grid = np.linspace(t0, t1, max(8, int((t1 - t0) * 240) + 1))
    times = np.unique(np.concatenate([grid, inside, [t0, t1]]))
    worst = [0.0, 0.0, 0.0]
    for t in times:
        diff = ch.difference(ch.at(t), probe.at(t - shift)) if ch.path in PATH_SIZE else (0.0, 0.0, float(np.max(np.abs(ch.at(t) - probe.at(t - shift)))))
        worst = [max(a, b) for a, b in zip(worst, diff)]
    return {'deg': worst[0], 'mm': worst[1], 'scale': worst[2]}


def op_retime(doc, clip, start=None, end=None, fps=None):
    """Trim a clip to [start, end] (shifted to start at 0) and/or resample it at fps; returns the measured errors."""
    i = _clip_index(doc, clip)
    anim = doc.gltf['animations'][i]
    channels = [Channel(doc, i, c) for c in range(len(anim['channels']))]
    first = min(float(ch.times[0]) for ch in channels)
    last = max(float(ch.times[-1]) for ch in channels)
    t0 = first if start is None else float(start)
    t1 = last if end is None else float(end)
    if not first - 1e-9 <= t0 < t1 <= last + 1e-9:
        raise ValueError(f'Clip {anim.get("name") or i}: trim must satisfy {first:g} <= start < end <= {last:g} s')
    worst = {'deg': 0.0, 'mm': 0.0, 'scale': 0.0}
    for ch in channels:
        if fps:
            n = max(1, int(math.ceil((t1 - t0) * fps - 1e-9)))
            times = np.unique(np.concatenate([t0 + np.arange(n) / fps, [t1]]))
            interpolation = 'STEP' if ch.interpolation == 'STEP' else 'LINEAR'
        elif ch.interpolation == 'CUBICSPLINE':
            times = np.unique(np.concatenate([np.linspace(t0, t1, max(2, int((t1 - t0) * 60) + 1)), [t1]]))
            interpolation = 'LINEAR'
        else:  # linear and step keys stay exactly where they are, with exact samples at the new ends
            inside = ch.times[(ch.times > t0 + 1e-9) & (ch.times < t1 - 1e-9)]
            times = np.concatenate([[t0], inside, [t1]])
            interpolation = ch.interpolation
        if _constant(ch):
            times = np.array([t0, t1])
        values = np.array([ch.at(t) for t in times])
        err = _error(ch, times - t0, values, interpolation, t0, t1, shift=t0)
        worst = {k: max(worst[k], err[k]) for k in worst}
        _set_channel(doc, anim, ch.index, times - t0, values, interpolation)
    _compact_samplers(anim)
    return {'clip': anim.get('name') or i, 'start_s': t0, 'end_s': t1, 'duration_s': t1 - t0, 'fps': fps,
            'error_deg': worst['deg'], 'error_mm': worst['mm'], 'error_scale': worst['scale']}


def op_drop_empty_channels(doc):
    dropped = []
    for i, anim in enumerate(doc.gltf.get('animations', [])):
        keep = []
        for c, channel in enumerate(anim['channels']):
            ch = Channel(doc, i, c)
            rest = rest_value(doc.gltf['nodes'][ch.node], ch.path) if ch.node is not None and ch.path in PATH_SIZE else None
            if _constant(ch) and rest is not None and same_value(ch, ch.values[0], rest):
                dropped.append({'clip': anim.get('name') or i, 'bone': _node_name(doc.gltf, ch.node), 'path': ch.path})
            else:
                keep.append(channel)
        if not keep:
            raise ValueError(f'Clip {anim.get("name") or i} would lose every channel; it moves nothing')
        anim['channels'] = keep
        _compact_samplers(anim)
    return dropped


def _skinned_primitives(doc):
    for node in doc.gltf.get('nodes', []):
        if 'mesh' in node and 'skin' in node:
            for prim in doc.gltf['meshes'][node['mesh']].get('primitives', []):
                yield node['skin'], prim


def _write_sets(doc, prim, joints, weights):
    """Replace every JOINTS_n/WEIGHTS_n of a primitive by sets of four (uint16 joints, float weights)."""
    attrs = prim['attributes']
    for key in [k for k in attrs if k.startswith(('JOINTS_', 'WEIGHTS_'))]:
        del attrs[key]
    width = max(4, int(math.ceil(joints.shape[1] / 4)) * 4)
    joints = np.pad(joints, ((0, 0), (0, width - joints.shape[1])))
    weights = np.pad(weights, ((0, 0), (0, width - weights.shape[1])))
    for n in range(width // 4):
        attrs[f'JOINTS_{n}'] = doc.add(joints[:, 4 * n:4 * n + 4], 'VEC4', 5123)
        attrs[f'WEIGHTS_{n}'] = doc.add(weights[:, 4 * n:4 * n + 4], 'VEC4', 5126)


def op_weights(doc, keep=None):
    """Normalise weights to sum 1; with keep, also keep only the strongest `keep` influences per vertex. Returns the change."""
    out = {'vertices': 0, 'max_weight_dropped': 0.0, 'max_sum_error_before': 0.0, 'keep': keep}
    seen = set()
    for _, prim in _skinned_primitives(doc):
        if id(prim) in seen:
            continue
        seen.add(id(prim))
        found = skinned_sets(doc, prim)
        if not found:
            continue
        joints, weights, _ = found
        out['vertices'] += len(weights)
        out['max_sum_error_before'] = max(out['max_sum_error_before'], float(np.abs(weights.sum(axis=1) - 1).max(initial=0)))
        if keep and weights.shape[1] > 0:
            order = np.argsort(-weights, axis=1)
            drop = order[:, keep:]
            rows = np.arange(len(weights))[:, None]
            out['max_weight_dropped'] = max(out['max_weight_dropped'], float(weights[rows, drop].max(initial=0)))
            take = order[:, :keep]
            joints, weights = joints[rows, take], weights[rows, take]
        sums = weights.sum(axis=1, keepdims=True)
        weights = np.where(sums > 0, weights / np.where(sums > 0, sums, 1), weights)
        _write_sets(doc, prim, joints, weights)
    return out


def op_drop_bones(doc, names):
    """Remove bones without influence (whole subtrees with no influential joint or mesh) from the skins and the scene."""
    gltf = doc.gltf
    facts = skeleton_facts(doc)
    removable = {b['name']: b for b in facts['removable_bones']}
    unknown = [n for n in names if n not in removable]
    if unknown:
        raise ValueError(f"Not removable (they have influence, meshes or influential children): {', '.join(unknown)}")
    remove = set()
    for name in names:
        remove.update(_subtree(gltf, removable[name]['node']))
    keep = [i for i in range(len(gltf['nodes'])) if i not in remove]
    new_index = {old: new for new, old in enumerate(keep)}
    # Per skin: which joint slots survive and the old-slot -> new-slot map (-1 = removed). Nothing is written yet.
    remaps, kept_slots = {}, {}
    for s, skin in enumerate(gltf.get('skins', [])):
        old_joints = skin['joints']
        kept = [j for j, node in enumerate(old_joints) if node not in remove]
        local = np.full(len(old_joints), -1, dtype=np.int64)
        local[kept] = np.arange(len(kept))
        remaps[s], kept_slots[s] = local, kept
    # A mesh can be shared by nodes with different skins, and JOINTS index into each node's own skin: every (mesh, remap)
    # pair is applied once. When the skins sharing a mesh need different remaps, the mesh is copied for all but the first.
    by_mesh = {}
    for n, node in enumerate(gltf['nodes']):
        if 'mesh' in node and 'skin' in node:
            by_mesh.setdefault(node['mesh'], []).append(n)
    split = []
    for m, users in sorted(by_mesh.items()):
        found_sets = [skinned_sets(doc, prim) for prim in gltf['meshes'][m].get('primitives', [])]
        reach = max((int(f[0].max(initial=-1)) + 1 for f in found_sets if f), default=0)  # slots the mesh's JOINTS can name
        groups = []  # [(remap over the reachable slots, [node indices])]
        for n in users:
            local = remaps[gltf['nodes'][n]['skin']]
            if reach > len(local):
                raise ValueError(f'Mesh {m} has joint indices beyond the skin of node {_node_name(gltf, n)}; nothing was changed')
            local = local[:reach]
            same = next((g for g in groups if np.array_equal(g[0], local)), None)
            if same:
                same[1].append(n)
            else:
                groups.append((local, [n]))
        targets = []
        for g, (local, nodes) in enumerate(groups):  # copies first, from the untouched mesh
            target = m
            if g > 0:  # a copy of the mesh whose JOINTS get this skin's remap; POSITION and the rest stay shared accessors
                gltf['meshes'].append(copy.deepcopy(gltf['meshes'][m]))
                target = len(gltf['meshes']) - 1
                gltf['meshes'][target]['name'] = f"{gltf['meshes'][m].get('name') or f'mesh_{m}'} (skin {gltf['nodes'][nodes[0]]['skin']})"
                for n in nodes:
                    gltf['nodes'][n]['mesh'] = target
                split.append({'mesh': m, 'copy': target, 'nodes': [_node_name(gltf, n) for n in nodes]})
            targets.append((target, local))
        for target, local in targets:
            if np.array_equal(local, np.arange(len(local))):
                continue  # this skin loses no joint: indices unchanged
            for prim in gltf['meshes'][target].get('primitives', []):
                found = skinned_sets(doc, prim)
                if not found:
                    continue
                joints, weights, _ = found
                if (joints >= len(local)).any():
                    raise ValueError(f'Mesh {m} has joint indices beyond its skin; nothing was changed')
                if (weights[local[joints] < 0] > 1e-6).any():
                    raise ValueError('A removed bone still has influence; nothing was changed')
                _write_sets(doc, prim, np.where(local[joints] < 0, 0, local[joints]), weights)
    for s, skin in enumerate(gltf.get('skins', [])):  # joint list and inverse bind matrices
        kept = kept_slots[s]
        if len(kept) == len(skin['joints']):
            continue
        if 'inverseBindMatrices' in skin:
            skin['inverseBindMatrices'] = doc.add(doc.read(skin['inverseBindMatrices']).reshape(-1, 16)[kept], 'MAT4')
        skin['joints'] = [skin['joints'][j] for j in kept]
    for node in gltf['nodes']:
        node['children'] = [new_index[c] for c in node.get('children', []) if c not in remove]
        if not node['children']:
            del node['children']
    for scene in gltf.get('scenes', []):
        scene['nodes'] = [new_index[n] for n in scene.get('nodes', []) if n not in remove]
    for skin in gltf.get('skins', []):
        skin['joints'] = [new_index[n] for n in skin['joints']]
        if 'skeleton' in skin:
            if skin['skeleton'] in remove:
                del skin['skeleton']
            else:
                skin['skeleton'] = new_index[skin['skeleton']]
    for anim in gltf.get('animations', []):
        anim['channels'] = [c for c in anim['channels'] if c.get('target', {}).get('node') not in remove]
        for c in anim['channels']:
            if 'node' in c.get('target', {}):
                c['target']['node'] = new_index[c['target']['node']]
        _compact_samplers(anim)
    gltf['nodes'] = [gltf['nodes'][i] for i in keep]
    return {'removed': [{'bone': n, 'subtree': removable[n]['subtree']} for n in names],
            'bones_left': max((len(s['joints']) for s in gltf.get('skins', [])), default=0), 'meshes_split': split}


def _dense_keys(ch, t0, t1):
    """Keys (times, values, interpolation) of a channel with exact samples at t0 and t1; cubic splines become linear at >= 60 fps."""
    if ch.interpolation == 'CUBICSPLINE':
        times = np.unique(np.concatenate([np.linspace(t0, t1, max(2, int((t1 - t0) * 60) + 1)), ch.times[(ch.times > t0) & (ch.times < t1)]]))
        interpolation = 'LINEAR'
    else:
        times = np.unique(np.concatenate([[t0], ch.times[(ch.times > t0 + 1e-9) & (ch.times < t1 - 1e-9)], [t1]]))
        interpolation = ch.interpolation
    return times, np.array([ch.at(t) for t in times]), interpolation


def op_close_loop(doc, clip):
    """Make the last pose equal the first: each channel's end-minus-start difference is spread linearly over the clip
    (rotations by a growing slerp of the correcting rotation). Returns the largest correction and the loop error after."""
    from .asset_animation_rig import qinv, qmul, slerp
    i = _clip_index(doc, clip)
    anim = doc.gltf['animations'][i]
    channels = [Channel(doc, i, c) for c in range(len(anim['channels']))]
    t0 = min(float(ch.times[0]) for ch in channels)
    t1 = max(float(ch.times[-1]) for ch in channels)
    if t1 - t0 <= 0:
        raise ValueError(f'Clip {anim.get("name") or i} has no duration to close')
    worst = {'deg': 0.0, 'mm': 0.0, 'weight': 0.0}
    changed = 0
    for ch in channels:
        first, last = ch.at(t0), ch.at(t1)
        if ch.path in PATH_SIZE and same_value(ch, first, last) or ch.path == 'weights' and np.abs(first - last).max(initial=0) < 1e-7:
            continue
        times, values, interpolation = _dense_keys(ch, t0, t1)
        u = (times - t0) / (t1 - t0)
        if ch.path == 'rotation':
            fix = qmul(first, qinv(last))
            new = np.array([qmul(slerp([0, 0, 0, 1], fix, w), v) for w, v in zip(u, values)])
            worst['deg'] = max(worst['deg'], quat_angle_deg(first, last))
        else:
            new = values + np.outer(u, first - last)
            if ch.path == 'translation':
                worst['mm'] = max(worst['mm'], float(np.linalg.norm(first - last)) * 1000)
            elif ch.path == 'weights':
                worst['weight'] = max(worst['weight'], float(np.abs(first - last).max()))
        _set_channel(doc, anim, ch.index, times, new, interpolation)
        changed += 1
    _compact_samplers(anim)
    after = next(c for c in clip_facts(doc, {'loop_clips': ['*']}) if c['index'] == i)
    return {'clip': anim.get('name') or i, 'channels_changed': changed, 'max_correction_deg': worst['deg'], 'max_correction_mm': worst['mm'],
            'max_correction_weight': worst['weight'], 'loop_error_deg_after': after['loop_error_deg'], 'loop_error_mm_after': after['loop_error_mm']}


def op_root_in_place(doc, clip):
    """Remove the steady horizontal travel of the clip's root (start-to-end, spread linearly) so it plays in place; the
    removed velocity is kept in the clip extras as root_motion for runtimes that drive the character themselves."""
    i = _clip_index(doc, clip)
    facts = root_motion_facts(doc)[i]
    if facts.get('node') is None:
        raise ValueError(f'Clip {doc.gltf["animations"][i].get("name") or i} moves no root (no translated joint)')
    from .asset_animation_rig import Rig
    rig = Rig(doc)
    anim = doc.gltf['animations'][i]
    root = facts['node']
    ch = next(Channel(doc, i, c) for c in range(len(anim['channels']))
              if anim['channels'][c].get('target', {}).get('node') == root and anim['channels'][c]['target'].get('path') == 'translation')
    parent = rig.parent.get(root)
    frame = rig.world()[parent] if parent is not None else np.eye(4)
    t0, t1 = rig.span(i)
    times, values, interpolation = _dense_keys(ch, t0, t1)
    world = np.array([(frame @ np.append(v, 1.0))[:3] for v in values])
    start, end = (frame @ np.append(ch.at(t0), 1.0))[:3], (frame @ np.append(ch.at(t1), 1.0))[:3]
    travel = np.array([end[0] - start[0], 0.0, end[2] - start[2]])
    u = ((times - t0) / (t1 - t0))[:, None]
    inverse = np.linalg.inv(frame)
    local = np.array([(inverse @ np.append(p, 1.0))[:3] for p in world - u * travel[None, :]])
    _set_channel(doc, anim, ch.index, times, local, interpolation)
    _compact_samplers(anim)
    velocity = travel / (t1 - t0)
    anim.setdefault('extras', {})['root_motion'] = {'bone': rig.names[root], 'velocity_mps': [round(float(v), 9) for v in velocity],
                                                    'distance_m': round(float(np.linalg.norm(travel)), 9), 'duration_s': t1 - t0}
    after = root_motion_facts(doc)[i]
    return {'clip': anim.get('name') or i, 'bone': rig.names[root], 'removed_horizontal_mm': float(np.linalg.norm(travel)) * 1000,
            'velocity_mps': [float(v) for v in velocity], 'horizontal_mm_after': after['horizontal_mm']}


def op_drop_morph_targets(doc, names=None):
    """Remove morph targets (default: those that move no vertex) from meshes, mesh/node weights and weight channels."""
    facts = morph_facts(doc)
    wanted = {(e['mesh'], e['index']) for e in facts['empty']} if not names else \
        {(r['mesh'], r['index']) for m in facts['meshes'] for r in m['rows'] if r['name'] in names}
    if names:
        known = {r['name'] for m in facts['meshes'] for r in m['rows']}
        unknown = [n for n in names if n not in known]
        if unknown:
            raise ValueError(f"Unknown morph targets: {', '.join(unknown)}")
    gltf, removed = doc.gltf, []
    for m in sorted({m for m, _ in wanted}):
        mesh = gltf['meshes'][m]
        count = max(len(p.get('targets', [])) for p in mesh['primitives'])
        keep = [k for k in range(count) if (m, k) not in wanted]
        names_of = (mesh.get('extras') or {}).get('targetNames') or []
        removed += [{'mesh': m, 'name': names_of[k] if k < len(names_of) else f'target_{k}'} for k in range(count) if (m, k) in wanted]
        for prim in mesh['primitives']:
            targets = prim.get('targets', [])
            prim['targets'] = [targets[k] for k in keep if k < len(targets)]
            if not prim['targets']:
                del prim['targets']
        if 'weights' in mesh:
            mesh['weights'] = [mesh['weights'][k] for k in keep if k < len(mesh['weights'])] or None
            if mesh['weights'] is None:
                del mesh['weights']
        if names_of:
            mesh['extras']['targetNames'] = [names_of[k] for k in keep if k < len(names_of)]
        nodes = {n for n, node in enumerate(gltf.get('nodes', [])) if node.get('mesh') == m}
        for n in nodes:
            if 'weights' in gltf['nodes'][n]:
                gltf['nodes'][n]['weights'] = [gltf['nodes'][n]['weights'][k] for k in keep] or None
                if gltf['nodes'][n]['weights'] is None:
                    del gltf['nodes'][n]['weights']
        for a, anim in enumerate(gltf.get('animations', [])):
            drop = []
            for c, channel in enumerate(anim['channels']):
                if channel.get('target', {}).get('path') == 'weights' and channel['target'].get('node') in nodes:
                    if not keep:
                        drop.append(c)
                        continue
                    sampler = anim['samplers'][channel['sampler']]
                    rows = np.asarray(doc.read(sampler['output']), dtype=np.float64).reshape(-1, count)[:, keep]
                    sampler_new = {'input': sampler['input'], 'output': doc.add(rows.reshape(-1), 'SCALAR'), 'interpolation': sampler.get('interpolation', 'LINEAR')}
                    anim['samplers'].append(sampler_new)
                    channel['sampler'] = len(anim['samplers']) - 1
            if drop:
                anim['channels'] = [ch for c, ch in enumerate(anim['channels']) if c not in drop]
                if not anim['channels']:
                    raise ValueError(f'Clip {anim.get("name") or a} would lose every channel; it only drove the removed morph targets')
            _compact_samplers(anim)
    return {'removed': removed, 'left': op_count_targets(doc)}


def op_count_targets(doc):
    return max((len(p.get('targets', [])) for m in doc.gltf.get('meshes', []) for p in m.get('primitives', [])), default=0)


# ---------- inspect / prepare entry points ----------
def _file_info(path):
    from .asset_inspection import format_of, sha256_of
    path = Path(path)
    kind = format_of(path)
    if kind not in ('glb', 'gltf'):
        raise ValueError(f'Animated assets are read from GLB or glTF (an FBX is converted with Blender first); {path.suffix} is not supported here')
    return {'path': str(path), 'format': kind, 'bytes': path.stat().st_size, 'sha256': sha256_of(path)}


def _source(services, model_id, path):
    """(GLB/glTF to read, library model or None, FBX conversion or None)."""
    if path and str(path).strip().strip('"').lower().endswith('.fbx') or model_id and _model_format(services, model_id) == 'fbx':
        from .asset_animation_fbx import readable_source
        try:
            return readable_source(services, model_id, path)
        except (RuntimeError, subprocess.TimeoutExpired) as exc:  # Blender missing or refused the file: a clear 400, not a crash
            raise ValueError(f'The FBX could not be converted with Blender: {exc}') from exc
    from .asset_inspection import resolve_source
    source, model = resolve_source(services, model_id, path)
    return source, model, None


def _model_format(services, model_id):
    model = services.models.get(int(model_id))
    return (model or {}).get('format')


def inspect_asset(services, target='web-animated', model_id=None, path=None):
    source, model, converted = _source(services, model_id, path)
    profile = get_profile(services.config.data_dir, target)
    info = _file_info(source)
    report = inspect_doc(Doc.load(source), profile, info)
    report['model'] = {'id': model['id'], 'name': model.get('name')} if model else None
    report['converted_from'] = converted
    return report


def animation_summary(services, model_id):
    """{animated, clips [{name, duration_s}], bones, morph_targets} of a library GLB/glTF/FBX model; other formats are not animated."""
    model = services.models.get(int(model_id))
    if model is None:
        raise LookupError(f'No model {model_id}')
    if model.get('format') not in ('glb', 'gltf', 'fbx'):
        return {'id': model['id'], 'format': model.get('format'), 'animated': False, 'clips': [], 'bones': 0, 'morph_targets': 0}
    source, _, converted = _source(services, model_id, None)
    doc = Doc.load(source)
    clips = clip_facts(doc)
    bones = max((len(s.get('joints', [])) for s in doc.gltf.get('skins', [])), default=0)
    morph = 0 if doc.compressed() else morph_facts(doc)['max_targets']
    return {'id': model['id'], 'format': model.get('format'), 'animated': bool(clips or bones or morph),
            'clips': [{'name': c['name'], 'duration_s': c['duration_s']} for c in clips], 'bones': bones, 'morph_targets': morph,
            'converted_from': converted}


def end_pose(doc):
    """{clip name: {node index: {path: value}}} at the last time of each clip, from this module's own sampler."""
    out = {}
    for i, anim in enumerate(doc.gltf.get('animations', [])):
        channels = [Channel(doc, i, c) for c in range(len(anim['channels']))]
        end = max(float(ch.times[-1]) for ch in channels)
        pose = {}
        for ch in channels:
            if ch.path in PATH_SIZE or ch.path == 'weights':
                pose.setdefault(str(ch.node), {})[ch.path] = [float(v) for v in np.atleast_1d(ch.at(end))]
        out[anim.get('name') or f'clip_{i}'] = {'duration_s': end, 'pose': pose, 'moves': any(not _constant(ch) for ch in channels)}
    return out


def reimport(path):
    """Load the GLB with three.js GLTFLoader in Node and play every clip to its end (independent consumer)."""
    from .simplify import REPO
    node = shutil.which('node')
    script = REPO / 'scripts' / 'anim_reimport.mjs'
    if not node or not script.is_file() or not (REPO / 'node_modules' / 'three' / 'package.json').is_file():
        return {'ok': False, 'available': False, 'reason': 'node and the three package are needed for the independent reimport'}
    done = subprocess.run([node, str(script), str(path)], capture_output=True, text=True, timeout=NODE_TIMEOUT_S, cwd=str(REPO))
    if done.returncode:
        return {'ok': False, 'available': True, 'reason': (done.stderr or done.stdout)[-800:]}
    return {'ok': True, 'available': True, **json.loads(done.stdout)}


def compare_reimport(doc, three):
    """Clip names/durations and the final pose of every animated bone: this module vs three.js."""
    ours = end_pose(doc)
    rows, worst = [], {'deg': 0.0, 'mm': 0.0, 'scale': 0.0, 'weights': 0.0}
    theirs = {c['name']: c for c in three.get('clips', [])}
    for name, clip in ours.items():
        other = theirs.get(name)
        row = {'clip': name, 'found': other is not None, 'duration_s': clip['duration_s']}
        if other:
            row['three_duration_s'] = other['duration']
            row['plays'] = bool(other.get('plays')) or not clip['moves']  # a clip that holds one pose plays without moving
            err = {'deg': 0.0, 'mm': 0.0, 'scale': 0.0, 'weights': 0.0}
            for node, paths in clip['pose'].items():
                got = (other.get('final') or {}).get(node) or {}
                for path, value in paths.items():
                    if path not in got or got[path] is None:  # three did not animate this channel at all
                        err['deg'] = err['mm'] = err['scale'] = err['weights'] = 1e9
                        continue
                    if path == 'rotation':
                        err['deg'] = max(err['deg'], quat_angle_deg(value, got[path]))
                    elif path == 'weights':
                        theirs_w = list(got[path])[:len(value)] + [0.0] * max(0, len(value) - len(got[path]))
                        err['weights'] = max(err['weights'], float(np.max(np.abs(np.subtract(value, theirs_w)))))
                    elif path == 'translation':
                        err['mm'] = max(err['mm'], float(np.linalg.norm(np.subtract(value, got[path]))) * 1000)
                    else:
                        err['scale'] = max(err['scale'], float(np.max(np.abs(np.subtract(value, got[path])))))
            row.update(final_pose_error_deg=err['deg'], final_pose_error_mm=err['mm'], final_pose_error_scale=err['scale'],
                       final_pose_error_weights=err['weights'], duration_matches=abs(other['duration'] - clip['duration_s']) < 1e-4)
            worst = {k: max(worst[k], err[k]) for k in worst}
        rows.append(row)
    ok = (all(r['found'] and r['duration_matches'] and r['plays'] for r in rows) and len(theirs) == len(ours)
          and worst['deg'] < 0.01 and worst['mm'] < 0.01 and worst['scale'] < 1e-5 and worst['weights'] < 1e-4)
    return {'ok': ok, 'clips': rows, 'max_final_pose_error_deg': worst['deg'], 'max_final_pose_error_mm': worst['mm'],
            'max_final_pose_error_weights': worst['weights']}


def _prepared_dir(services):
    folder = Path(services.config.data_dir) / 'anim_prepared'
    folder.mkdir(parents=True, exist_ok=True)
    return folder


def _trim_windows(animations, original_names, trims):
    """{clip index: [start, end]} for trims keyed by index, current name or the name the clip had before any rename; unknown keys are refused."""
    out = {}
    for key, window in (trims or {}).items():
        hits = [i for i, anim in enumerate(animations) if str(i) == str(key) or anim.get('name') == key or original_names[i] == key]
        if len(hits) != 1:
            raise LookupError(f'No clip {key!r} to trim' if not hits else f'Trim key {key!r} names several clips; use the index')
        if window:
            out[hits[0]] = window
    return out


def _clip_index_after_rename(doc, key, original_names):
    """A clip by index, current name or the name it had before a rename in the same preparation."""
    animations = doc.gltf.get('animations', [])
    hits = [i for i, anim in enumerate(animations) if str(i) == str(key) or anim.get('name') == key or original_names[i] == key]
    if len(hits) != 1:
        raise LookupError(f'No clip {key!r}' if not hits else f'Clip key {key!r} names several clips; use the index')
    return hits[0]


def prepare(services, target='web-animated', model_id=None, path=None, renames=None, trims=None, resample_fps=None,
            drop_empty_channels=None, drop_unused_bones=None, normalize_weights=None, limit_influences=None,
            close_loops=None, root_in_place=None, drop_empty_morph_targets=None):
    """Write a verified animated variant; the original is never modified. Returns the stored receipt.

    close_loops / root_in_place change the motion itself, so they apply only when asked: True (the clips the report suggests)
    or a list of clip names. An FBX source is converted with Blender first and the conversion is named in the receipt."""
    from .asset_inspection import sha256_of
    source, model, converted = _source(services, model_id, path)
    profile = get_profile(services.config.data_dir, target)
    rules = profile['rules']
    info = _file_info(source)
    before = inspect_doc(Doc.load(source), profile, info)
    prep_id = time.strftime('%Y%m%d%H%M%S') + uuid.uuid4().hex[:6]
    receipt = {'id': prep_id, 'kind': 'animated_preparation', 'created': time.time(), 'profile': profile['id'],
               'model': {'id': model['id'], 'name': model.get('name')} if model else None, 'source': info, 'converted_from': converted,
               'options': {'renames': renames, 'trims': trims, 'resample_fps': resample_fps, 'drop_empty_channels': drop_empty_channels,
                           'drop_unused_bones': drop_unused_bones, 'normalize_weights': normalize_weights, 'limit_influences': limit_influences,
                           'close_loops': close_loops, 'root_in_place': root_in_place, 'drop_empty_morph_targets': drop_empty_morph_targets},
               'before': {'summary': before['summary'], 'checks': before['checks']}, 'operations': []}
    planned = {op['op']: op for op in before['operations']}
    suggested = {op['op']: op for op in before.get('suggested', [])}
    asked = bool(renames or trims or resample_fps or close_loops or root_in_place or drop_empty_morph_targets)
    if before['manual'] and not before['operations'] and not asked:
        receipt['manual'] = before['manual']
    try:
        if Doc.load(source).compressed():
            raise ValueError('compressed glTF data is not rewritten here: ' + '; '.join(before['manual']))
        doc = Doc.load(source)
        log = receipt['operations']
        if drop_unused_bones or (drop_unused_bones is None and 'drop_unused_bones' in planned):
            names = [b['name'] for b in (skeleton_facts(doc)['removable_bones'])]
            if names:
                log.append({'op': 'drop_unused_bones', **op_drop_bones(doc, names)})
        keep = limit_influences or (rules['max_influences'] if limit_influences is None and 'limit_influences' in planned else None)
        if keep or normalize_weights or (normalize_weights is None and 'normalize_weights' in planned):
            log.append({'op': 'limit_influences' if keep else 'normalize_weights', **op_weights(doc, keep)})
        if drop_empty_morph_targets or (drop_empty_morph_targets is None and 'drop_empty_morph_targets' in planned):
            if morph_facts(doc)['empty']:
                log.append({'op': 'drop_empty_morph_targets', **op_drop_morph_targets(doc)})
        if drop_empty_channels or (drop_empty_channels is None and 'drop_empty_channels' in planned):
            dropped = op_drop_empty_channels(doc)
            if dropped:
                log.append({'op': 'drop_empty_channels', 'dropped': dropped})
        original_names = [a.get('name') for a in doc.gltf.get('animations', [])]
        windows = _trim_windows(doc.gltf.get('animations', []), original_names, trims)  # clip index -> window, by old or new name
        auto_names = (planned.get('rename_clips') or {}).get('renames') or {}
        names_map = {**auto_names, **(renames or {})}
        if names_map:
            log.append({'op': 'rename_clips', 'renamed': op_rename(doc, names_map)})
        fps = resample_fps or (rules['target_fps'] if 'resample' in planned else None)
        clips = clip_facts(doc, rules)
        for clip in clips:
            window = windows.get(clip['index'])
            dense = fps and clip['key_rate_fps'] > fps * 1.001
            if window or dense:
                start, end = (window or [None, None])[:2]
                done = op_retime(doc, clip['index'], start, end, fps if dense else None)
                done['within_tolerance'] = done['error_deg'] <= rules['max_resample_error_deg'] + 1e-9 and done['error_mm'] <= rules['max_resample_error_mm'] + 1e-9
                log.append({'op': ('trim+' if window else '') + ('resample' if dense else 'trim'), **done})

        def chosen(option, op):
            if not option:
                return []
            if option is True:
                return list((suggested.get(op) or {}).get('clips') or [])
            return list(option)
        for clip in chosen(root_in_place, 'root_in_place'):  # before closing: the steady travel is not a loop error to spread
            log.append({'op': 'root_in_place', **op_root_in_place(doc, _clip_index_after_rename(doc, clip, original_names))})
        for clip in chosen(close_loops, 'close_loops'):
            log.append({'op': 'close_loop', **op_close_loop(doc, _clip_index_after_rename(doc, clip, original_names))})
        if not log:
            receipt.update(status='unchanged', note='Nothing to do: the asset already meets the automatic rules (see manual steps, if any).',
                           manual=before['manual'])
            return _store_receipt(services, receipt)
        too_far = [op for op in log if op.get('within_tolerance') is False]
        if too_far:
            receipt.update(status='rejected', reason='The resampling error exceeds the profile tolerance; nothing was written. '
                           'Raise the fps or the tolerance.', manual=before['manual'])
            return _store_receipt(services, receipt)
        output = _prepared_dir(services) / f"{Path(info['path']).stem}.{profile['id']}.{prep_id}.glb"
        atomic.write_bytes_atomic(output, doc.to_glb())
        written = Doc.load(output)
        out_info = _file_info(output)
        after = inspect_doc(written, profile, out_info)
        three = reimport(output)
        comparison = compare_reimport(written, three) if three.get('ok') else {'ok': False, 'reason': three.get('reason')}
        unchanged = sha256_of(Path(info['path'])) == info['sha256']
        receipt.update(output=out_info, after={'summary': after['summary'], 'checks': after['checks'], 'clips': after['clips'], 'skeleton': after['skeleton'],
                                               'morph': after['morph'], 'root_motion': after['root_motion']},
                       reimport={'consumer': 'three.js GLTFLoader + AnimationMixer (Node)', **{k: v for k, v in three.items() if k != 'final'},
                                 'comparison': comparison}, original_unchanged=unchanged, manual=after['manual'])
        receipt['status'] = 'prepared' if unchanged and (comparison['ok'] or not three.get('available', True)) else 'failed'
        if receipt['status'] == 'failed':
            receipt['reason'] = 'The original changed during preparation' if not unchanged else 'The independent reimport did not match: ' + json.dumps(comparison)[:600]
    except (ValueError, LookupError, KeyError, subprocess.TimeoutExpired) as exc:
        receipt.update(status='rejected', reason=f'{type(exc).__name__}: {exc}'[:800])
    return _store_receipt(services, receipt)


def _store_receipt(services, receipt):
    atomic.write_json_atomic(_prepared_dir(services) / f"{receipt['id']}.json", receipt)
    return receipt


def list_preparations(services, model_id=None, limit=20):
    rows = []
    for file in sorted(_prepared_dir(services).glob('*.json'), reverse=True):
        try:
            row = json.loads(file.read_text(encoding='utf-8'))
        except (OSError, ValueError):
            continue
        if model_id and (row.get('model') or {}).get('id') != model_id:
            continue
        rows.append({k: row.get(k) for k in ('id', 'status', 'profile', 'model', 'created', 'reason')} |
                    {'output': (row.get('output') or {}).get('path'), 'operations': [op['op'] for op in row.get('operations', [])],
                     'compliant_after': ((row.get('after') or {}).get('summary') or {}).get('compliant')})
        if len(rows) >= limit:
            break
    return rows


def get_preparation(services, prep_id):
    if not re.fullmatch(r'[0-9a-f]{20}', prep_id or ''):
        raise ValueError('Invalid preparation id')
    file = _prepared_dir(services) / f'{prep_id}.json'
    if not file.is_file():
        raise LookupError('Preparation not found')
    return json.loads(file.read_text(encoding='utf-8'))


def sha256_file(path):
    from .asset_inspection import sha256_of
    return sha256_of(Path(path))
