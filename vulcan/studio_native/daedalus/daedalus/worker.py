"""Owned model worker: all model computation lives inside this package."""
import json
from pathlib import Path
import shutil
import sys
import time
import traceback

# A separately versioned content interpreter must not import the server's
# site-packages through cwd/PYTHONPATH. Direct isolated execution exposes only
# our package subtree, so its binary dependencies come from its own environment.
if not __package__:
    import types
    package=types.ModuleType('vulcan')
    package.__path__=[str(Path(__file__).resolve().parents[3])]
    sys.modules['vulcan']=package
    __package__='vulcan.studio_native.daedalus.daedalus'

def progress(stage,value):
    print(json.dumps({'event':'progress','stage':stage,'progress':value}),flush=True)

def align_refinement_input(spec):
    if spec['kind']!='refine' or not spec.get('alignment_degrees',0):
        return None
    import hashlib
    import numpy as np
    import trimesh
    alignment=trimesh.transformations.rotation_matrix(np.deg2rad(spec['alignment_degrees']),[0,1,0])
    source=Path(spec['mesh'])
    mesh=trimesh.load(source,force='mesh',process=False)
    mesh.apply_transform(alignment)
    aligned=Path(spec['output_dir'])/'aligned-source.glb'
    mesh.export(aligned)
    with source.open('rb') as stream:
        digest=hashlib.file_digest(stream,'sha256').hexdigest()
    (aligned.parent/'alignment.json').write_text(json.dumps({'degrees_y':spec['alignment_degrees'],
        'matrix':alignment.tolist(),'original':str(source),'original_sha256':digest}))
    spec['mesh']=str(aligned)
    return alignment

def restore_refinement_pose(folder,alignment):
    if alignment is None:
        return
    import numpy as np
    import trimesh
    geometry=np.load(folder/'refined.npz')
    vertices=trimesh.transform_points(geometry['vertices'],np.linalg.inv(alignment))
    faces=geometry['faces']
    np.savez_compressed(folder/'refined.npz',vertices=vertices,faces=faces)
    trimesh.Trimesh(vertices=vertices,faces=faces,process=False).export(folder/'refined.glb')

def prepare_input(spec):
    if spec['kind']=='upscale':
        output=Path(spec['output_dir'])/'input.png'
        shutil.copy2(spec['image'],output)
        spec['image']=str(output)
        return
    if spec['kind']!='generate':
        return
    import numpy as np
    from PIL import Image
    from scipy.ndimage import binary_propagation
    image=Image.open(spec['image']).convert('RGBA')
    rgba=np.asarray(image).copy()
    if spec.get('remove_background',True) and np.all(rgba[...,3]==255):
        rgb=rgba[...,:3].astype(np.float32)
        border=np.concatenate([rgb[0],rgb[-1],rgb[:,0],rgb[:,-1]])
        color=np.median(border,axis=0)
        close=np.linalg.norm(rgb-color,axis=-1)<18
        border_close=np.linalg.norm(border-color,axis=-1)<18
        if border_close.mean()>.95:
            seed=np.zeros(close.shape,dtype=bool)
            seed[0]=close[0]
            seed[-1]=close[-1]
            seed[:,0]=close[:,0]
            seed[:,-1]=close[:,-1]
            background=binary_propagation(seed,mask=close)
            rgba[...,3][background]=0
    output=Path(spec['output_dir'])/'input.png'
    Image.fromarray(rgba).save(output)
    spec['image']=str(output)

def attach_progress(engine,kind,resolution):
    original=engine.event
    def event(name,**data):
        original(name,**data)
        if name=='image_encoded' or name=='surface_encoded':
            progress('preparing_geometry',.08)
        elif name=='sampling_step':
            steps={'structure':(.08,.18),'shape_512':(.18,.4 if resolution==1024 else .62),
                   'shape_1024':(.4,.66),'texture':(.66,.8)}
            lo,hi=steps[data['stage']]
            progress({'structure':'building_structure','shape_512':'generating_shape','shape_1024':'adding_detail','texture':'generating_materials'}[data['stage']],
                     lo+(hi-lo)*data['step']/data['total'])
        elif name=='refine_step':
            progress('refining_shape',.1+.55*data['step']/data.get('total',50))
        elif name=='sdf_level':
            progress('decoding_mesh',.7+(.1 if data['resolution']==512 else 0))
        elif name=='geometry_decoded':
            progress('decoding_materials',.82)
    engine.event=event

def publish(spec):
    import numpy as np
    import trimesh
    from scipy.spatial import cKDTree
    folder=Path(spec['output_dir'])
    result=json.loads((folder/'result.json').read_text())
    if spec['kind']=='refine':
        geometry=np.load(folder/'refined.npz')
        raw=geometry['vertices'][:,[2,0,1]]
        np.savez_compressed(folder/'geometry.npz',vertices=raw,faces=geometry['faces'])
        if spec.get('transfer_materials',True):
            shutil.copy2(Path(spec['material_source'])/'pbr-voxels.npz',folder/'pbr-voxels.npz')
        mesh=trimesh.load(folder/'refined.glb',force='mesh',process=False)
        if (folder/'pbr-voxels.npz').exists():
            voxels=np.load(folder/'pbr-voxels.npz')
            centers=(voxels['coords'].astype(np.float32)+.5)/int(voxels['resolution'])-.5
            distances,indices=cKDTree(centers).query(raw,workers=4)
            mesh.visual=trimesh.visual.ColorVisuals(mesh=mesh,vertex_colors=np.clip(voxels['attributes'][indices,:3]*255,0,255).astype(np.uint8))
            result['material_transfer']={'method':'nearest original PBR voxel','mean_distance_normalized':float(distances.mean())}
        mesh.export(folder/'model.glb')
        mesh.export(folder/'model.stl')
        mesh.export(folder/'model.ply')
        details=next(row for row in reversed(result['events']) if row['event']=='mesh_decoded')
        result.update({key:details[key] for key in ['vertices','faces','watertight']})
    progress('preparing_export',.87)
    if (folder/'pbr-voxels.npz').exists():
        from .geometry import materials,normals
        materials.run(folder)
        progress('smoothing_preview',.94)
        normals.run(folder)
        preview_path=folder/'model-pbr-smooth.glb'
    else:
        mesh=trimesh.load(folder/'model.glb',force='mesh',process=False)
        if len(mesh.faces)>200000:
            mesh=mesh.simplify_quadric_decimation(face_count=200000)
        mesh.export(folder/'model-preview.glb')
        preview_path=folder/'model-preview.glb'
    preview=trimesh.load(preview_path,force='mesh',process=False)
    preview.visual=trimesh.visual.TextureVisuals(material=trimesh.visual.material.PBRMaterial(
        baseColorFactor=[60,65,75,255],roughnessFactor=.8,metallicFactor=0,doubleSided=True))
    preview.export(folder/'model-clay.glb')
    metrics={'vertices':int(result['vertices']),'faces':int(result['faces']),'watertight':bool(result['watertight']),
             'preview_faces':len(preview.faces),'bbox_normalized':preview.bounds.tolist()}
    files={label:name for label,name in {'full_glb':'model.glb','glb':preview_path.name,'clay':'model-clay.glb',
            'stl':'model.stl','ply':'model.ply','color':'base-color.png','material':'metallic-roughness.png',
            'geometry':'geometry.npz','pbr':'pbr-voxels.npz','reference':'preprocessed.png'}.items() if (folder/name).exists()}
    return {'files':files,'metrics':metrics,'seed':spec.get('seed',42),'resolution':spec.get('resolution',512),
            'kind':spec['kind'],'materials':'transferred' if spec['kind']=='refine' and spec.get('transfer_materials',True) else 'generated' if spec['kind'] in {'generate','upscale'} else 'none',
            'engine_seconds':result['seconds_total'],'memory':result.get('memory',[]),
            'block_calls':result.get('block_calls',{}),'refiner_tokens':spec.get('tokens'),
            'refiner_backend':spec.get('backend','detail') if spec['kind']=='refine' else None,
            'alignment_degrees':spec.get('alignment_degrees',0),'refiner_steps':spec.get('steps',50) if spec['kind']=='refine' else None,
            'upscale_source':result.get('upscale_source'),'generator':result.get('generator','trellis' if spec['kind'] in {'generate','upscale'} else None),
            'model_provenance':result.get('model_provenance'),'mesh_resolution':result.get('mesh_resolution')}

def main():
    spec=json.loads(Path(sys.argv[1]).read_text(encoding='utf-8'))
    folder=Path(spec['output_dir'])
    begin=time.perf_counter()
    try:
        if spec['kind'] in {'repair','optimize'}:
            from .geometry import postprocess
            result=postprocess.run(spec,progress)
            temporary=folder/'artifacts.json.tmp'
            temporary.write_text(json.dumps(result,indent=2),encoding='utf-8')
            temporary.replace(folder/'artifacts.json')
            progress('complete',1)
            return
        progress('preparing_image',.005)
        prepare_input(spec)
        progress('loading_models',.01)
        if spec['kind']=='generate' and spec.get('generator')=='triposr':
            from .geometry import draft_generation
            draft_generation.run(spec,progress)
            result=publish(spec)
            result['materials']='vertex-colors'
            result['seconds_total']=time.perf_counter()-begin
            temporary=folder/'artifacts.json.tmp'
            temporary.write_text(json.dumps(result,indent=2),encoding='utf-8')
            temporary.replace(folder/'artifacts.json')
            progress('complete',1)
            return
        if spec['kind'] in {'generate','upscale'}:
            from .geometry import generation as engine
        elif spec.get('backend')=='ultra':
            from .geometry import ultra_refinement as engine
        else:
            from .geometry import refinement as engine
        alignment=align_refinement_input(spec)
        attach_progress(engine,spec['kind'],spec.get('resolution',512))
        engine.run(spec)
        restore_refinement_pose(folder,alignment)
        import gc
        import torch
        gc.collect()
        for gpu in range(torch.cuda.device_count()):
            with torch.cuda.device(gpu):
                torch.cuda.empty_cache()
        result=publish(spec)
        result['seconds_total']=time.perf_counter()-begin
        temporary=folder/'artifacts.json.tmp'
        temporary.write_text(json.dumps(result,indent=2),encoding='utf-8')
        temporary.replace(folder/'artifacts.json')
        progress('complete',1)
    except Exception as error:
        message='No hay memoria suficiente en una de las gráficas. Prueba con la resolución 512.' if 'out of memory' in str(error).lower() else 'El motor no ha podido terminar: '+str(error)
        (folder/'failure.json').write_text(json.dumps({'message':message,'type':type(error).__name__}),encoding='utf-8')
        traceback.print_exc()
        raise SystemExit(1)

if __name__=='__main__':
    main()
