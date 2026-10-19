import json
import sys
from pathlib import Path
from .hoard_link.atomic import write_text_atomic
from .geometry import Recipe,export

if __name__=='__main__':
    folder=Path(sys.argv[1])
    try:
        recipe=Recipe.model_validate_json((folder/'recipe.json').read_text(encoding='utf-8'))
        result={'ok':True,**export(recipe,folder)}
    except Exception as exc:
        result={'ok':False,'error':f'{type(exc).__name__}: {exc}'}
    write_text_atomic(folder/'result.json',json.dumps(result,ensure_ascii=False,allow_nan=False))
    raise SystemExit(0 if result['ok'] else 1)
