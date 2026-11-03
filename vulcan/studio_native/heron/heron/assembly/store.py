"""Immutable assembly revisions in Heron's data directory.

Every create, parameter change or import is a new revision with its own folder of exports; earlier revisions
and the Heron builds they reference are never rewritten. Evaluation runs in a worker process with a timeout,
like CAD builds. A request id replays its stored result; reusing it for different content is refused.
"""
from __future__ import annotations

import copy
import hashlib
import json
import re
import sqlite3
import sys
import time
from pathlib import Path

from ..hoard_link.atomic import write_text_atomic
from ..hoard_link.proc import run
from .spec import evaluate

ID = re.compile(r'^[a-zA-Z0-9_-]{1,80}$')
WORKER_TIMEOUT = 600


def prevalidate(spec):
    from .engine import prevalidate as check  # numpy/scipy load only when an assembly is written
    return check(spec)


def _digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(',', ':')).encode()).hexdigest()


class AssemblyStore:
    def __init__(self, directory, builds):
        self.directory = Path(directory).resolve()
        self.root = self.directory / 'assemblies'
        self.root.mkdir(parents=True, exist_ok=True)
        self.builds = builds
        self.path = self.directory / 'assemblies.sqlite3'
        with self.connect() as db:
            db.execute('CREATE TABLE IF NOT EXISTS revisions (assembly_id TEXT, revision INTEGER, request_id TEXT UNIQUE, digest TEXT, '
                       'operation TEXT, base_revision INTEGER, spec TEXT, state TEXT, result TEXT, created REAL, PRIMARY KEY (assembly_id, revision))')
            db.execute('CREATE TABLE IF NOT EXISTS checks (request_id TEXT PRIMARY KEY, digest TEXT, assembly_id TEXT, revision INTEGER, '
                       'arguments TEXT, state TEXT, result TEXT, created REAL)')

    def connect(self):
        return sqlite3.connect(self.path, timeout=20)

    # -- reading -----------------------------------------------------------------------------------------
    def _row(self, row):
        assembly_id, revision, request_id, operation, base, spec, state, result, created = row
        return {'id': assembly_id, 'revision': revision, 'request_id': request_id, 'operation': operation, 'base_revision': base,
                'spec': json.loads(spec), 'state': state, 'result': json.loads(result) if result else None, 'created': created}

    def get(self, assembly_id, revision=None):
        if not ID.match(assembly_id or ''):
            raise ValueError('Invalid assembly id')
        columns = 'assembly_id,revision,request_id,operation,base_revision,spec,state,result,created'
        with self.connect() as db:
            if revision is None:
                row = db.execute(f'SELECT {columns} FROM revisions WHERE assembly_id=? ORDER BY revision DESC LIMIT 1', (assembly_id,)).fetchone()
            else:
                row = db.execute(f'SELECT {columns} FROM revisions WHERE assembly_id=? AND revision=?', (assembly_id, int(revision))).fetchone()
            revisions = [r[0] for r in db.execute('SELECT revision FROM revisions WHERE assembly_id=? ORDER BY revision', (assembly_id,))]
        if not row:
            raise LookupError('Assembly revision not found')
        out = self._row(row)
        out['revisions'] = revisions
        return out

    def list(self):
        with self.connect() as db:
            rows = db.execute('SELECT assembly_id, MAX(revision), MAX(created) FROM revisions GROUP BY assembly_id ORDER BY MAX(created) DESC LIMIT 100').fetchall()
        out = []
        for assembly_id, revision, _ in rows:
            row = self.get(assembly_id, revision)
            result = row['result'] or {}
            out.append({'id': assembly_id, 'revision': revision, 'title': row['spec'].get('title'), 'state': row['state'],
                        'status': result.get('status'), 'interference': (result.get('interference') or {}).get('verdict')})
        return out

    def file(self, assembly_id, revision, name):
        row = self.get(assembly_id, revision)
        files = (row['result'] or {}).get('files', {})
        if row['state'] != 'finished' or name not in files:
            raise LookupError('Verified assembly export not found')
        folder = (self.root / assembly_id / f'r{int(revision)}').resolve()
        path = (folder / name).resolve()
        if not path.is_relative_to(folder):
            raise ValueError('Invalid file path')
        if hashlib.sha256(path.read_bytes()).hexdigest() != files[name]['sha256']:
            raise ValueError('Export changed since verification')
        return path

    # -- writing -----------------------------------------------------------------------------------------
    def _builds_for(self, spec):
        builds = {}
        for component in spec['components']:
            source = component['source']
            if source['kind'] != 'build':
                continue
            row = self.builds.get(source['build_id'])
            if row['state'] != 'finished':
                raise ValueError(f"Build {source['build_id']} is not finished")
            path = self.builds.file(source['build_id'], 'model.step')
            builds[source['build_id']] = {'step_path': str(path), 'sha256': row['result']['files']['model.step']['sha256'], 'recipe': row['recipe']}
        return builds

    def _replay(self, request_id, digest):
        with self.connect() as db:
            row = db.execute('SELECT assembly_id, revision, digest FROM revisions WHERE request_id=?', (request_id,)).fetchone()
        if row:
            if row[2] != digest:
                raise ValueError('Request id belongs to different content; use a new request_id')
            return self.get(row[0], row[1])
        return None

    def _evaluate(self, assembly_id, revision, request_id, digest, operation, base_revision, spec, previous=None, initial_poses=None):
        if not ID.match(request_id or ''):
            raise ValueError('Invalid request_id')
        builds = self._builds_for(spec)
        with self.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            again = db.execute('SELECT assembly_id, revision, digest FROM revisions WHERE request_id=?', (request_id,)).fetchone()
            if again:
                db.rollback()
                return self._replay(request_id, digest)
            if db.execute('SELECT 1 FROM revisions WHERE assembly_id=? AND revision=?', (assembly_id, revision)).fetchone():
                raise ValueError('That revision already exists; read the latest revision and retry')
            db.execute('INSERT INTO revisions VALUES (?,?,?,?,?,?,?,?,?,?)', (assembly_id, revision, request_id, digest, operation, base_revision,
                                                                               json.dumps(spec), 'running', None, time.time()))
        folder = self.root / assembly_id / f'r{revision}'
        folder.mkdir(parents=True, exist_ok=False)
        payload = {'mode': 'evaluate', 'spec': spec, 'builds': builds, 'previous': previous, 'initial_poses': initial_poses,
                   'assembly_id': assembly_id, 'revision': revision}
        result = self._work(folder, payload)
        with self.connect() as db:
            db.execute('UPDATE revisions SET state=?, result=? WHERE assembly_id=? AND revision=?',
                       ('finished' if result.get('ok') else 'failed', json.dumps(result), assembly_id, revision))
        return self.get(assembly_id, revision)

    def _work(self, folder, payload):
        write_text_atomic(folder / 'input.json', json.dumps(payload, allow_nan=False))
        try:
            proc = run([sys.executable, '-m', 'heron.assembly.worker', str(folder)], cwd=Path(__file__).resolve().parents[2],
                       timeout=WORKER_TIMEOUT, text=True)
            result = json.loads((folder / 'result.json').read_text(encoding='utf-8'))
            if proc.returncode and result.get('ok'):
                raise ValueError('Worker failed after evaluation')
        except Exception as exc:
            result = {'ok': False, 'error': f'Assembly worker failed: {type(exc).__name__}. Originals retained.'}
        return result

    def create(self, request_id, spec):
        spec = prevalidate(spec)
        digest = _digest({'operation': 'create', 'spec': spec})
        return self._replay(request_id, digest) or self._evaluate(request_id, 1, request_id, digest, 'create', None, spec)

    def set_parameters(self, assembly_id, base_revision, request_id, changes):
        digest = _digest({'operation': 'parameters', 'id': assembly_id, 'base': base_revision, 'changes': changes})
        replay = self._replay(request_id, digest)
        if replay:
            return replay
        base = self.get(assembly_id)
        if base['revision'] != base_revision:
            raise ValueError(f"Stale base revision {base_revision}; the latest is {base['revision']}")
        if base['state'] != 'finished':
            raise ValueError('The base revision did not finish; fix it with a new assembly')
        if not changes:
            raise ValueError('No changes given')
        spec = copy.deepcopy(base['spec'])
        for change in changes:
            if set(change) - {'variable', 'component', 'parameter', 'value'}:
                raise ValueError('A change has variable/value or component/parameter/value')
            if 'variable' in change:
                if change['variable'] not in spec['variables']:
                    raise ValueError(f"Unknown variable {change['variable']!r}")
                spec['variables'][change['variable']] = evaluate(change['value'], spec['variables'])
                continue
            component = next((c for c in spec['components'] if c['id'] == change.get('component')), None)
            if component is None or component['source']['kind'] != 'family':
                raise ValueError(f"{change.get('component')!r} is not a parametric component of this assembly")
            if not isinstance(change.get('parameter'), str):
                raise ValueError('parameter is required')
            component['source'].setdefault('params', {})[change['parameter']] = change['value']
        spec = prevalidate(spec)
        return self._evaluate(assembly_id, base['revision'] + 1, request_id, digest, 'parameters', base['revision'], spec, previous=base['result'])

    def import_manifest(self, request_id, manifest):
        if not isinstance(manifest, dict) or manifest.get('format') != 'heron-assembly-manifest' or manifest.get('version') != 1:
            raise ValueError('Not a Heron assembly manifest (format heron-assembly-manifest, version 1)')
        spec = prevalidate(manifest.get('spec'))
        poses = {c['id']: c['pose']['matrix'] for c in manifest.get('components', [])}
        digest = _digest({'operation': 'import', 'manifest': manifest})
        replay = self._replay(request_id, digest)
        if replay:
            return replay
        row = self._evaluate(request_id, 1, request_id, digest, 'import', None, spec, initial_poses=poses)
        if row['state'] == 'finished':
            import numpy as np
            errors = {cid: float(np.abs(np.array(row['result']['poses'][cid]['matrix']) - np.array(m)).max()) for cid, m in poses.items() if cid in row['result']['poses']}
            row['import'] = {'source': {'assembly_id': manifest.get('assembly_id'), 'revision': manifest.get('revision')},
                             'max_pose_difference': max(errors.values() or [0.0]), 'per_component': errors,
                             'components_match': set(poses) == set(row['result']['poses'])}
            row['result']['import'] = row['import']
            with self.connect() as db:
                db.execute('UPDATE revisions SET result=? WHERE assembly_id=? AND revision=?', (json.dumps(row['result']), row['id'], row['revision']))
        return row

    def revision_check(self, request_id, assembly_id, revision, component, source):
        if not ID.match(request_id or ''):
            raise ValueError('Invalid request_id')
        base = self.get(assembly_id, revision)
        if base['state'] != 'finished' or base['result']['status'] == 'ambiguous':
            raise ValueError('Revision checks need a finished assembly with chosen poses')
        arguments = {'assembly_id': assembly_id, 'revision': base['revision'], 'component': component, 'source': source}
        digest = _digest(arguments)
        with self.connect() as db:
            row = db.execute('SELECT digest, result FROM checks WHERE request_id=?', (request_id,)).fetchone()
        if row:
            if row[0] != digest:
                raise ValueError('Request id belongs to a different check; use a new request_id')
            return json.loads(row[1])
        spec_b = copy.deepcopy(base['spec'])
        for c in spec_b['components']:
            if c['id'] == component:
                c['source'] = source
        builds = {**self._builds_for(base['spec']), **self._builds_for(spec_b)}
        folder = self.root / assembly_id / 'checks' / request_id
        folder.mkdir(parents=True, exist_ok=False)
        result = self._work(folder, {'mode': 'revision_check', 'spec': base['spec'], 'base': base['result'], 'component': component,
                                     'source': source, 'builds': builds})
        result.update(assembly_id=assembly_id, revision=base['revision'], request_id=request_id)
        with self.connect() as db:
            db.execute('INSERT INTO checks VALUES (?,?,?,?,?,?,?,?)', (request_id, digest, assembly_id, base['revision'], json.dumps(arguments),
                                                                     'finished' if result.get('ok') else 'failed', json.dumps(result), time.time()))
        return result
