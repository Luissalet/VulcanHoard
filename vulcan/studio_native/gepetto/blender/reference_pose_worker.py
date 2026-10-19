"""Generate a reviewable posed copy from an image. Never save the source scene."""
import json
from pathlib import Path
import sys
import time
import traceback
import shutil

import bpy
import numpy as np
from mathutils import Matrix

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from pose_engine.ops import auto_card_pose as auto


def main(config):
    start=time.monotonic()
    run=Path(config['run_dir']).resolve()
    def stage(message):
        temp=run/'stage.tmp'
        temp.write_text(json.dumps({'message':message}),encoding='utf-8')
        temp.replace(run/'stage.json')
    ctx=bpy.context
    arm=auto.cpo.find_armature(ctx)
    if arm is None:
        raise ValueError('Selecciona una escena con una única armadura de personaje.')
    rig=auto.cpo.rig_from_armature(arm)
    missing=[n for n in auto.cp.POSED_BONES if n not in rig.index]
    if missing:
        raise ValueError('Este rig no usa las articulaciones ACNH compatibles: '+', '.join(missing))
    rig,_=auto.cpo.reference_rig(arm,rig)
    before={b.name:auto.cpo._np(b.matrix_basis) for b in arm.pose.bones}
    source_folder=Path(bpy.data.filepath).parent
    cached,_=auto.vision.cached_observation(run,rig)
    if cached is None and (source_folder/'card.png').is_file():
        source_cached,_=auto.vision.cached_observation(source_folder,rig)
        if source_cached is not None and (source_folder/'card.png').read_bytes()==(run/'card.png').read_bytes():
            shutil.copy2(source_folder/auto.vision.CACHE_NAME,run/auto.vision.CACHE_NAME)
            cached=source_cached
    image,points=None,{}
    if cached is None:
        stage('Preparando la vista de referencia del personaje…')
        image,points=auto.calibration_render(ctx,arm,rig,run)
    stage('Interpretando la imagen y ajustando la pose…')
    data,res,hit=auto.vision.analyze_and_fit(run,rig,image,points)
    for n,m in res.bone_basis.items():
        arm.pose.bones[n].matrix_basis=Matrix(m.tolist())
    arm[auto.cpo.REFERENCE_KEY]=json.dumps(data['reference_basis'])
    ctx.view_layer.update()
    for b in arm.pose.bones:
        if b.name not in auto.cp.POSED_BONES and not np.array_equal(before[b.name],auto.cpo._np(b.matrix_basis)):
            raise ValueError('La comprobación de articulaciones detectó un cambio no permitido.')
    scene=ctx.scene
    camera=auto.cpo.ensure_card_camera(scene,res,str(run/'card.png'))
    scene.camera=camera
    # Resolve existing external assets before saving the copy elsewhere.
    for image in bpy.data.images:
        if image.filepath:
            image.filepath=bpy.path.abspath(image.filepath,library=image.library)
    for library in bpy.data.libraries:
        library.filepath=bpy.path.abspath(library.filepath)
    W,H=res.image_size
    factor=min(1.,966/max(W,H))
    scene.render.resolution_x=max(64,int(W*factor))
    scene.render.resolution_y=max(64,int(H*factor))
    scene.render.resolution_percentage=100
    scene.render.pixel_aspect_x=scene.render.pixel_aspect_y=1
    bpy.ops.wm.save_as_mainfile(filepath=str(run/'posed.blend'))
    stage('Creando la vista previa de la propuesta…')
    # Preview the rig, not expensive displacement/subdivision processing.
    for obj in scene.objects:
        if obj.type=='MESH':
            for mod in obj.modifiers:
                if mod.type!='ARMATURE': mod.show_render=False
    scene.render.engine='BLENDER_WORKBENCH'
    scene.render.film_transparent=True
    scene.render.use_border=scene.render.use_crop_to_border=False
    scene.display.shading.light='FLAT'
    scene.display.shading.color_type='TEXTURE'
    scene.render.image_settings.file_format='PNG'
    scene.render.image_settings.color_mode='RGBA'
    scene.render.filepath=str(run/'preview.png')
    bpy.ops.render.render(write_still=True)
    preview=bpy.data.images.load(str(run/'preview.png'),check_existing=False)
    try:
        alpha=np.asarray(preview.pixels[:],dtype=np.float32).reshape(-1,4)[:,3]
        if np.count_nonzero(alpha>.01)/max(1,len(alpha))<.01:
            raise ValueError('La vista previa no muestra al personaje; no se entrega la propuesta.')
    finally:
        bpy.data.images.remove(preview)
    return {'success':True,'rms_px':res.rms_px,'image_size':list(res.image_size),
            'normalized_error':res.rms_px/res.image_size[1],
            'seconds':round(time.monotonic()-start,2),'cached':hit,
            'bones':list(res.bone_basis),'source_unchanged':True,
            'analysis_notes':data.get('analysis_notes',''),
            'limitations':['La pose solo ajusta cadera, cuello, hombros y codos.',
                           'Piernas, manos, expresión y ropa conservan su estado original.'],
            'errors_px':res.errors_px}


config=json.loads(Path(sys.argv[sys.argv.index('--')+1]).read_text('utf-8'))
try:
    result=main(config)
except Exception as exc:
    traceback.print_exc()
    result={'success':False,'error':str(exc)}
Path(config['run_dir'],'result.json').write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
if not result['success']:
    raise SystemExit(1)
