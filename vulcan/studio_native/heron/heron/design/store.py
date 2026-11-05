"""Immutable part revisions in Heron's data directory.

A part is created from a spec, then changed only by new revisions (new parameter values or a revised spec); each
revision has its own folder of verified exports. A parameter revision starts the solver from the base revision's
solved geometry, so the regenerated sketch keeps the same configuration of its constraints. A request id replays its
stored result; reusing it for different content is refused.
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
from ..assembly.spec import evaluate
from .spec import Part

ID = re.compile(r'^[a-zA-Z0-9_-]{1,80}$')
WORKER_TIMEOUT = 300


def _digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(',', ':')).encode()).hexdigest()


def normalise(spec):
    return Part.model_validate(spec).model_dump(mode='json', exclude_none=True, exclude_defaults=True)


class PartStore:
    def __init__(self, directory):
        self.directory = Path(directory).resolve()
        self.root = self.directory / 'parts'
        self.root.mkdir(parents=True, exist_ok=True)
        self.path = self.directory / 'parts.sqlite3'
        with self.connect() as db:
            db.execute('CREATE TABLE IF NOT EXISTS revisions (part_id TEXT, revision INTEGER, request_id TEXT UNIQUE, digest TEXT, operation TEXT, '
                       'base_revision INTEGER, spec TEXT, state TEXT, result TEXT, created REAL, PRIMARY KEY (part_id, revision))')

    def connect(self):
        return sqlite3.connect(self.path, timeout=20)

    def _row(self, row):
        part_id, revision, request_id, operation, base, spec, state, result, created = row
        return {'id': part_id, 'revision': revision, 'request_id': request_id, 'operation': operation, 'base_revision': base,
                'spec': json.loads(spec), 'state': state, 'result': json.loads(result) if result else None, 'created': created}

    def get(self, part_id, revision=None):
        if not ID.match(part_id or ''):
            raise ValueError('Invalid part id')
        columns = 'part_id,revision,request_id,operation,base_revision,spec,state,result,created'
        with self.connect() as db:
            if revision is None:
                row = db.execute(f'SELECT {columns} FROM revisions WHERE part_id=? ORDER BY revision DESC LIMIT 1', (part_id,)).fetchone()
            else:
                row = db.execute(f'SELECT {columns} FROM revisions WHERE part_id=? AND revision=?', (part_id, int(revision))).fetchone()
            revisions = [r[0] for r in db.execute('SELECT revision FROM revisions WHERE part_id=? ORDER BY revision', (part_id,))]
        if not row:
            raise LookupError('Part revision not found')
        out = self._row(row)
        out['revisions'] = revisions
        return out

    def list(self):
        with self.connect() as db:
            rows = db.execute('SELECT part_id, MAX(revision), MAX(created) FROM revisions GROUP BY part_id ORDER BY MAX(created) DESC LIMIT 100').fetchall()
        out = []
        for part_id, revision, _ in rows:
            row = self.get(part_id, revision)
            result = row['result'] or {}
            out.append({'id': part_id, 'revision': revision, 'title': row['spec'].get('title'), 'state': row['state'], 'status': result.get('status'),
                        'dof': (result.get('sketch') or {}).get('dof'), 'checks_passed': result.get('checks_passed')})
        return out

    def file(self, part_id, revision, name):
        row = self.get(part_id, revision)
        files = (row['result'] or {}).get('files', {})
        if row['state'] != 'finished' or name not in files:
            raise LookupError('Verified part export not found')
        folder = (self.root / part_id / f'r{int(revision)}').resolve()
        path = (folder / name).resolve()
        if not path.is_relative_to(folder):
            raise ValueError('Invalid file path')
        if hashlib.sha256(path.read_bytes()).hexdigest() != files[name]['sha256']:
            raise ValueError('Export changed since verification')
        return path

    # -- writing -----------------------------------------------------------------------------------------
    def _replay(self, request_id, digest):
        if not ID.match(request_id or ''):
            raise ValueError('Invalid request_id')
        with self.connect() as db:
            row = db.execute('SELECT part_id, revision, digest FROM revisions WHERE request_id=?', (request_id,)).fetchone()
        if row:
            if row[2] != digest:
                raise ValueError('Request id belongs to different content; use a new request_id')
            return self.get(row[0], row[1])
        return None

    def _evaluate(self, part_id, revision, request_id, digest, operation, base_revision, spec, previous=None, start=None, extra=None):
        with self.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            if db.execute('SELECT 1 FROM revisions WHERE request_id=?', (request_id,)).fetchone():
                db.rollback()
                return self._replay(request_id, digest)
            if db.execute('SELECT 1 FROM revisions WHERE part_id=? AND revision=?', (part_id, revision)).fetchone():
                raise ValueError('That revision already exists; read the latest revision and retry')
            db.execute('INSERT INTO revisions VALUES (?,?,?,?,?,?,?,?,?,?)', (part_id, revision, request_id, digest, operation, base_revision,
                                                                               json.dumps(spec), 'running', None, time.time()))
        folder = self.root / part_id / f'r{revision}'
        folder.mkdir(parents=True, exist_ok=False)
        write_text_atomic(folder / 'input.json', json.dumps({'spec': spec, 'previous': previous, 'start': start}, allow_nan=False))
        try:
            proc = run([sys.executable, '-m', 'heron.design.worker', str(folder)], cwd=Path(__file__).resolve().parents[2],
                       timeout=WORKER_TIMEOUT, text=True)
            result = json.loads((folder / 'result.json').read_text(encoding='utf-8'))
            if proc.returncode and result.get('ok'):
                raise ValueError('Worker failed after evaluation')
        except Exception as exc:
            result = {'ok': False, 'error': f'Part worker failed: {type(exc).__name__}. Earlier revisions retained.'}
        if extra:
            result.update(extra)
        imported = result.get('import')
        if imported and 'volume_mm3' in result:  # the OCCT solid against the area x height of the library recipe
            difference = result['volume_mm3'] - imported['expected_volume_mm3']
            imported.update(volume_difference_mm3=difference, volume_matches=abs(difference) <= max(1e-6 * imported['expected_volume_mm3'], 1e-4))
        with self.connect() as db:
            db.execute('UPDATE revisions SET state=?, result=? WHERE part_id=? AND revision=?',
                       ('finished' if result.get('ok') else 'failed', json.dumps(result), part_id, revision))
        return self.get(part_id, revision)

    @staticmethod
    def _start(base):
        sketch = (base.get('result') or {}).get('sketch') or {}
        return {'points': sketch.get('points') or {}, 'radii': sketch.get('radii') or {}} if sketch.get('points') else None

    def create(self, request_id, spec, operation='create', extra=None):
        spec = normalise(spec)
        digest = _digest({'operation': operation, 'spec': spec})
        return self._replay(request_id, digest) or self._evaluate(request_id, 1, request_id, digest, operation, None, spec, extra=extra)

    def _base(self, part_id, base_revision):
        base = self.get(part_id)
        if base['revision'] != base_revision:
            raise ValueError(f"Stale base revision {base_revision}; the latest is {base['revision']}")
        if base['state'] != 'finished':
            raise ValueError('The base revision did not finish; revise it with a corrected spec')
        return base

    def set_parameters(self, part_id, base_revision, request_id, changes):
        digest = _digest({'operation': 'parameters', 'id': part_id, 'base': base_revision, 'changes': changes})
        replay = self._replay(request_id, digest)
        if replay:
            return replay
        base = self._base(part_id, base_revision)
        if not changes:
            raise ValueError('No changes given')
        spec = copy.deepcopy(base['spec'])
        values = (base['result'] or {}).get('parameters') or {}
        applied = []
        for change in changes:
            if not isinstance(change, dict) or set(change) - {'parameter', 'constraint', 'value'} or 'value' not in change or \
                    ('parameter' in change) == ('constraint' in change):
                raise ValueError('A change is {parameter, value} or {constraint, value}')
            new = evaluate(change['value'], values)  # "hole_spacing + 5" reads the base values
            if 'parameter' in change:
                if change['parameter'] not in spec.get('parameters', {}):
                    raise ValueError(f"Unknown parameter {change['parameter']!r}")
                spec['parameters'][change['parameter']] = new
                applied.append({'parameter': change['parameter'], 'from': values.get(change['parameter']), 'to': new})
            else:
                target = next((c for c in spec['sketch'].get('constraints', []) if c['id'] == change['constraint']), None)
                if target is None or 'value' not in target:
                    raise ValueError(f"{change['constraint']!r} is not a dimensioned constraint of this part")
                applied.append({'constraint': change['constraint'], 'from': target['value'], 'to': new})
                target['value'] = new
        spec = normalise(spec)
        return self._evaluate(part_id, base['revision'] + 1, request_id, digest, 'parameters', base['revision'], spec,
                              previous=base['result'], start=self._start(base), extra={'changes': applied})

    def revise(self, part_id, base_revision, request_id, spec):
        spec = normalise(spec)
        digest = _digest({'operation': 'revise', 'id': part_id, 'base': base_revision, 'spec': spec})
        replay = self._replay(request_id, digest)
        if replay:
            return replay
        base = self._base(part_id, base_revision)
        start = self._start(base)
        if start:  # keep the solved position of points and circles that still exist
            start = {'points': {k: v for k, v in start['points'].items() if k in spec['sketch']['points']},
                     'radii': {k: v for k, v in start['radii'].items() if k in spec['sketch'].get('circles', {})}}
        return self._evaluate(part_id, base['revision'] + 1, request_id, digest, 'revise', base['revision'], spec,
                              previous=base['result'], start=start)
