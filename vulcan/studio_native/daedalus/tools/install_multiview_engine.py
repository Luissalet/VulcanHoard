"""Explicit provisioning of the isolated local multiview runtime and public weights."""
import argparse
import hashlib
import json
from pathlib import Path
import subprocess
import sys
import urllib.request
import venv

MANIFEST=Path(__file__).resolve().parents[1]/'daedalus/geometry/multiview_model/weights.json'


def digest(path):
    with path.open('rb') as stream:
        return hashlib.file_digest(stream,'sha256').hexdigest()


def prepare_models(directory,verify_only=False):
    manifest=json.loads(MANIFEST.read_text(encoding='utf-8'))
    for item in manifest:
        target=directory/item['path']
        if not target.is_file():
            if verify_only:
                raise FileNotFoundError(target)
            target.parent.mkdir(parents=True,exist_ok=True)
            temporary=target.with_name(target.name+'.part')
            url=f'https://huggingface.co/{item["repo"]}/resolve/{item["revision"]}/{item["filename"]}'
            print('Downloading '+item['path'],flush=True)
            with urllib.request.urlopen(url,timeout=90) as source,temporary.open('wb') as output:
                while chunk:=source.read(4*1024*1024):
                    output.write(chunk)
            if temporary.stat().st_size!=item['size'] or digest(temporary)!=item['sha256']:
                raise ValueError('Weight digest mismatch: '+item['path'])
            temporary.replace(target)
        if target.stat().st_size!=item['size'] or digest(target)!=item['sha256']:
            raise ValueError('Weight digest mismatch: '+item['path'])
        print('Verified '+item['path'],flush=True)
    (directory/'weights-manifest.json').write_text(json.dumps(manifest,indent=2)+'\n',encoding='utf-8')
    print('Joint checkpoints retain CC-BY-NC-4.0; component terms are documented in THIRD_PARTY.md.',flush=True)


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--environment',type=Path)
    parser.add_argument('--models',type=Path,help='The multiview subdirectory of the normal model root')
    parser.add_argument('--verify-only',action='store_true',help='Check pinned files without network access')
    args=parser.parse_args()
    if not args.environment and not args.models:
        parser.error('Choose --environment and/or --models explicitly')
    if args.environment:
        if sys.version_info[:2]!=(3,11):
            parser.error('Run environment setup using Python3.11 for the verified runtime')
        venv.EnvBuilder(with_pip=True).create(args.environment)
        python=args.environment/('Scripts/python.exe' if sys.platform=='win32' else 'bin/python')
        subprocess.run([str(python),'-m','pip','install','--disable-pip-version-check','torch==2.11.0+cu128','--index-url','https://download.pytorch.org/whl/cu128'],check=True)
        subprocess.run([str(python),'-m','pip','install','--disable-pip-version-check','peft==0.17.1','transformers==4.57.3','safetensors>=0.6','easydict>=1.13','tqdm>=4.66','numpy>=2,<3','pillow>=11','trimesh>=4.6','scipy>=1.15','fast-simplification>=0.2'],check=True)
        print('Set DAEDALUS_MULTIVIEW_PYTHON='+str(python.resolve()),flush=True)
    if args.models:
        prepare_models(args.models.resolve(),args.verify_only)


if __name__=='__main__':
    main()
