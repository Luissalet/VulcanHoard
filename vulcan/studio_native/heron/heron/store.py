"""Durable idempotent CAD receipts. Unknown workers are not automatically re-run."""
import hashlib
import json
import re
import sqlite3
import sys
from pathlib import Path
from .hoard_link.atomic import write_text_atomic
from .hoard_link.proc import run
from .geometry import Recipe


class Store:
    def __init__(self,directory):
        self.directory=Path(directory).resolve();self.directory.mkdir(parents=True,exist_ok=True)
        self.path=self.directory/'cad.sqlite3'
        with self.connect() as db:
            db.execute('CREATE TABLE IF NOT EXISTS builds (id TEXT PRIMARY KEY,digest TEXT,recipe TEXT,state TEXT,result TEXT)')

    def connect(self):return sqlite3.connect(self.path,timeout=20)

    def get(self,id):
        if not re.fullmatch(r'[a-zA-Z0-9_-]{1,80}',id):raise ValueError('Invalid build id')
        with self.connect() as db:row=db.execute('SELECT recipe,state,result FROM builds WHERE id=?',(id,)).fetchone()
        if not row:raise LookupError('Build not found')
        return {'id':id,'recipe':json.loads(row[0]),'state':row[1],'result':json.loads(row[2]) if row[2] else None}

    def list(self):
        with self.connect() as db:rows=db.execute('SELECT id FROM builds ORDER BY rowid DESC LIMIT 100').fetchall()
        return [self.get(r[0]) for r in rows]

    def build(self,id,recipe):
        if not re.fullmatch(r'[a-zA-Z0-9_-]{1,80}',id):raise ValueError('Invalid request_id')
        recipe=Recipe.model_validate(recipe);text=recipe.model_dump_json();digest=hashlib.sha256(text.encode()).hexdigest()
        with self.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            row=db.execute('SELECT digest FROM builds WHERE id=?',(id,)).fetchone()
            if row:
                if row[0]!=digest:raise ValueError('Request id belongs to a different recipe; use a new id for a revision')
                return self.get(id)
            if db.execute("SELECT COUNT(*) FROM builds WHERE state='running'").fetchone()[0]:
                raise ValueError('Another CAD build is unresolved; inspect it before submitting another')
            db.execute('INSERT INTO builds VALUES (?,?,?,\'running\',NULL)',(id,digest,text))
        folder=self.directory/id;folder.mkdir()
        write_text_atomic(folder/'recipe.json',text)
        try:
            proc=run([sys.executable,'-m','heron.worker',str(folder)],cwd=Path(__file__).resolve().parent.parent,timeout=120,text=True)
            result=json.loads((folder/'result.json').read_text(encoding='utf-8'))
            if proc.returncode and result.get('ok'):raise ValueError('Worker failed after export')
        except Exception as exc:
            result={'ok':False,'error':f'CAD worker failed: {type(exc).__name__}. Inspect build artifacts; originals retained.'}
        with self.connect() as db:db.execute('UPDATE builds SET state=?,result=? WHERE id=?',('finished' if result.get('ok') else 'failed',json.dumps(result),id))
        return self.get(id)

    def file(self,id,name):
        row=self.get(id)
        if row['state']!='finished' or name not in row['result'].get('files',{}):raise LookupError('Verified export not found')
        path=(self.directory/id/name).resolve()
        if not path.is_relative_to(self.directory):raise ValueError('Invalid file path')
        expected=row['result']['files'][name]['sha256']
        if hashlib.sha256(path.read_bytes()).hexdigest()!=expected:raise ValueError('Export changed since verification')
        return path
