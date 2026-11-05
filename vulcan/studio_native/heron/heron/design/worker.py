import json
import sys
import traceback
from pathlib import Path

from ..hoard_link.atomic import write_text_atomic
from . import solid

if __name__ == '__main__':
    folder = Path(sys.argv[1])
    try:
        job = json.loads((folder / 'input.json').read_text(encoding='utf-8'))
        result = solid.evaluate(job['spec'], folder, previous=job.get('previous'), start=job.get('start'))
    except Exception as exc:
        detail = traceback.format_exc(limit=3)
        result = {'ok': False, 'error': f'{type(exc).__name__}: {exc}'[:1000], 'trace': detail[-1500:]}
    write_text_atomic(folder / 'result.json', json.dumps(result, ensure_ascii=False, allow_nan=False))
    raise SystemExit(0 if result['ok'] else 1)
