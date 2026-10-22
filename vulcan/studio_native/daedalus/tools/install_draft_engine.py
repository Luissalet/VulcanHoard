"""Explicit setup only: isolated GPU environment and pinned public local weights."""
import argparse
import hashlib
import json
from pathlib import Path
import subprocess
import sys
import urllib.request
import venv

MODEL_REVISION = '5b521936b01fbe1890f6f9baed0254ab6351c04a'
ENCODER_REVISION = 'f205d5d8e640a89a2b8ef0369670dfc37cc07fc2'
FILES = [
    ('model.ckpt', f'https://huggingface.co/stabilityai/TripoSR/resolve/{MODEL_REVISION}/model.ckpt',
     '429e2c6b22a0923967459de24d67f05962b235f79cde6b032aa7ed2ffcd970ee', None),
    ('config.yaml', f'https://huggingface.co/stabilityai/TripoSR/resolve/{MODEL_REVISION}/config.yaml',
     None, 'fa3e59a701f02679bd1dca02fbaf97306174d291'),
    ('image-encoder-config.json', f'https://huggingface.co/facebook/dino-vitb16/resolve/{ENCODER_REVISION}/config.json',
     None, '77374d76174bf4b940cc485b61568ff27fcbbc0d'),
    ('MODEL-CARD.md', f'https://huggingface.co/stabilityai/TripoSR/resolve/{MODEL_REVISION}/README.md',
     None, 'e1bcca137e14a0aebb951187ffd3b2e71936aa7b'),
]


def digest(path):
    with path.open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def download_models(directory):
    directory.mkdir(parents=True, exist_ok=True)
    proof=[]
    for name,url,sha256,git_blob in FILES:
        target=directory/name
        if not target.is_file():
            temporary=target.with_suffix(target.suffix+'.part')
            print('Downloading '+name,flush=True)
            urllib.request.urlretrieve(url, temporary)
            temporary.replace(target)
        actual=digest(target)
        if sha256 and actual!=sha256:
            raise ValueError('Weight digest mismatch: '+name)
        if git_blob:
            content=target.read_bytes()
            blob=hashlib.sha1(b'blob '+str(len(content)).encode()+b'\0'+content).hexdigest()
            if blob!=git_blob:
                raise ValueError('Source blob mismatch: '+name)
        proof.append({'file':name,'sha256':actual,'bytes':target.stat().st_size,'url':url})
    (directory/'weights-proof.json').write_text(json.dumps({'model_revision':MODEL_REVISION,'encoder_revision':ENCODER_REVISION,'model_license':'MIT','encoder_config_license':'Apache-2.0','files':proof},indent=2),encoding='utf-8')
    print('Verified local model files: '+str(directory),flush=True)


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--environment',type=Path)
    parser.add_argument('--models',type=Path)
    args=parser.parse_args()
    if not args.environment and not args.models:
        parser.error('Choose --environment and/or --models explicitly')
    if args.environment:
        if sys.version_info[:2]!=(3,11):
            parser.error('Run this setup using Python3.11 for the pinned GPU runtime')
        venv.EnvBuilder(with_pip=True).create(args.environment)
        python=args.environment/('Scripts/python.exe' if sys.platform=='win32' else 'bin/python')
        subprocess.run([str(python),'-m','pip','install','--disable-pip-version-check','torch==2.11.0+cu128','--index-url','https://download.pytorch.org/whl/cu128'],check=True)
        subprocess.run([str(python),'-m','pip','install','--disable-pip-version-check','transformers==4.49.0','omegaconf==2.3.0','einops>=0.8,<1','numpy>=2,<3','pillow>=11','trimesh>=4.6','scipy>=1.15','scikit-image>=0.24','fast-simplification>=0.2'],check=True)
        print('Set DAEDALUS_DRAFT_PYTHON='+str(python.resolve()),flush=True)
    if args.models:
        download_models(args.models.resolve())


if __name__=='__main__':
    main()
