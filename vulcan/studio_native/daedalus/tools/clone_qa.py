"""Copy this task's idle private fixture without stopping its server."""
import json
from pathlib import Path
import shutil
import sqlite3
import requests

ROOT=Path(__file__).resolve().parents[1]
OLD=ROOT.parent/'daedalus-qa622-20261015'
NEW=ROOT.parent/'daedalus-qa622b-20261015'
jobs=requests.get('http://127.0.0.1:63622/api/jobs',timeout=10).json()['jobs']
assert not any(job['status'] in {'queued','waiting','running','cancelling'} for job in jobs)
NEW.mkdir(exist_ok=True)
assert not (NEW/'daedalus.db').exists(),'Preserve an existing fixture'
with sqlite3.connect(OLD/'daedalus.db') as source,sqlite3.connect(NEW/'daedalus.db') as destination:
    source.backup(destination)
for name in ['images','jobs']:
    shutil.copytree(OLD/name,NEW/name)
with sqlite3.connect(NEW/'daedalus.db') as db:
    rows=db.execute('SELECT id,path FROM images').fetchall()
    for identity,path in rows:
        relative=Path(path).relative_to(OLD)
        db.execute('UPDATE images SET path=? WHERE id=?',(str(NEW/relative),identity))
    db.commit()
(NEW/'fixture-origin.json').write_text(json.dumps({'source':str(OLD),'source_service_left_idle':True,
     'copied_job_ids':[job['id'] for job in jobs],'changed':'image paths only, into copied fixture; tokens/space IDs fresh'},indent=2))
print('Cloned owned idle QA artifacts; original server and source fixture unchanged.')
