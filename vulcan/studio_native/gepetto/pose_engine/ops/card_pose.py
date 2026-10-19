"""
ops/card_pose.py - Pose a villager like its amiibo card (hip, shoulders,
elbows, neck only).

Input: ``card_pose.json`` next to the .blend (Characters/<Villager>/):

    {
      "card": "card.png",                 # optional, shown behind CardCam
      "image_size": [230, 322],           # card pixels (w, h)
      "keypoints": {                      # pixels, y DOWN; _L = villager's
        "pelvis": [u, v],                 #   own left (viewer's right)
        "shoulder_L": [u, v], ...
      },
      "weights": {"ankle_L": 0.5, ...},   # optional, default 1
      "camera_pitch_deg": 25              # optional, overrides CARD_PITCH_DEG
    }

Valid keypoint names: ``core.card_pose.KEYPOINT_BONES``. Only
``core.card_pose.POSED_BONES`` are rotated; everything else keeps its
current pose. The six solved bones use a saved reference (or an explicit
``reference_basis`` in JSON), making repeated clicks reproducible.
Writes ``pose_bone.matrix_basis`` directly (no action, no
keyframes) and builds/updates a ``CardCam`` that matches the
card, with the card as camera background for a visual check.
Does not change render dimensions; the headless preview sets the card frame.
Optional ``surfaces`` observations contain bone, local normal, min_facing and
weight. They prevent an observed broad wing from being fitted edge-on.
"""

import json
import os

import bpy
import numpy as np
from mathutils import Matrix

from ..core import card_pose as cp

CARD_JSON = "card_pose.json"
CAM_NAME = "CardCam"
REFERENCE_KEY = "figure_tools_card_pose_reference_v1"


def _np(m):
    return np.array([list(r) for r in m], dtype=float)


def rig_from_armature(obj) -> cp.Rig:
    bones = obj.data.bones
    names = [b.name for b in bones]
    index = {n: i for i, n in enumerate(names)}
    parents = [index[b.parent.name] if b.parent else -1 for b in bones]
    rest = np.stack([_np(b.matrix_local) for b in bones])
    basis = np.stack([_np(obj.pose.bones[n].matrix_basis) for n in names])
    return cp.Rig(names, parents, rest, basis, _np(obj.matrix_world))


def reference_rig(obj, rig, explicit=None):
    """Keep a stable prior between clicks; preserve every other current bone."""
    saved = obj.get(REFERENCE_KEY)
    reference = explicit if explicit is not None else (json.loads(saved) if saved else
                 {n: rig.basis[rig.index[n]].tolist() for n in cp.POSED_BONES})
    if set(reference) != set(cp.POSED_BONES):
        raise ValueError("Stored card pose reference does not match this rig")
    basis = rig.basis.copy()
    for name, matrix in reference.items():
        m = np.asarray(matrix, dtype=float)
        if m.shape != (4, 4) or not np.isfinite(m).all():
            raise ValueError("Invalid stored card pose reference")
        basis[rig.index[name]] = m
    return cp.Rig(rig.names, rig.parents, rig.rest, basis, rig.world), reference


def find_armature(context):
    obj = context.active_object
    if obj is not None and obj.type == 'ARMATURE':
        return obj
    if obj is not None and obj.type == 'MESH':
        for m in obj.modifiers:
            if m.type == 'ARMATURE' and m.object is not None:
                return m.object
        if obj.parent is not None and obj.parent.type == 'ARMATURE':
            return obj.parent
    arms = [o for o in context.scene.objects if o.type == 'ARMATURE']
    return arms[0] if len(arms) == 1 else None


def load_card_json(path):
    with open(path, "r", encoding="utf-8") as f:
        data = json.load(f)
    kps = {k: tuple(v) for k, v in data.get("keypoints", {}).items() if v is not None}
    return data, kps


def ensure_card_camera(scene, res: cp.FitResult, card_path=None):
    M, kind, value = cp.blender_camera(res)
    cam_obj = scene.objects.get(CAM_NAME)
    if cam_obj is None or cam_obj.type != 'CAMERA':
        cam_data = bpy.data.cameras.new(CAM_NAME)
        cam_obj = bpy.data.objects.new(CAM_NAME, cam_data)
        scene.collection.objects.link(cam_obj)
    cam = cam_obj.data
    cam.sensor_fit = 'HORIZONTAL'
    cam.shift_x = cam.shift_y = 0
    W, H = res.image_size
    if kind == 'PERSP':
        cam.type = 'PERSP'
        cam.lens = float(value) / W * cam.sensor_width
    else:
        cam.type = 'ORTHO'
        cam.ortho_scale = float(value) * W / max(W, H)
    cam_obj.matrix_world = Matrix(M.tolist())
    # A nearly orthographic perspective fit may place the camera far away.
    # Keep the character between the clipping planes in that case.
    distance = cam_obj.matrix_world.translation.length
    cam.clip_start = max(0.1, distance / 1000.0) if distance > 500 else 0.1
    cam.clip_end = max(1000.0, distance * 3.0)
    cam_obj["card_image_size"] = [int(W), int(H)]
    if card_path and os.path.isfile(card_path):
        img = bpy.data.images.load(card_path, check_existing=True)
        cam.show_background_images = True
        bg = cam.background_images[0] if len(cam.background_images) else cam.background_images.new()
        bg.image = img
        bg.alpha = 0.5
        bg.display_depth = 'FRONT'
    return cam_obj


def apply_card_pose(context, arm_obj, json_path, make_camera=True, apply_pose=True):
    data, kps = load_card_json(json_path)
    size = data.get("image_size")
    card_file = data.get("card")
    card_path = os.path.join(os.path.dirname(json_path), card_file) if card_file else None
    if size is None and card_path and os.path.isfile(card_path):
        img = bpy.data.images.load(card_path, check_existing=True)
        size = tuple(img.size)
    if size is None:
        raise ValueError("card_pose.json needs image_size or a readable card image")

    rig = rig_from_armature(arm_obj)
    missing = [b for b in cp.POSED_BONES if b not in rig.index]
    if missing:
        raise ValueError(f"Rig lacks required pose bones: {missing}")
    reference = None
    if apply_pose:
        rig, reference = reference_rig(arm_obj, rig, data.get("reference_basis"))
    res = cp.fit_card_pose(rig, kps, size, weights=data.get("weights"),
                           posed=cp.POSED_BONES if apply_pose else (),
                           surfaces=data.get("surfaces"),
                           pitch_deg=data.get("camera_pitch_deg", cp.CARD_PITCH_DEG))

    for name, basis in (res.bone_basis.items() if apply_pose else ()):
        arm_obj.pose.bones[name].matrix_basis = Matrix(basis.tolist())
    context.view_layer.update()

    print(f"[CardPose] {arm_obj.name}: rms {res.rms_px:.2f}px, "
          f"scale {res.scale:.2f}px/u, cost {res.cost:.5f}")
    for k, e in sorted(res.errors_px.items(), key=lambda kv: -kv[1]):
        print(f"[CardPose]   {k:11s} {e:6.2f}px")
    if make_camera:
        ensure_card_camera(context.scene, res, card_path)
    if apply_pose:
        arm_obj[REFERENCE_KEY] = json.dumps(reference)
    return res


class FIGURETOOL_OT_apply_card_pose(bpy.types.Operator):
    """Pose hip, shoulders, elbows and neck like the villager's amiibo card
    (reads card_pose.json next to the .blend)"""
    bl_idname = "figure_tools.apply_card_pose"
    bl_label = "Apply Card Pose"
    bl_options = {'REGISTER', 'UNDO'}

    def execute(self, context):
        if not bpy.data.filepath:
            self.report({'ERROR'}, "Save the .blend first (card_pose.json is looked up next to it)")
            return {'CANCELLED'}
        json_path = os.path.join(os.path.dirname(bpy.data.filepath), CARD_JSON)
        if not os.path.isfile(json_path):
            self.report({'ERROR'}, f"No {CARD_JSON} next to the .blend")
            return {'CANCELLED'}
        arm = find_armature(context)
        if arm is None:
            self.report({'ERROR'}, "No armature found (select it or its mesh)")
            return {'CANCELLED'}
        try:
            res = apply_card_pose(context, arm, json_path)
        except Exception as e:  # noqa: BLE001 - surface any solver/IO error in the UI
            self.report({'ERROR'}, f"Card pose failed: {e}")
            return {'CANCELLED'}
        self.report({'INFO'}, f"Card pose applied (rms {res.rms_px:.1f}px). Check it through {CAM_NAME}.")
        return {'FINISHED'}
