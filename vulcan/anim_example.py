"""A small invented rig written as GLB, used by the animated-asset tests and offered as the example of the Animaciones
page: five joints, one of them without
influence, a vertex with five influences, unnormalised weights, a non-uniform joint scale, and four clips (a closed idle
loop at 60 fps with a channel that holds the rest pose, a walk that does not close, an open clip at 30 fps, an unnamed clip)."""
import json
import math
import struct

import numpy as np


def _quat_z(deg):
    a = math.radians(deg) / 2
    return [0.0, 0.0, math.sin(a), math.cos(a)]


def build(path, unnamed=True, morph=False, root_motion=False):
    """morph adds two morph targets to the body (one that moves vertices, one that moves nothing) and a 'blink' clip driving
    the first; root_motion makes the walk clip carry the root 0.5 m along +x (a walk that travels instead of playing in place)."""
    chunks, views, accessors = [], [], []

    def add(array, kind, component=5126, minmax=False):
        array = np.ascontiguousarray(array, dtype={5126: np.float32, 5123: np.uint16}[component])
        offset = sum(len(c) for c in chunks)
        data = array.tobytes()
        chunks.append(data + b'\x00' * ((-len(data)) % 4))
        views.append({'buffer': 0, 'byteOffset': offset, 'byteLength': len(data)})
        acc = {'bufferView': len(views) - 1, 'componentType': component, 'count': int(len(array)), 'type': kind}
        if minmax:
            flat = array.reshape(len(array), -1)
            acc['min'], acc['max'] = [float(v) for v in flat.min(0)], [float(v) for v in flat.max(0)]
        accessors.append(acc)
        return len(accessors) - 1

    levels = [0.0, 0.1, 0.2, 0.3]
    positions = np.array([[x, 0.0, z] for z in levels for x in (-0.05, 0.05)])
    indices = np.array([i for k in range(3) for i in (2 * k, 2 * k + 1, 2 * k + 3, 2 * k, 2 * k + 3, 2 * k + 2)])
    # skin-local joints: 0 root, 1 spine, 2 head, 3 tip, 4 unused_end
    j0 = np.array([[0, 0, 0, 0], [0, 0, 0, 0], [0, 1, 0, 0], [0, 1, 0, 0], [1, 2, 0, 0], [1, 2, 0, 0], [2, 1, 0, 3], [2, 1, 0, 3]])
    w0 = np.array([[1, 0, 0, 0], [1, 0, 0, 0], [.5, .5, 0, 0], [.5, .5, 0, 0], [.6, .3, 0, 0], [.6, .4, 0, 0], [.4, .2, .2, .1], [.5, .2, .2, .1]])
    j1 = np.zeros((8, 4))
    w1 = np.zeros((8, 4))
    w0[6] = [.3, .2, .2, .1]
    j1[6, 0], w1[6, 0] = 1, 0.2  # vertex 6: a fifth weight slot in the second set (sum 1.0)
    mesh = {'primitives': [{'attributes': {'POSITION': add(positions, 'VEC3', minmax=True), 'JOINTS_0': add(j0, 'VEC4', 5123), 'WEIGHTS_0': add(w0, 'VEC4'),
                                           'JOINTS_1': add(j1, 'VEC4', 5123), 'WEIGHTS_1': add(w1, 'VEC4')},
                            'indices': add(indices.astype(np.uint16), 'SCALAR', 5123)}]}
    joints_world = {1: [0, 0, 0], 2: [0, 0, 0.1], 3: [0, 0, 0.2], 4: [0, 0, 0.3], 6: [0, 0, 0.35]}
    ibm = np.array([np.array([[1, 0, 0, 0], [0, 1, 0, 0], [0, 0, 1, 0], [-x, -y, -z, 1]], dtype=float).reshape(-1) for x, y, z in joints_world.values()])
    nodes = [{'name': 'Armature', 'children': [1, 5]},
             {'name': 'root', 'children': [2]},
             {'name': 'spine', 'translation': [0, 0, 0.1], 'children': [3]},
             {'name': 'head', 'translation': [0, 0, 0.1], 'children': [4, 6]},
             {'name': 'tip', 'translation': [0, 0, 0.1], 'scale': [1, 1.5, 1]},
             {'name': 'body', 'mesh': 0, 'skin': 0},
             {'name': 'unused_end', 'translation': [0, 0, 0.15]}]
    animations = []

    def clip(name, channels):
        samplers, chans = [], []
        for node, pathname, times, values in channels:
            samplers.append({'input': add(np.array(times), 'SCALAR', minmax=True), 'output': add(np.array(values), 'VEC4' if pathname == 'rotation' else 'VEC3'),
                             'interpolation': 'LINEAR'})
            chans.append({'sampler': len(samplers) - 1, 'target': {'node': node, 'path': pathname}})
        anim = {'samplers': samplers, 'channels': chans}
        if name:
            anim['name'] = name
        animations.append(anim)

    t60 = np.linspace(0, 2, 121)
    clip('idle', [(2, 'rotation', t60, [_quat_z(20 * math.sin(math.pi * t)) for t in t60]),
                  (3, 'translation', [0, 2], [[0, 0, 0.1], [0, 0, 0.1]])])
    t60w = np.linspace(0, 1, 61)
    walk = [(1, 'rotation', t60w, [_quat_z(10 * t) for t in t60w])]
    if root_motion:
        walk.append((1, 'translation', t60w, [[0.5 * t, 0, 0] for t in t60w]))
    clip('walk', walk)
    t30 = np.linspace(0, 1.5, 46)
    clip('open', [(3, 'rotation', t30, [_quat_z(45 * t / 1.5) for t in t30])])
    clip(None if unnamed else 'wave', [(4, 'translation', [0, 0.5], [[0, 0, 0.1], [0.02, 0, 0.1]])])
    if morph:
        bulge = np.zeros((8, 3))
        bulge[4:, 0] = 0.02  # the upper vertices move 20 mm along x
        mesh['primitives'][0]['targets'] = [{'POSITION': add(bulge, 'VEC3', minmax=True)}, {'POSITION': add(np.zeros((8, 3)), 'VEC3', minmax=True)}]
        mesh['weights'] = [0.0, 0.0]
        mesh['extras'] = {'targetNames': ['bulge', 'unused_shape']}
        times = add(np.array([0.0, 0.5, 1.0]), 'SCALAR', minmax=True)
        weights = add(np.array([0.0, 0.0, 1.0, 0.0, 0.0, 0.0]), 'SCALAR')
        animations.append({'name': 'blink', 'samplers': [{'input': times, 'output': weights, 'interpolation': 'LINEAR'}],
                           'channels': [{'sampler': 0, 'target': {'node': 5, 'path': 'weights'}}]})
    gltf = {'asset': {'version': '2.0', 'generator': 'vulcan test rig'}, 'scene': 0, 'scenes': [{'nodes': [0]}], 'nodes': nodes, 'meshes': [mesh],
            'skins': [{'joints': [1, 2, 3, 4, 6], 'inverseBindMatrices': add(ibm, 'MAT4'), 'skeleton': 1}], 'animations': animations,
            'accessors': accessors, 'bufferViews': views}
    binary = b''.join(chunks)
    gltf['buffers'] = [{'byteLength': len(binary)}]
    text = json.dumps(gltf).encode()
    text += b' ' * ((-len(text)) % 4)
    total = 12 + 8 + len(text) + 8 + len(binary)
    data = struct.pack('<4sII', b'glTF', 2, total) + struct.pack('<I4s', len(text), b'JSON') + text + struct.pack('<I4s', len(binary), b'BIN\x00') + binary
    with open(path, 'wb') as handle:
        handle.write(data)
    return path


def build_target(path, prefix='mixamorig:'):
    """A second invented rig for retargeting tests: the same chain with other bone names (prefixed, capitalised), twice the
    size, a spine whose local axes are turned 90 degrees about z, and no clips."""
    chunks, views, accessors = [], [], []

    def add(array, kind, component=5126, minmax=False):
        array = np.ascontiguousarray(array, dtype={5126: np.float32, 5123: np.uint16}[component])
        offset = sum(len(c) for c in chunks)
        data = array.tobytes()
        chunks.append(data + b'\x00' * ((-len(data)) % 4))
        views.append({'buffer': 0, 'byteOffset': offset, 'byteLength': len(data)})
        acc = {'bufferView': len(views) - 1, 'componentType': component, 'count': int(len(array)), 'type': kind}
        if minmax:
            flat = array.reshape(len(array), -1)
            acc['min'], acc['max'] = [float(v) for v in flat.min(0)], [float(v) for v in flat.max(0)]
        accessors.append(acc)
        return len(accessors) - 1

    turn = _quat_z(90)
    nodes = [{'name': 'Rig', 'children': [1, 5]},
             {'name': prefix + 'Root', 'children': [2]},
             {'name': prefix + 'Spine', 'translation': [0, 0, 0.2], 'rotation': turn, 'children': [3]},
             {'name': prefix + 'Head', 'translation': [0, 0, 0.2], 'rotation': [0, 0, -turn[2], turn[3]], 'children': [4]},
             {'name': prefix + 'Tip', 'translation': [0, 0, 0.2]},
             {'name': 'Body', 'mesh': 0, 'skin': 0}]
    world = {}
    for index in (1, 2, 3, 4):  # world matrices of the joints at rest, for the inverse bind matrices
        node = nodes[index]
        m = np.eye(4)
        x, y, z, w = node.get('rotation', [0, 0, 0, 1])
        m[:3, :3] = [[1 - 2 * (y * y + z * z), 2 * (x * y - z * w), 2 * (x * z + y * w)], [2 * (x * y + z * w), 1 - 2 * (x * x + z * z), 2 * (y * z - x * w)],
                     [2 * (x * z - y * w), 2 * (y * z + x * w), 1 - 2 * (x * x + y * y)]]
        m[:3, 3] = node.get('translation', [0, 0, 0])
        world[index] = (world[index - 1] @ m) if index > 1 else m
    ibm = np.array([np.linalg.inv(world[i]).T.reshape(-1) for i in (1, 2, 3, 4)])  # glTF matrices are column-major
    levels = [0.0, 0.2, 0.4, 0.6]
    positions = np.array([[x, 0.0, z] for z in levels for x in (-0.1, 0.1)])
    indices = np.array([i for k in range(3) for i in (2 * k, 2 * k + 1, 2 * k + 3, 2 * k, 2 * k + 3, 2 * k + 2)])
    joints = np.array([[0, 0, 0, 0]] * 2 + [[1, 0, 0, 0]] * 2 + [[2, 0, 0, 0]] * 2 + [[3, 0, 0, 0]] * 2)
    weights = np.array([[1, 0, 0, 0]] * 8)
    mesh = {'primitives': [{'attributes': {'POSITION': add(positions, 'VEC3', minmax=True), 'JOINTS_0': add(joints, 'VEC4', 5123),
                                           'WEIGHTS_0': add(weights, 'VEC4')}, 'indices': add(indices.astype(np.uint16), 'SCALAR', 5123)}]}
    gltf = {'asset': {'version': '2.0', 'generator': 'vulcan test rig (target)'}, 'scene': 0, 'scenes': [{'nodes': [0]}], 'nodes': nodes,
            'meshes': [mesh], 'skins': [{'joints': [1, 2, 3, 4], 'inverseBindMatrices': add(ibm, 'MAT4'), 'skeleton': 1}],
            'accessors': accessors, 'bufferViews': views}
    binary = b''.join(chunks)
    gltf['buffers'] = [{'byteLength': len(binary)}]
    text = json.dumps(gltf).encode()
    text += b' ' * ((-len(text)) % 4)
    total = 12 + 8 + len(text) + 8 + len(binary)
    data = struct.pack('<4sII', b'glTF', 2, total) + struct.pack('<I4s', len(text), b'JSON') + text + struct.pack('<I4s', len(binary), b'BIN\x00') + binary
    with open(path, 'wb') as handle:
        handle.write(data)
    return path
