"""Persistent project links without opening or translating native stores."""
from __future__ import annotations

import hashlib
import json
import threading
import time
import uuid
from pathlib import Path

from ..hoard_link.atomic import write_json_atomic


class Projects:
    def __init__(self, data_dir):
        self.path = Path(data_dir) / 'studio-projects.json'
        self.lock = threading.RLock()

    def _read(self):
        return json.loads(self.path.read_text(encoding='utf-8')) if self.path.exists() else {'schema': 1, 'projects': {}}

    def list(self):
        with self.lock:
            return list(self._read()['projects'].values())

    def get(self, identity):
        with self.lock:
            project = self._read()['projects'].get(identity)
            if project is None:
                raise KeyError('Unknown studio project')
            return project

    def create(self, title):
        title = title.strip()
        if not title or len(title) > 200:
            raise ValueError('Project title must contain 1 to 200 characters')
        with self.lock:
            state = self._read()
            project = {'id': uuid.uuid4().hex, 'title': title, 'created_at': time.time(), 'links': [], 'artifacts': []}
            state['projects'][project['id']] = project
            write_json_atomic(self.path, state)
            return project

    def link(self, identity, module, native_id, revision=None, title=None, url=None):
        if not isinstance(native_id, str) or not native_id or len(native_id) > 500:
            raise ValueError('A native object id is required')
        with self.lock:
            state = self._read()
            project = state['projects'][identity]
            link = {'module': module, 'native_id': native_id, 'revision': revision}
            if not any(all(existing.get(key) == value for key,value in link.items()) for existing in project['links']):
                if title:
                    link['title'] = title
                if url:
                    link['url'] = url
                project['links'].append(link)
                write_json_atomic(self.path, state)
            return project

    def artifact(self, identity, module, native_id, path, revision=None):
        path = Path(path).expanduser().resolve(strict=True)
        if not path.is_file():
            raise ValueError('An artifact must be a regular file')
        before = path.stat()
        digest = hashlib.sha256()
        with path.open('rb') as stream:
            while chunk := stream.read(1024 * 1024):
                digest.update(chunk)
        after = path.stat()
        if (before.st_size, before.st_mtime_ns) != (after.st_size, after.st_mtime_ns):
            raise ValueError('Artifact changed while being read; register the completed revision')
        row = {'module': module, 'native_id': native_id, 'revision': revision, 'path': str(path),
               'sha256': digest.hexdigest(), 'bytes': after.st_size, 'recorded_at': time.time()}
        with self.lock:
            state = self._read()
            project = state['projects'][identity]
            project['artifacts'].append(row)
            write_json_atomic(self.path, state)
            return project


TOOLS = [
    {'name': 'vulcan_studio_objects', 'description': 'Read existing objects from an explicitly enabled native module; never activates it.',
     'inputSchema': {'type':'object','properties':{'module':{'type':'string'}},'required':['module']}, 'annotations': {'readOnlyHint':True}},
    {'name': 'vulcan_studio_projects', 'description': 'Read shared 3D projects, native links and retained artifact revisions without starting engines.',
     'inputSchema': {'type': 'object', 'properties': {'id': {'type': 'string'}}}, 'annotations': {'readOnlyHint': True}},
    {'name': 'vulcan_studio_create', 'description': 'Create a shared 3D project without creating native objects or activating engines.',
     'inputSchema': {'type': 'object', 'properties': {'title': {'type': 'string', 'minLength': 1, 'maxLength': 200}}, 'required': ['title']}},
    {'name': 'vulcan_studio_link', 'description': 'Associate an existing native object and its revision with a shared 3D project; preserve its id and format.',
     'inputSchema': {'type': 'object', 'properties': {'id': {'type': 'string'}, 'module': {'type': 'string'},
         'native_id': {'type': 'string'}, 'title':{'type':'string'}, 'revision': {'type': ['string', 'null']}}, 'required': ['id', 'module', 'native_id']}},
    {'name': 'vulcan_studio_artifact', 'description': 'Retain a completed artifact reference with SHA256 and native identity; never copy or modify the original.',
     'inputSchema': {'type': 'object', 'properties': {'id': {'type': 'string'}, 'module': {'type': 'string'},
         'native_id': {'type': 'string'}, 'path': {'type': 'string'}, 'revision': {'type': ['string', 'null']}},
         'required': ['id', 'module', 'native_id', 'path']}},
    {'name':'vulcan_studio_index_artifact','description':'Index one retained STL/3MF/OBJ with the native library and link its real model ID to the project. The library must be enabled.',
     'inputSchema':{'type':'object','properties':{'id':{'type':'string'},'artifact_index':{'type':'integer','minimum':0}},'required':['id','artifact_index']}},
]
