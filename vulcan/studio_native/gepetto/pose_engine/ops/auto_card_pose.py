"""One-click card.png -> remote observations -> constrained, undoable rig pose."""
import json
import os
from pathlib import Path
import threading

import bpy
from bpy.props import BoolProperty
from mathutils import Matrix
import numpy as np

from ..core import card_pose as cp
from ..core import card_pose_vision as vision
from . import card_pose as cpo


def calibration_render(context, arm, rig, folder):
    """Low-resolution workbench reference. Restore ALL touched scene settings."""
    scene=context.scene
    r=scene.render
    render_names=('engine','resolution_x','resolution_y','resolution_percentage',
                  'pixel_aspect_x','pixel_aspect_y','film_transparent','filepath',
                  'use_border','use_crop_to_border')
    saved={n:getattr(r,n) for n in render_names}
    image_saved={n:getattr(r.image_settings,n) for n in ('file_format','color_mode','color_depth')}
    shade=scene.display.shading
    shade_saved={n:getattr(shade,n) for n in ('light','color_type','show_shadows','show_cavity')}
    camera=scene.camera
    pose={n:arm.pose.bones[n].matrix_basis.copy() for n in cp.POSED_BONES}
    hidden=[(o,o.hide_render) for o in scene.objects]
    modifiers=[]
    cam_data=bpy.data.cameras.new('CardPoseCalibration')
    cam_obj=bpy.data.objects.new('CardPoseCalibration',cam_data)
    scene.collection.objects.link(cam_obj)
    try:
        meshes=[]
        for obj in scene.objects:
            if obj.type != 'MESH': continue
            belongs=any(m.type=='ARMATURE' and m.object==arm for m in obj.modifiers) or obj.parent==arm
            obj.hide_render=not belongs
            if belongs:
                meshes.append(obj)
                for mod in obj.modifiers:
                    if mod.type!='ARMATURE':
                        modifiers.append((mod,mod.show_viewport,mod.show_render))
                        mod.show_viewport=mod.show_render=False
        if not meshes:
            raise ValueError('No mesh driven by the selected armature')
        for n in cp.POSED_BONES:
            arm.pose.bones[n].matrix_basis=Matrix(rig.basis[rig.index[n]].tolist())
        context.view_layer.update()
        points=rig.fk()
        R=rig.front_camera_base()
        q=points @ R[:2].T
        lo,hi=q.min(axis=0),q.max(axis=0)
        # Anatomical joints do not reach the top of a chibi skull; add margin.
        lo-=.20*(hi-lo)
        hi+=.12*(hi-lo)
        W,H=460,644
        scale=.9*min(W/max(hi[0]-lo[0],1e-6),H/max(hi[1]-lo[1],1e-6))
        offset=np.array([W,H])/2-scale*(lo+hi)/2
        ref=cp.FitResult({},R,scale,offset,0.,{},{},(W,H))
        M,_,_=cp.blender_camera(ref)
        cam_obj.matrix_world=Matrix(M.tolist())
        cam_data.type='ORTHO'
        cam_data.sensor_fit='HORIZONTAL'
        cam_data.ortho_scale=W/scale
        cam_data.clip_end=10000
        scene.camera=cam_obj
        r.engine='BLENDER_WORKBENCH'
        r.resolution_x=W; r.resolution_y=H; r.resolution_percentage=100
        r.pixel_aspect_x=r.pixel_aspect_y=1
        r.use_border=r.use_crop_to_border=False
        r.film_transparent=True
        r.image_settings.file_format='PNG'
        r.image_settings.color_mode='RGBA'
        r.image_settings.color_depth='8'
        shade.light='FLAT'; shade.color_type='TEXTURE'
        shade.show_shadows=shade.show_cavity=False
        path=Path(folder)/'card_pose.reference.png'
        r.filepath=str(path)
        bpy.ops.render.render(write_still=True)
        normalized={n:[x/W,y/H] for n,(x,y) in cp.project_all(rig,ref,{}).items()}
        (Path(folder)/'card_pose.reference.json').write_text(json.dumps(normalized,indent=2),encoding='utf-8')
        return path,normalized
    finally:
        scene.camera=camera
        for n,v in saved.items(): setattr(r,n,v)
        for n,v in image_saved.items(): setattr(r.image_settings,n,v)
        for n,v in shade_saved.items(): setattr(shade,n,v)
        for o,v in hidden: o.hide_render=v
        for mod,viewport,render in modifiers:
            mod.show_viewport=viewport; mod.show_render=render
        for n,m in pose.items(): arm.pose.bones[n].matrix_basis=m
        bpy.data.objects.remove(cam_obj,do_unlink=True)
        bpy.data.cameras.remove(cam_data)
        context.view_layer.update()


class AnalysisJob:
    """Plain Python worker. Never accesses bpy from another thread."""
    def __init__(self,*args,force=False):
        self.args=args
        self.force=force
        self.cancel=threading.Event()
        self.result=None
        self.error=None
        self.thread=threading.Thread(target=self.run,daemon=True)

    def run(self):
        try:
            self.result=vision.analyze_and_fit(*self.args,cancel=self.cancel,force=self.force)
        except Exception as exc:
            self.error=str(exc)


class FIGURETOOL_OT_pose_from_card(bpy.types.Operator):
    """Read this character's card.png and pose hips, neck, shoulders and elbows"""
    bl_idname='figure_tools.pose_from_card'
    bl_label='Pose from card.png'
    bl_description='Read card.png with signed-in Codex and fit this character; reuses cached analysis'
    bl_options={'REGISTER','UNDO'}

    read_again: BoolProperty(name='Read card again',default=False,
                            description='Analyze the image again instead of using the saved result')
    _busy=False

    def _status(self,context,text):
        if context.workspace: context.workspace.status_text_set(text)

    def _finish(self,context):
        if getattr(self,'_timer',None):
            context.window_manager.event_timer_remove(self._timer)
            self._timer=None
        type(self)._busy=False
        self._status(context,None)

    def _apply(self,context):
        if self._job.error:
            raise RuntimeError(self._job.error)
        if bpy.data.filepath!=self._blend or self._arm.name not in context.scene.objects:
            raise ValueError('The scene changed during analysis; reopen the character and retry')
        for n,m in self._snapshot.items():
            if not np.allclose(cpo._np(self._arm.pose.bones[n].matrix_basis),m,atol=1e-6,rtol=0):
                raise ValueError('The pose changed during analysis; retry to use the cached card')
        data,res,hit=self._job.result
        cpo.ensure_card_camera(context.scene,res,str(self._folder/'card.png'))
        for n,basis in res.bone_basis.items():
            self._arm.pose.bones[n].matrix_basis=Matrix(basis.tolist())
        self._arm[cpo.REFERENCE_KEY]=json.dumps(data['reference_basis'])
        context.view_layer.update()
        self.report({'INFO'},f'Card pose applied ({"saved analysis" if hit else "card analyzed"}, {res.rms_px:.1f}px). Undo with Ctrl+Z.')
        return {'FINISHED'}

    def execute(self,context):
        if type(self)._busy:
            self.report({'WARNING'},'A card is already being analyzed')
            return {'CANCELLED'}
        try:
            if not bpy.data.filepath:
                raise ValueError('Save the blend in the character folder first')
            self._blend=bpy.data.filepath
            self._folder=Path(self._blend).parent
            vision.png_size(self._folder/'card.png')
            self._arm=cpo.find_armature(context)
            if self._arm is None:
                raise ValueError('Select the character mesh or its armature')
            rig=cpo.rig_from_armature(self._arm)
            missing=[n for n in cp.POSED_BONES if n not in rig.index]
            if missing:
                raise ValueError('Unsupported rig; missing joints: '+', '.join(missing))
            rig,_=cpo.reference_rig(self._arm,rig)
            self._snapshot={b.name:cpo._np(b.matrix_basis) for b in self._arm.pose.bones}
            cached,_=vision.cached_observation(self._folder,rig)
            calibration,points=None,{}
            if cached is None or self.read_again:
                if os.environ.get('FIGURE_TOOLS_POSE_PROVIDER','codex')=='codex':
                    vision.codex_executable()  # Fail clearly before touching calibration settings.
                self._status(context,'Preparing character reference...')
                calibration,points=calibration_render(context,self._arm,rig,self._folder)
            self._job=AnalysisJob(self._folder,rig,calibration,points,force=self.read_again)
            type(self)._busy=True
            if bpy.app.background:
                self._job.run()
                try: return self._apply(context)
                finally: self._finish(context)
            self._job.thread.start()
            self._timer=context.window_manager.event_timer_add(.25,window=context.window)
            context.window_manager.modal_handler_add(self)
            self._status(context,'Reading card.png and fitting pose... Esc to cancel')
            return {'RUNNING_MODAL'}
        except Exception as exc:
            self._finish(context)
            self.report({'ERROR'},str(exc))
            return {'CANCELLED'}

    def modal(self,context,event):
        if event.type=='ESC':
            self._job.cancel.set()
            self._finish(context)
            self.report({'INFO'},'Card pose cancelled')
            return {'CANCELLED'}
        if event.type!='TIMER' or self._job.thread.is_alive():
            return {'PASS_THROUGH'}
        try:
            return self._apply(context)
        except Exception as exc:
            self.report({'ERROR'},str(exc))
            return {'CANCELLED'}
        finally:
            self._finish(context)

    def cancel(self,context):
        if getattr(self,'_job',None): self._job.cancel.set()
        self._finish(context)
