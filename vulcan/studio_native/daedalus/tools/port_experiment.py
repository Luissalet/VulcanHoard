"""One-time incorporation of our validated experiment into owned engine APIs."""
from pathlib import Path
import re

ROOT=Path(__file__).resolve().parents[1]
STUDY=ROOT.parent/'3d-refine-4gpu-20261015'
DEST=ROOT/'daedalus'/'geometry'
text=(STUDY/'generate_four.py').read_text()
start=text.index('ROOT = Path(__file__)')
end=text.index('import numpy as np',start)
text=text[:start]+"from types import SimpleNamespace\nROOT = None\nWEIGHTS = None\n\n"+text[end:]
text=text.replace('import comfy.ops','from . import ops')
text=text.replace('import comfy.clip_vision','from .image_encoder import DINOv3ViTModel')
text=text.replace('from comfy.ldm.trellis2.model import Trellis2','from .generator_model import Trellis2')
text=text.replace('from comfy.ldm.trellis2.vae import','from .surface_vae import')
text=text.replace("BASE / 'weights'",'WEIGHTS').replace("BASE / 'pipeline.json'","WEIGHTS / 'pipeline.json'")
text=text.replace('operations=comfy.ops.disable_weight_init','operations=ops.disable_weight_init')
old="clip = comfy.clip_vision.load(str(WEIGHTS / 'clip_vision' / 'dino_v3_vit_l.safetensors'))\n    clip.model.to(device='cuda:0', dtype=DTYPE)"
new="config = json.loads((Path(__file__).parent / 'image-encoder-config.json').read_text())\n    clip = DINOv3ViTModel(config, DTYPE, 'cpu', ops.disable_weight_init)\n    clip.load_state_dict(load_file(str(WEIGHTS / 'clip_vision' / 'dino_v3_vit_l.safetensors')), strict=True, assign=True)\n    clip.eval().to(device='cuda:0', dtype=DTYPE)"
assert old in text
text=text.replace(old,new).replace('clip.model(', 'clip(').replace('clip.model.cpu()', 'clip.cpu()')
start=text.index('def main():')
end=text.index('    config = ',text.index("event('environment'",start))
text=text[:start]+'''def run(spec):
    global ROOT, WEIGHTS, START
    ROOT = Path(spec['output_dir'])
    WEIGHTS = Path(spec['model_root']) / 'generator'
    START = time.perf_counter()
    args = SimpleNamespace(resolution=int(spec.get('resolution',512)),image=spec['image'],seed=int(spec.get('seed',42)),resume=False)
    out = ROOT
    out.mkdir(parents=True,exist_ok=True)
    assert torch.cuda.device_count()==4
    event('environment',torch=torch.__version__,devices=[torch.cuda.get_device_name(i) for i in range(4)],resolution=args.resolution)
'''+text[end:]
start=text.index('        # Check that partitioning')
end=text.index('        for i in range(4):',start)
text=text[:start]+'        split(model)\n'+text[end:]
text=text.replace("np.stack([vertices[:, 0], vertices[:, 2], -vertices[:, 1]], axis=-1)","vertices[:, [1, 2, 0]]")
text=text[:text.index("\n\nif __name__ == '__main__':")]
assert 'comfy.' not in text and 'BASE /' not in text
(DEST/'generation.py').write_text(text,encoding='utf-8')

text=(STUDY/'refine_four.py').read_text()
start=text.index('ROOT = Path(__file__)')
end=text.index('import numpy as np',start)
text=text[:start]+"from types import SimpleNamespace\nROOT = None\n\n"+text[end:]
text=text.replace('from detail_models import','from .refiner_model import')
start=text.index('# This separate scheduler file')
end=text.index('START = ',start)
text=text[:start]+'from .refiner_scheduler import RectifiedFlowScheduler\n\n'+text[end:]
text=text.replace("ROOT / 'weights' /",'ROOT /')
start=text.index('def main():')
end=text.index('    assert torch.cuda.device_count()',start)
text=text[:start]+'''def run(spec):
    global ROOT, START
    ROOT = Path(spec['model_root']) / 'refiner'
    START = time.perf_counter()
    args = SimpleNamespace(mesh=spec['mesh'],image=spec['image'],name=spec['output_dir'],
            steps=int(spec.get('steps',50)),resolution=512,seed=int(spec.get('seed',42)),
            tokens=int(spec.get('tokens',2048)),resume=False,initial_latent=None,baseline=None)
    out = Path(spec['output_dir'])
    out.mkdir(parents=True,exist_ok=True)
'''+text[end:]
start=text.index('            # Full trained model,')
end=text.index('            scheduler = ',start)
text=text[:start]+'            model.to(device="cuda:0",dtype=DTYPE)\n            split(model)\n'+text[end:]
text=text.replace('            del reference, actual, error, check_x\n','')
text=text.replace("[('vae-only',initial), ('refined',refined)]","[('refined',refined)]")
text=text[:text.index("\n\nif __name__ == '__main__':")]
assert 'detailgen-runtime' not in text and 'sys.path.insert' not in text
(DEST/'refinement.py').write_text(text,encoding='utf-8')
print('Owned generation.run(spec) and refinement.run(spec) incorporated.')
