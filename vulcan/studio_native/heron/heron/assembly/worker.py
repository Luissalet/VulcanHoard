import json
import sys
import traceback
from pathlib import Path

from ..hoard_link.atomic import write_text_atomic
from . import engine

if __name__ == '__main__':
    folder = Path(sys.argv[1])
    try:
        job = json.loads((folder / 'input.json').read_text(encoding='utf-8'))
        if job['mode'] == 'evaluate':
            result = engine.evaluate_revision(job['spec'], builds=job['builds'], previous=job.get('previous'), initial_poses=job.get('initial_poses'),
                                              folder=folder, assembly_id=job['assembly_id'], revision=job['revision'])
        else:
            result = engine.revision_check(job['spec'], job['base'], job['component'], job['source'], builds=job['builds'])
    except Exception as exc:
        detail = traceback.format_exc(limit=3)
        result = {'ok': False, 'error': f'{type(exc).__name__}: {exc}'[:1000], 'trace': detail[-1500:]}
    write_text_atomic(folder / 'result.json', json.dumps(result, ensure_ascii=False, allow_nan=False))
    raise SystemExit(0 if result['ok'] else 1)
