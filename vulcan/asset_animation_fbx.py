"""FBX for the animated-asset tools, through Blender in the background (the newest install, or VULCAN_BLENDER).

Import: an FBX is converted once to a GLB (cached by the FBX's SHA-256 under ``<data>/anim_imports``) and that GLB is what
inspection and preparation read; the receipt says what Blender read (actions and frame ranges, armatures and bones, shape
keys) next to what the GLB holds, so a lost clip or bone shows up. Export: a GLB/glTF (a library model, a path or a prepared
variant) is written as FBX under ``<data>/anim_exports``, then Blender reads that FBX back and the result is compared with
the source: clip names and durations, joint names, morph target names and the world position of every joint at the start
and end of every clip. The original files are never written.
"""
from __future__ import annotations

import json
import math
import os
import re
import subprocess
import tempfile
import time
import uuid
from pathlib import Path

import numpy as np

from .hoard_link import atomic

WORKER = Path(__file__).with_name('blender_anim_worker.py')
TIMEOUT_S = 900
POSITION_TOLERANCE_MM = 1.0


def status():
    from .blender_render import blender_status
    info = blender_status()
    return {'available': info['available'], 'path': info['path'], 'version': info['version'], 'note': info['note']}


def _folder(services, name):
    folder = Path(services.config.data_dir) / name
    folder.mkdir(parents=True, exist_ok=True)
    return folder


def run_blender(mode, source, target, timeout=TIMEOUT_S):
    """Run the worker; returns its summary (what Blender read) or raises RuntimeError with Blender's own error tail."""
    from .blender_render import find_blender
    blender = find_blender()
    if not blender:
        raise RuntimeError('Blender not found: install it or set VULCAN_BLENDER to blender.exe.')
    with tempfile.TemporaryDirectory(prefix='vulcan-fbx-') as tmp:
        summary = Path(tmp) / 'summary.json'
        flags = 0x08000000 if os.name == 'nt' else 0  # no console window
        done = subprocess.run([blender, '-b', '--factory-startup', '--python-exit-code', '3', '--python', str(WORKER), '--', mode,
                               str(source), str(target), str(summary)], capture_output=True, text=True, timeout=timeout,
                              creationflags=flags, encoding='utf-8', errors='replace')
        if done.returncode or not summary.is_file() or not Path(target).is_file():
            tail = '\n'.join((done.stdout or '').splitlines()[-6:] + (done.stderr or '').splitlines()[-6:])
            raise RuntimeError(f'Blender could not convert the file ({mode}): {tail[-700:]}')
        out = json.loads(summary.read_text(encoding='utf-8'))
    out['blender_path'] = blender
    return out


def clip_key(name):
    """Clip names as an exporter may prefix them ('Armature|idle', 'Take 001|idle'): the part after the last '|'."""
    return (name or '').split('|')[-1].strip()


def _facts(doc):
    from . import asset_animation as aa
    clips = aa.clip_facts(doc)
    skeleton = aa.skeleton_facts(doc) if not doc.compressed() else None
    return {'clips': [{'index': c['index'], 'name': c['name'], 'duration_s': c['duration_s'], 'channels': c['channels'], 'key_rate_fps': c['key_rate_fps'],
                       'holds_one_pose': all(r['constant'] for r in c['channel_rows'])} for c in clips],
            'bones': (skeleton or {}).get('max_bones', 0), 'morph': aa.morph_facts(doc)}


def import_fbx(services, model_id=None, path=None, force=False):
    """Convert an FBX to a GLB once (cached by content) and describe both sides; the GLB feeds inspection and preparation."""
    from .asset_animation import Doc, _file_info
    from .asset_inspection import resolve_source, sha256_of
    source, model = resolve_source(services, model_id, path)
    if source.suffix.lower() != '.fbx':
        raise ValueError(f'FBX import reads .fbx files; {source.suffix} is read directly')
    digest = sha256_of(source)
    folder = _folder(services, 'anim_imports')
    key = digest[:20]
    receipt_path = folder / f'{key}.json'
    glb = folder / f'{re.sub(r"[^A-Za-z0-9_.-]+", "_", source.stem)[:60]}.{key}.glb'
    if not force and receipt_path.is_file() and glb.is_file():
        cached = json.loads(receipt_path.read_text(encoding='utf-8'))
        if cached.get('output', {}).get('sha256') == sha256_of(glb):
            return {**cached, 'cached': True}
    started = time.monotonic()
    tmp = glb.with_name(glb.stem + f'.{os.getpid()}.tmp.glb')
    summary = run_blender('fbx2glb', source, tmp)
    atomic.replace_with_retry(tmp, glb)
    doc = Doc.load(glb)
    facts = _facts(doc)
    read = summary.get('read') or {}
    exact = {a['name']: a for a in read.get('actions', [])}
    by_key = {clip_key(a['name']): a for a in read.get('actions', [])}
    rows = []
    for clip in facts['clips']:
        action = exact.get(clip['name']) or by_key.get(clip_key(clip['name']))
        same = bool(action) and abs((action['duration_s'] or 0) - clip['duration_s']) <= 1.5 / (read.get('fps') or 24)
        row = {'clip': clip['name'], 'action': action and action['name'], 'duration_s': clip['duration_s'],
               'blender_duration_s': action and action['duration_s'], 'matches': same or bool(action) and clip['holds_one_pose']}
        if action and not same and clip['holds_one_pose']:
            row['note'] = 'the clip holds one pose, so the GLB keeps a single key instead of the whole frame range'
        rows.append(row)
    receipt = {'id': key, 'kind': 'fbx_import', 'created': time.time(), 'seconds': round(time.monotonic() - started, 2),
               'model': {'id': model['id'], 'name': model.get('name')} if model else None,
               'source': {'path': str(source), 'format': 'fbx', 'bytes': source.stat().st_size, 'sha256': digest},
               'output': _file_info(glb), 'blender': {'path': summary['blender_path'], 'version': read.get('blender'), 'importer': summary.get('importer')},
               'read_by_blender': read, 'glb': facts, 'clips_vs_actions': rows,
               'original_unchanged': sha256_of(source) == digest,
               'note': 'Inspection and preparation read this GLB; the FBX is never written.' if facts['clips'] else
                       'Blender found no animation in this FBX; the GLB holds the mesh and skeleton only.'}
    atomic.write_json_atomic(receipt_path, receipt)
    return receipt


def readable_source(services, model_id=None, path=None):
    """(path of a GLB/glTF to read, conversion receipt or None): an FBX goes through import_fbx first."""
    from .asset_inspection import resolve_source
    source, model = resolve_source(services, model_id, path)
    if source.suffix.lower() == '.fbx':
        receipt = import_fbx(services, model_id, path)
        return Path(receipt['output']['path']), model, {'from': receipt['source'], 'import_id': receipt['id'], 'cached': receipt.get('cached', False),
                                                         'blender': receipt['blender']}
    return source, model, None


def compare_roundtrip(source_doc, back_doc, fps=24.0):
    """What survived GLB -> FBX -> (Blender) -> GLB: clips by name and duration, joints, morph target names, joint world positions."""
    from .asset_animation_rig import Rig
    a_rig, b_rig = Rig(source_doc), Rig(back_doc)
    b_clips = {}  # Blender names takes 'Armature|Armature|idle' and the shape keys of the same take 'Key|Armature|idle': keep the fuller one
    for i, anim in enumerate(back_doc.gltf.get('animations', [])):
        key = clip_key(anim.get('name'))
        if key not in b_clips or len(anim.get('channels', [])) > len(back_doc.gltf['animations'][b_clips[key]].get('channels', [])):
            b_clips[key] = i
    rows, worst, lost = [], 0.0, []
    for i, anim in enumerate(source_doc.gltf.get('animations', [])):
        name = anim.get('name') or f'clip_{i}'
        j = b_clips.get(clip_key(name))
        row = {'clip': name, 'found': j is not None}
        paths = {c.get('target', {}).get('path') for c in anim.get('channels', [])}
        if j is None and paths == {'weights'}:
            row['lost_by_fbx'] = ('a clip that only drives morph targets: Blender writes shape key animation only inside the take of an '
                                  'animated object, so it has no take of its own in the FBX')
            lost.append(name)
            rows.append(row)
            continue
        a_span = a_rig.span(i)
        row['duration_s'] = a_span[1] - a_span[0]
        if j is not None:
            b_span = b_rig.span(j)
            row.update(back_name=back_doc.gltf['animations'][j].get('name'), back_duration_s=b_span[1] - b_span[0])
            row['duration_matches'] = abs(row['back_duration_s'] - row['duration_s']) <= 1.0 / fps + 1e-6
            error = 0.0
            for fa, fb in ((a_span[0], b_span[0]), (a_span[1], b_span[1])):
                pa, pb = a_rig.joint_positions(a_rig.channels(i), fa), b_rig.joint_positions(b_rig.channels(j), fb)
                common = set(pa) & set(pb)
                error = max([error] + [float(np.linalg.norm(pa[n] - pb[n])) * 1000 for n in common])
            row['max_joint_position_error_mm'] = error
            worst = max(worst, error)
        rows.append(row)
    a_joints, b_joints = [a_rig.names[j] for j in a_rig.joints()], set(b_rig.names[j] for j in b_rig.joints())
    names = lambda doc: sorted({n for m in doc.gltf.get('meshes', []) for n in ((m.get('extras') or {}).get('targetNames') or [])})  # noqa: E731
    morph_missing = sorted(set(names(source_doc)) - set(names(back_doc)))
    out = {'clips': rows, 'joints': len(a_joints), 'joints_missing': [n for n in a_joints if n not in b_joints],
           'morph_targets_missing': morph_missing, 'max_joint_position_error_mm': worst, 'tolerance_mm': POSITION_TOLERANCE_MM,
           'clips_lost_by_fbx': lost}
    out['ok'] = (all(r['found'] and r['duration_matches'] for r in rows if 'lost_by_fbx' not in r) and not out['joints_missing']
                 and not morph_missing and worst <= POSITION_TOLERANCE_MM)
    return out


def export_fbx(services, model_id=None, path=None, preparation_id=None):
    """Write a GLB/glTF as FBX with Blender, read the FBX back with Blender and compare it with the source."""
    from .asset_animation import Doc, get_preparation
    from .asset_inspection import resolve_source, sha256_of
    model = None
    if preparation_id:
        prepared = get_preparation(services, preparation_id)
        if not prepared.get('output'):
            raise ValueError('That preparation wrote no file')
        source = Path(prepared['output']['path'])
    else:
        source, model = resolve_source(services, model_id, path)
    if source.suffix.lower() not in ('.glb', '.gltf'):
        raise ValueError(f'FBX export reads a GLB or glTF; {source.suffix} is not one')
    digest = sha256_of(source)
    export_id = time.strftime('%Y%m%d%H%M%S') + uuid.uuid4().hex[:6]
    folder = _folder(services, 'anim_exports')
    out = folder / f'{re.sub(r"[^A-Za-z0-9_.-]+", "_", source.stem)[:60]}.{export_id}.fbx'
    started = time.monotonic()
    receipt = {'id': export_id, 'kind': 'fbx_export', 'created': time.time(), 'preparation_id': preparation_id,
               'model': {'id': model['id'], 'name': model.get('name')} if model else None,
               'source': {'path': str(source), 'bytes': source.stat().st_size, 'sha256': digest}}
    try:
        written = run_blender('glb2fbx', source, out)
        receipt.update(output={'path': str(out), 'format': 'fbx', 'bytes': out.stat().st_size, 'sha256': sha256_of(out)},
                       blender={'path': written['blender_path'], 'version': (written.get('read') or {}).get('blender')},
                       read_by_blender=written.get('read'))
        with tempfile.TemporaryDirectory(prefix='vulcan-fbx-back-') as tmp:
            back = Path(tmp) / 'back.glb'
            reread = run_blender('fbx2glb', out, back)
            comparison = compare_roundtrip(Doc.load(source), Doc.load(back), fps=(reread.get('read') or {}).get('fps') or 24.0)
        receipt.update(roundtrip={'consumer': 'Blender FBX importer, re-exported to GLB and compared here', **comparison},
                       original_unchanged=sha256_of(source) == digest)
        if not (comparison['ok'] and receipt['original_unchanged']):
            receipt.update(status='mismatch', reason='The FBX read back by Blender does not match the source; see roundtrip')
        elif comparison['clips_lost_by_fbx']:
            receipt.update(status='partial', reason='Everything else matches, but these morph-only clips have no take in the FBX: '
                           + ', '.join(comparison['clips_lost_by_fbx']))
        else:
            receipt['status'] = 'exported'
    except (RuntimeError, subprocess.TimeoutExpired, ValueError) as exc:
        receipt.update(status='failed', reason=f'{type(exc).__name__}: {exc}'[:900])
    receipt['seconds'] = round(time.monotonic() - started, 2)
    atomic.write_json_atomic(folder / f'{export_id}.json', receipt)
    return receipt


def get_export(services, export_id):
    if not re.fullmatch(r'[0-9a-f]{20}', export_id or ''):
        raise ValueError('Invalid export id')
    file = _folder(services, 'anim_exports') / f'{export_id}.json'
    if not file.is_file():
        raise LookupError('Export not found')
    return json.loads(file.read_text(encoding='utf-8'))


def list_exports(services, limit=20):
    rows = []
    for file in sorted(_folder(services, 'anim_exports').glob('*.json'), reverse=True)[:limit]:
        try:
            row = json.loads(file.read_text(encoding='utf-8'))
        except (OSError, ValueError):
            continue
        rows.append({k: row.get(k) for k in ('id', 'status', 'created', 'preparation_id', 'reason')} |
                    {'output': (row.get('output') or {}).get('path'), 'source': (row.get('source') or {}).get('path'),
                     'roundtrip_ok': (row.get('roundtrip') or {}).get('ok')})
    return rows
