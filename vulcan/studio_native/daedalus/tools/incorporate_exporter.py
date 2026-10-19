"""Incorporate our tested CPU exporter as native package functions."""
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
SOURCE=ROOT.parent/'trellis2-multigpu'
for source,target in [('bake_pbr.py','materials.py'),('smooth_normals.py','normals.py')]:
    text=(SOURCE/source).read_text()
    text=text.replace("ROOT = Path(__file__).resolve().parent\nsys.path.insert(0, str(ROOT / 'deps'))\n",'')
    text=text.replace('def main(folder):','def run(folder):')
    if source=='bake_pbr.py':
        text=text.replace("np.stack([vertices[:, 0], vertices[:, 2], -vertices[:, 1]], -1)","vertices[:, [1, 2, 0]]")
        text=text.replace("attrs[..., :3] * 255", "np.concatenate([attrs[..., :3],attrs[...,5:6]],axis=-1) * 255")
        text=text.replace("alphaMode='OPAQUE'", "alphaMode='MASK' if np.any(attrs[...,5]<0.98) else 'OPAQUE'")
    text=text[:text.index("\n\nif __name__ == '__main__':")]
    (ROOT/'daedalus'/'geometry'/target).write_text(text)
