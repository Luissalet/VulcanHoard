"""Retarget clips from one rig to another whose bones have other names, lengths or rest orientations.

Bones are paired by an explicit map or by normalised names (prefixes such as 'mixamorig:' or 'Armature|' dropped, case,
separators and Left/Right/L/R side tokens unified). For every sampled time the source bone's world rotation change from
its rest pose is applied to the paired target bone in world space and turned back into the target's local rotation, so
bone axes and lengths may differ; the top-most paired bone also receives the source root's world travel, scaled by the
ratio of the skeleton heights. This assumes both rigs share a rest pose family (both T-pose or both A-pose): the angle
between each pair's rest bone directions is reported, never hidden.

The result is the target file plus the new clips, written as a NEW GLB (neither input is touched), measured again from
disk (the world rotation change of every paired bone, target against source, at keys and midpoints) and replayed with
three.js like any preparation. Unpaired bones on either side are listed.
"""
from __future__ import annotations

import re
import time
import uuid
from pathlib import Path

import numpy as np

from .asset_animation_rig import Rig, qangle, qinv, qmul, world_rotation

SIDES = {'l': 'l', 'left': 'l', 'r': 'r', 'right': 'r'}
MAX_KEYS = 1800


def normalise_bone(name):
    """'mixamorig:LeftUpLeg' / 'Armature|upleg.L' / 'UpLeg_L' -> 'l:upleg'."""
    text = re.split(r'[|:]', name or '')[-1]
    text = re.sub(r'(?<=[a-z0-9])(?=[A-Z])', ' ', text).lower()
    tokens = [t for t in re.split(r'[\s._\-]+', text) if t]
    side = ''
    rest = []
    for t in tokens:
        if t in SIDES:
            side = SIDES[t]
        else:
            rest.append(t)
    return (side + ':' if side else '') + ''.join(rest)


def pair_bones(source_rig, target_rig, bone_map=None):
    """{target node: (source node, how)} from the explicit map, then unique normalised names."""
    s_joints = source_rig.joints() or list(range(len(source_rig.nodes)))
    t_joints = target_rig.joints() or list(range(len(target_rig.nodes)))
    s_by_name = {source_rig.names[j]: j for j in s_joints}
    t_by_name = {target_rig.names[j]: j for j in t_joints}
    pairs = {}
    for s_name, t_name in (bone_map or {}).items():
        if s_name not in s_by_name:
            raise ValueError(f'map: {s_name!r} is not a source bone')
        if t_name not in t_by_name:
            raise ValueError(f'map: {t_name!r} is not a target bone')
        pairs[t_by_name[t_name]] = (s_by_name[s_name], 'map')
    used = {s for s, _ in pairs.values()}
    s_norm, t_norm = {}, {}
    for j in s_joints:
        s_norm.setdefault(normalise_bone(source_rig.names[j]), []).append(j)
    for j in t_joints:
        t_norm.setdefault(normalise_bone(target_rig.names[j]), []).append(j)
    for key, targets in t_norm.items():
        sources = s_norm.get(key) or []
        if len(targets) == 1 and len(sources) == 1 and targets[0] not in pairs and sources[0] not in used:
            pairs[targets[0]] = (sources[0], 'name')
            used.add(sources[0])
    return pairs


def _mapped_child(rig, node, pairs_or_set):
    """The nearest descendant of node that is paired (breadth first), or None."""
    queue = list(rig.nodes[node].get('children', []))
    while queue:
        n = queue.pop(0)
        if n in pairs_or_set:
            return n
        queue.extend(rig.nodes[n].get('children', []))
    return None


def _height(world, joints):
    """Largest extent of the joints' rest positions (the skeleton's size along its main axis)."""
    if not joints:
        return 0.0
    points = np.array([world[j][:3, 3] for j in joints])
    return float(np.ptp(points, axis=0).max())


def _sample_times(chans, t0, t1):
    times = np.unique(np.concatenate([c.times for c in chans.values()] + [[t0, t1]]))
    times = times[(times >= t0 - 1e-9) & (times <= t1 + 1e-9)]
    if len(times) > MAX_KEYS:
        times = np.linspace(t0, t1, MAX_KEYS)
    return times


def retarget_doc(source_doc, target_doc, bone_map=None, clips=None, translation_scale=None):
    """Append retargeted clips to target_doc (in memory). Returns the facts of the mapping and the clips written."""
    rs, rt = Rig(source_doc), Rig(target_doc)
    pairs = pair_bones(rs, rt, bone_map)
    if not pairs:
        raise ValueError('No bone of the target could be paired with the source: give a map {source bone: target bone}')
    s_rest, t_rest = rs.world(), rt.world()
    rs_rot = {n: world_rotation(s_rest[n]) for n in range(len(rs.nodes))}
    rt_rot = {n: world_rotation(t_rest[n]) for n in range(len(rt.nodes))}
    depth = lambda rig, n: 0 if n not in rig.parent else 1 + depth(rig, rig.parent[n])  # noqa: E731
    root_t = min(pairs, key=lambda n: (depth(rt, n), n))
    root_s = pairs[root_t][0]
    s_size = _height(s_rest, [s for s, _ in pairs.values()])  # measured on the paired bones only: an extra bone is not size
    t_size = _height(t_rest, list(pairs))
    scale = float(translation_scale) if translation_scale else (t_size / s_size if s_size > 1e-9 and t_size > 1e-9 else 1.0)
    rest_rows = []
    for t_node, (s_node, how) in sorted(pairs.items(), key=lambda kv: rt.order.index(kv[0])):
        child_t = _mapped_child(rt, t_node, pairs)
        row = {'source': rs.names[s_node], 'target': rt.names[t_node], 'how': how}
        if child_t is not None:
            child_s = pairs[child_t][0]
            dt = t_rest[child_t][:3, 3] - t_rest[t_node][:3, 3]
            ds = s_rest[child_s][:3, 3] - s_rest[s_node][:3, 3]
            if np.linalg.norm(dt) > 1e-9 and np.linalg.norm(ds) > 1e-9:
                cos = float(np.dot(dt, ds) / (np.linalg.norm(dt) * np.linalg.norm(ds)))
                row['rest_direction_difference_deg'] = float(np.degrees(np.arccos(min(1.0, max(-1.0, cos)))))
        rest_rows.append(row)
    s_joints = rs.joints() or list(range(len(rs.nodes)))
    paired_sources = {s for s, _ in pairs.values()}
    wanted = None if not clips else set(clips)
    names = {a.get('name') for a in target_doc.gltf.get('animations', [])}
    written = []
    for a, anim in enumerate(source_doc.gltf.get('animations', [])):
        name = anim.get('name') or f'clip_{a}'
        if wanted is not None and name not in wanted and str(a) not in wanted:
            continue
        chans = rs.channels(a)
        if not any(n in paired_sources for n, _ in chans):
            continue
        t0, t1 = rs.span(a)
        times = _sample_times(chans, t0, t1)
        keys = {n: [] for n in pairs}
        root_keys = []
        moves_root = (root_s, 'translation') in chans
        for t in times:
            world_s = rs.world(chans, t)
            local_t = [list(x) for x in rt.rest]
            rot = {}
            for n in rt.order:  # world rotations of the target, parents first
                p = rt.parent.get(n)
                parent_rot = rot[p] if p is not None else np.array([0.0, 0.0, 0.0, 1.0])
                if n in pairs:
                    s = pairs[n][0]
                    rot[n] = qmul(qmul(world_rotation(world_s[s]), qinv(rs_rot[s])), rt_rot[n])
                    local = qmul(qinv(parent_rot), rot[n])
                    local_t[n][1] = local / np.linalg.norm(local)
                    keys[n].append(local_t[n][1])
                else:
                    rot[n] = qmul(parent_rot, rt.rest[n][1])
            if moves_root:
                travel = (world_s[root_s][:3, 3] - s_rest[root_s][:3, 3]) * scale
                world_t = rt.world(local=[tuple(x) for x in local_t])
                p = rt.parent.get(root_t)
                parent_m = world_t[p] if p is not None else np.eye(4)
                root_keys.append((np.linalg.inv(parent_m) @ np.append(t_rest[root_t][:3, 3] + travel, 1.0))[:3])
        new_name = name if name not in names else f'{name}_retarget'
        names.add(new_name)
        samplers, channels = [], []
        times_out = np.asarray(times - t0, dtype=np.float32)
        for n, values in keys.items():
            values = np.array(values)
            for k in range(1, len(values)):  # keep neighbouring quaternions in the same hemisphere for clean slerps
                if np.dot(values[k], values[k - 1]) < 0:
                    values[k] = -values[k]
            samplers.append({'input': target_doc.add(times_out, 'SCALAR', minmax=True), 'output': target_doc.add(values, 'VEC4'), 'interpolation': 'LINEAR'})
            channels.append({'sampler': len(samplers) - 1, 'target': {'node': n, 'path': 'rotation'}})
        if moves_root:
            samplers.append({'input': target_doc.add(times_out, 'SCALAR', minmax=True), 'output': target_doc.add(np.array(root_keys), 'VEC3'),
                             'interpolation': 'LINEAR'})
            channels.append({'sampler': len(samplers) - 1, 'target': {'node': root_t, 'path': 'translation'}})
        target_doc.gltf.setdefault('animations', []).append({'name': new_name, 'samplers': samplers, 'channels': channels,
                                                              'extras': {'retargeted_from': name}})
        written.append({'source_clip': name, 'clip': new_name, 'index': len(target_doc.gltf['animations']) - 1, 'keys': int(len(times)),
                        'duration_s': float(t1 - t0), 'root_translation': moves_root, 'source_index': a})
    if not written:
        raise ValueError('No source clip animates a paired bone' + (f' among {sorted(wanted)}' if wanted else ''))
    animated_unpaired = sorted({rs.names[n] for a in range(len(source_doc.gltf.get('animations', []))) for n, _ in rs.channels(a)
                                if n in s_joints and n not in paired_sources})
    return {'pairs': rest_rows, 'root': {'source': rs.names[root_s], 'target': rt.names[root_t], 'translation_scale': scale,
                                         'source_size_m': s_size, 'target_size_m': t_size},
            'unpaired_target_bones': sorted(rt.names[j] for j in (rt.joints() or []) if j not in pairs),
            'unpaired_source_bones': sorted(rs.names[j] for j in s_joints if j not in paired_sources),
            'animated_source_bones_not_transferred': animated_unpaired, 'clips': written,
            'max_rest_direction_difference_deg': max((r.get('rest_direction_difference_deg', 0.0) for r in rest_rows), default=0.0)}


def fidelity(source_doc, written_doc, facts, bone_map=None):
    """From the written file: the world rotation change of each paired target bone against its source bone (degrees) and
    the root travel against the scaled source travel (mm), at every key and midpoint of every written clip."""
    rs, rw = Rig(source_doc), Rig(written_doc)
    pairs = pair_bones(rs, rw, bone_map)
    s_rest, w_rest = rs.world(), rw.world()
    worst_deg, worst_mm, rows = 0.0, 0.0, []
    root_s = next(i for i, n in enumerate(rs.names) if n == facts['root']['source'])
    root_t = next(i for i, n in enumerate(rw.names) if n == facts['root']['target'])
    for clip in facts['clips']:
        s_ch, w_ch = rs.channels(clip['source_index']), rw.channels(clip['index'])
        s0, s1 = rs.span(clip['source_index'])
        keys = np.asarray(sorted({float(t) for c in w_ch.values() for t in c.times}))
        probe = np.unique(np.concatenate([keys, (keys[:-1] + keys[1:]) / 2])) if len(keys) > 1 else keys
        if len(probe) > 400:
            probe = np.linspace(probe[0], probe[-1], 400)
        deg = mm = 0.0
        for t in probe:
            ws, ww = rs.world(s_ch, s0 + t), rw.world(w_ch, t)
            for n, (s, _) in pairs.items():
                d_src = qmul(world_rotation(ws[s]), qinv(world_rotation(s_rest[s])))
                d_out = qmul(world_rotation(ww[n]), qinv(world_rotation(w_rest[n])))
                deg = max(deg, qangle(d_src, d_out))
            if clip['root_translation']:
                expected = (ws[root_s][:3, 3] - s_rest[root_s][:3, 3]) * facts['root']['translation_scale']
                mm = max(mm, float(np.linalg.norm((ww[root_t][:3, 3] - w_rest[root_t][:3, 3]) - expected)) * 1000)
        rows.append({'clip': clip['clip'], 'samples': int(len(probe)), 'max_rotation_change_error_deg': deg, 'max_root_travel_error_mm': mm})
        worst_deg, worst_mm = max(worst_deg, deg), max(worst_mm, mm)
    return {'clips': rows, 'max_rotation_change_error_deg': worst_deg, 'max_root_travel_error_mm': worst_mm,
            'note': 'midpoints between keys are interpolated by the runtime, so a small error there is the sampling, not the mapping'}


def retarget(services, source_id=None, source_path=None, target_id=None, target_path=None, bone_map=None, clips=None, translation_scale=None,
             max_error_deg=1.0):
    """Write the target rig with the source clips retargeted onto it as a NEW GLB, measured and replayed; returns the receipt."""
    from .asset_animation import Doc, _file_info, _prepared_dir, _source, _store_receipt, compare_reimport, inspect_doc, get_profile, reimport
    from .asset_inspection import sha256_of
    from .hoard_link import atomic
    src, src_model, src_conv = _source(services, source_id, source_path)
    tgt, tgt_model, tgt_conv = _source(services, target_id, target_path)
    src_info, tgt_info = _file_info(src), _file_info(tgt)
    prep_id = time.strftime('%Y%m%d%H%M%S') + uuid.uuid4().hex[:6]
    receipt = {'id': prep_id, 'kind': 'retarget', 'created': time.time(), 'profile': 'retarget',
               'model': {'id': tgt_model['id'], 'name': tgt_model.get('name')} if tgt_model else None,
               'source': src_info, 'target': tgt_info, 'converted_from': {'source': src_conv, 'target': tgt_conv},
               'options': {'map': bone_map, 'clips': clips, 'translation_scale': translation_scale, 'max_error_deg': max_error_deg}, 'operations': []}
    try:
        source_doc, target_doc = Doc.load(src), Doc.load(tgt)
        if source_doc.compressed() or target_doc.compressed():
            raise ValueError('compressed glTF data is not rewritten here; decompress it first')
        facts = retarget_doc(source_doc, target_doc, bone_map, clips, translation_scale)
        receipt['operations'].append({'op': 'retarget', **{k: v for k, v in facts.items() if k != 'pairs'}})
        receipt['mapping'] = facts['pairs']
        output = _prepared_dir(services) / f'{Path(tgt).stem}.retarget.{prep_id}.glb'
        atomic.write_bytes_atomic(output, target_doc.to_glb())
        written = Doc.load(output)
        measured = fidelity(Doc.load(src), written, facts, bone_map)
        three = reimport(output)
        comparison = compare_reimport(written, three) if three.get('ok') else {'ok': False, 'reason': three.get('reason')}
        after = inspect_doc(written, get_profile(services.config.data_dir, 'web-animated'), _file_info(output))
        unchanged = sha256_of(Path(src_info['path'])) == src_info['sha256'] and sha256_of(Path(tgt_info['path'])) == tgt_info['sha256']
        receipt.update(output=_file_info(output), fidelity=measured, after={'clips': after['clips'], 'skeleton': after['skeleton']},
                       reimport={'consumer': 'three.js GLTFLoader + AnimationMixer (Node)', **{k: v for k, v in three.items() if k != 'final'},
                                 'comparison': comparison}, original_unchanged=unchanged)
        good = measured['max_rotation_change_error_deg'] <= max_error_deg and (comparison['ok'] or not three.get('available', True))
        receipt['status'] = 'prepared' if unchanged and good else 'failed'
        if receipt['status'] == 'failed':
            receipt['reason'] = ('An input changed during the retarget' if not unchanged else
                                 f"Measured error {measured['max_rotation_change_error_deg']:.4f} deg (limit {max_error_deg}) or the three.js replay did not match")
    except (ValueError, LookupError, KeyError) as exc:
        receipt.update(status='rejected', reason=f'{type(exc).__name__}: {exc}'[:800])
    return _store_receipt(services, receipt)
