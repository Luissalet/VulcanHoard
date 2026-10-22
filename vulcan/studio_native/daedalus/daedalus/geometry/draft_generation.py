"""Owned single-GPU reconstruction/export using the incorporated local network."""
import json
from pathlib import Path
import time

import numpy as np
from PIL import Image


def prepare_reference(source):
    image=Image.open(source).convert('RGBA')
    rgba=np.asarray(image)
    if np.any(rgba[...,3]<255):
        rows,columns=np.where(rgba[...,3]>0)
        if not len(rows):
            raise ValueError('La referencia es completamente transparente.')
        image=image.crop((int(columns.min()),int(rows.min()),int(columns.max())+1,int(rows.max())+1))
        side=max(1,int(np.ceil(max(image.size)/.85)))
        canvas=Image.new('RGBA',(side,side),(0,0,0,0))
        canvas.paste(image,((side-image.width)//2,(side-image.height)//2))
        rgba=np.asarray(canvas)
    values=rgba.astype(np.float32)/255
    rgb=values[...,:3]*values[...,3:4]+.5*(1-values[...,3:4])
    return Image.fromarray(np.clip(rgb*255,0,255).astype(np.uint8))


def run(spec,progress):
    begin=time.perf_counter()
    import torch
    from .draft_model.system import TSR

    if not torch.cuda.is_available():
        raise RuntimeError('El borrador necesita una GPU CUDA disponible en su entorno local.')
    torch.manual_seed(spec.get('seed',42))
    torch.cuda.init()
    torch.cuda.reset_peak_memory_stats(0)
    folder=Path(spec['output_dir'])
    model_root=Path(spec['model_root'])/'triposr'
    resolution=int(spec.get('draft_resolution',128))
    if resolution not in (128,256):
        raise ValueError('Detalle del borrador no admitido.')
    progress('loading_models',.04)
    model=TSR.from_pretrained(str(model_root),'config.yaml','model.ckpt').eval().to('cuda:0')
    model.renderer.set_chunk_size(4096)
    image=prepare_reference(spec['image'])
    image.save(folder/'preprocessed.png')
    progress('generating_shape',.15)
    with torch.inference_mode():
        codes=model([image],device='cuda:0')
        progress('decoding_mesh',.5)
        mesh=model.extract_mesh(codes,has_vertex_color=True,resolution=resolution)[0]
    if not len(mesh.faces) or not np.isfinite(mesh.vertices).all():
        raise ValueError('El borrador no ha producido una malla válida.')
    # TripoSR is Z-up; viewer/export convention here is right-handed Y-up.
    mesh.vertices=np.asarray(mesh.vertices)[:,[0,2,1]]*np.array([1,1,-1])
    mesh.fix_normals()
    np.savez_compressed(folder/'geometry.npz',vertices=np.asarray(mesh.vertices)[:,[2,0,1]],faces=mesh.faces)
    for extension in ('glb','ply','stl'):
        mesh.export(folder/('model.'+extension))
    torch.cuda.synchronize(0)
    summary={'generator':'triposr','mesh_resolution':resolution,'vertices':len(mesh.vertices),'faces':len(mesh.faces),
             'watertight':bool(mesh.is_watertight),'seconds_total':time.perf_counter()-begin,
             'memory':[{'logical_gpu':0,'physical_gpu':spec.get('physical_gpus',[spec.get('draft_gpu',0)])[0],
                        'peak_allocated_mib':round(torch.cuda.max_memory_allocated(0)/2**20,1),
                        'peak_reserved_mib':round(torch.cuda.max_memory_reserved(0)/2**20,1)}],
             'model_provenance':json.loads((model_root/'weights-proof.json').read_text(encoding='utf-8')),
             'export':'Y-up GLB/PLY vertex colors; STL geometry. No PBR voxels or TRELLIS latent.',
             'events':[],'block_calls':{}}
    (folder/'result.json').write_text(json.dumps(summary,indent=2),encoding='utf-8')
    progress('preparing_export',.85)
