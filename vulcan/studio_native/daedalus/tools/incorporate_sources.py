"""Build-time incorporation of pinned source, never used at application runtime."""
import hashlib
import json
from pathlib import Path
import shutil

ROOT=Path(__file__).resolve().parents[1]
REFERENCE=ROOT.parent/'trellis2-multigpu'/'runtime'
STUDY=ROOT.parent/'3d-refine-4gpu-20261015'
DEST=ROOT/'daedalus'/'geometry'
DEST.mkdir(parents=True,exist_ok=True)
HEADER='# Incorporated and modified for Daedalus; GPL-3.0. Historical source: ComfyUI\n# 95539f56344958339e39b7582a476267d489b0ee. See THIRD_PARTY.md.\n'
mapping={
 'comfy/ldm/trellis2/model.py':('generator_model.py',[
  ('import comfy.model_management\n',''),('import comfy.ops','from . import ops'),
  ('from comfy.ldm.trellis2.vae import','from .surface_vae import'),
  ('from comfy.ldm.modules.attention import optimized_attention','from .ops import optimized_attention'),
  ('from comfy.ldm.genmo.joint_model.layers import TimestepEmbedder','from .ops import TimestepEmbedder'),
  ('from comfy.ldm.flux.math import apply_rope, apply_rope1','from .ops import apply_rope, apply_rope1'),
  ('comfy.ops.','ops.'),('comfy.model_management.cast_to(', 'ops.cast_to(')]),
 'comfy/ldm/trellis2/vae.py':('surface_vae.py',[
  ('import comfy.ops','from . import ops as native_ops'),('from comfy.ldm.trellis2.flexgemm import','from .sparse_ops import'),('comfy.ops.disable_weight_init','native_ops.disable_weight_init'),('comfy.ops.cast_to','native_ops.cast_to')]),
 'comfy/ldm/trellis2/flexgemm.py':('sparse_ops.py',[
  ('import comfy.model_management','from . import ops'),('comfy.model_management.get_free_memory','ops.get_free_memory')]),
 'comfy/image_encoders/dino3.py':('image_encoder.py',[
  ('import comfy.ops','from . import ops'),
  ('from comfy.ldm.modules.attention import optimized_attention_for_device','from .ops import optimized_attention_for_device'),
  ('from comfy.image_encoders.dino2 import LayerScale as DINOv3ViTLayerScale','from .ops import LayerScale as DINOv3ViTLayerScale'),('comfy.ops.','ops.')])
}
proof=[]
for source,(target,replacements) in mapping.items():
    original=(REFERENCE/source).read_text(encoding='utf-8')
    text=original
    for old,new in replacements:
        text=text.replace(old,new)
    assert 'import comfy' not in text and 'comfy.' not in text,target
    (DEST/target).write_text(HEADER+text,encoding='utf-8')
    proof.append({'source':source,'target':target,'source_sha256':hashlib.sha256(original.encode()).hexdigest()})
shutil.copy2(REFERENCE/'LICENSE',ROOT/'LICENSE')
shutil.copy2(REFERENCE/'comfy/image_encoders/dino3_large.json',DEST/'image-encoder-config.json')
shutil.copy2(STUDY/'detail_models.py',DEST/'refiner_model.py')
shutil.copy2(STUDY/'detailgen-runtime/detailgen3d/schedulers/scheduling_rectified_flow.py',DEST/'refiner_scheduler.py')
shutil.copy2(STUDY/'detailgen-runtime/LICENSE',DEST/'DETAILGEN-LICENSE')
(ROOT/'docs').mkdir(exist_ok=True)
(ROOT/'docs/source-incorporation.json').write_text(json.dumps({'revision':'95539f56344958339e39b7582a476267d489b0ee','files':proof},indent=2))
print('Incorporated native model source; no application-runtime import from reference checkouts.')
