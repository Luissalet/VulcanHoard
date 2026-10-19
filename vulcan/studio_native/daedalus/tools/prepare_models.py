"""Install immutable, verified local weights into this Hoard's model store."""
import hashlib
import json
import os
from pathlib import Path
import shutil

ROOT=Path(__file__).resolve().parents[1]
WORKSPACE=ROOT.parents[1]
STORE=WORKSPACE/'HoardStorage'/'daedalus-models'
STORE.mkdir(parents=True,exist_ok=True)
GEN=ROOT.parent/'trellis2-multigpu'
REF=ROOT.parent/'3d-refine-4gpu-20261015'
records=[]
for component,source in [('generator',GEN/'weights'),('refiner',REF/'weights')]:
    for path in source.rglob('*'):
        if not path.is_file() or path.suffix=='.part':
            continue
        target=STORE/component/path.relative_to(source)
        target.parent.mkdir(parents=True,exist_ok=True)
        if not target.exists():
            try:
                os.link(path,target)
            except OSError:
                shutil.copy2(path,target)
        assert target.stat().st_size==path.stat().st_size
        records.append({'file':str(target.relative_to(STORE)).replace('\\','/'),'bytes':target.stat().st_size})
shutil.copy2(GEN/'pipeline.json',STORE/'generator'/'pipeline.json')
shutil.copy2(GEN/'TRELLIS-LICENSE',STORE/'generator'/'LICENSE')
shutil.copy2(REF/'detailgen-runtime'/'LICENSE',STORE/'refiner'/'LICENSE')
shutil.copy2(GEN/'weights-proof.json',STORE/'generator'/'weights-proof.json')
shutil.copy2(REF/'weights-proof.json',STORE/'refiner'/'weights-proof.json')
(STORE/'manifest.json').write_text(json.dumps({'schema':1,'generator_revision':'430a9d09b2416687018c8fe8edced2ad4858a439',
 'refiner_revision':'ff2fac7768babe768bb73d03e998f80a397a3454','files':records},indent=2))
print(json.dumps({'model_store':str(STORE),'files':len(records),'logical_bytes':sum(row['bytes'] for row in records)}))
