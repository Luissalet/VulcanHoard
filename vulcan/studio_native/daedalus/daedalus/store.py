from contextlib import contextmanager
import json
from pathlib import Path
import sqlite3
import time
import uuid

TERMINAL={'complete','error','cancelled','interrupted'}

class Store:
    def __init__(self,path):
        self.path=Path(path)
        with self.connection() as db:
            db.executescript('''
                PRAGMA journal_mode=WAL;
                CREATE TABLE IF NOT EXISTS images(id TEXT PRIMARY KEY,name TEXT NOT NULL,path TEXT NOT NULL,
                    width INTEGER NOT NULL,height INTEGER NOT NULL,created REAL NOT NULL);
                CREATE TABLE IF NOT EXISTS jobs(id TEXT PRIMARY KEY,kind TEXT NOT NULL,image_id TEXT NOT NULL REFERENCES images(id),
                    parent_id TEXT REFERENCES jobs(id),title TEXT NOT NULL,status TEXT NOT NULL,stage TEXT NOT NULL,
                    progress REAL NOT NULL DEFAULT 0,params TEXT NOT NULL,result TEXT,error TEXT,
                    created REAL NOT NULL,updated REAL NOT NULL);
                CREATE INDEX IF NOT EXISTS jobs_created ON jobs(created DESC);
            ''')

    @contextmanager
    def connection(self):
        db=sqlite3.connect(self.path,timeout=30)
        db.row_factory=sqlite3.Row
        db.execute('PRAGMA foreign_keys=ON')
        try:
            yield db
            db.commit()
        finally:
            db.close()

    def add_image(self,name,path,width,height,image_id=None):
        identity=image_id or uuid.uuid4().hex
        with self.connection() as db:
            db.execute('INSERT INTO images VALUES(?,?,?,?,?,?)',(identity,name,str(path),width,height,time.time()))
        return self.image(identity)

    def image(self,identity):
        with self.connection() as db:
            row=db.execute('SELECT * FROM images WHERE id=?',(identity,)).fetchone()
        return dict(row) if row else None

    def create(self,kind,image_id,title,params,parent_id=None):
        identity=uuid.uuid4().hex
        now=time.time()
        with self.connection() as db:
            db.execute('INSERT INTO jobs VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)',
                (identity,kind,image_id,parent_id,title,'queued','queued',0,json.dumps(params),None,None,now,now))
        return self.job(identity)

    def job(self,identity):
        with self.connection() as db:
            row=db.execute('SELECT * FROM jobs WHERE id=?',(identity,)).fetchone()
        if not row:
            return None
        item=dict(row)
        item['params']=json.loads(item['params'])
        item['result']=json.loads(item['result']) if item['result'] else None
        return item

    def list(self,limit=None):
        with self.connection() as db:
            query='SELECT id FROM jobs ORDER BY created DESC'
            ids=[row[0] for row in (db.execute(query) if limit is None else db.execute(query+' LIMIT ?',(limit,)))]
        return [self.job(identity) for identity in ids]

    def update(self,identity,**values):
        allowed={'status','stage','progress','result','error','title'}
        if set(values)-allowed:
            raise ValueError('Unsupported job update')
        values['updated']=time.time()
        if 'result' in values and values['result'] is not None:
            values['result']=json.dumps(values['result'])
        with self.connection() as db:
            db.execute('UPDATE jobs SET '+','.join(key+'=?' for key in values)+' WHERE id=?',(*values.values(),identity))
        return self.job(identity)

    def request_cancel(self,identity):
        with self.connection() as db:
            row=db.execute('SELECT status FROM jobs WHERE id=?',(identity,)).fetchone()
            if not row:
                return None
            if row['status'] in TERMINAL:
                return self.job(identity)
            status='cancelled' if row['status']=='queued' else 'cancelling'
            db.execute('UPDATE jobs SET status=?,stage=?,updated=? WHERE id=?',(status,status,time.time(),identity))
        return self.job(identity)

    def finish(self,identity,result):
        with self.connection() as db:
            changed=db.execute("UPDATE jobs SET status='complete',stage='complete',progress=1,result=?,error=NULL,updated=? WHERE id=? AND status IN ('running','waiting')",
                               (json.dumps(result),time.time(),identity)).rowcount
        return bool(changed)

    def recover(self):
        with self.connection() as db:
            db.execute("UPDATE jobs SET status='interrupted',stage='interrupted',error=?,updated=? WHERE status IN ('running','waiting','cancelling')",
                       ('La aplicación se cerró antes de terminar. Puedes iniciar una nueva versión.',time.time()))

    def versions(self,identity):
        job=self.job(identity)
        if not job:
            return []
        with self.connection() as db:
            ids=[row[0] for row in db.execute('SELECT id FROM jobs WHERE image_id=? ORDER BY created DESC',(job['image_id'],))]
        return [self.job(identity) for identity in ids]
