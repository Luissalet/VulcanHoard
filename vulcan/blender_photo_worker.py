"""Runs INSIDE Blender (``blender -b --factory-startup --python blender_photo_worker.py -- spec.json``): product photos of
one mesh for a listing. Never imported by Vulcan itself (it needs ``bpy``); blender_render.py writes the spec and reads the
``VULCAN_RESULT`` line this prints. Works on Blender 3.6 to 5.x (STL importer and EEVEE engine names changed in 4.2)."""

import json
import math
import sys
import time

import bpy
from mathutils import Vector

# Camera directions from the model centre (Z up as printed; front looks from -Y like model_look).
DIRECTIONS = {
    "hero": (0.85, -1.25, 0.62),
    "iso": (1.0, -1.0, 0.9),
    "front": (0.0, -1.0, 0.22),
    "side": (1.0, 0.0, 0.22),
    "back": (0.0, 1.0, 0.22),
    "left": (-1.0, 0.0, 0.22),
    "top": (0.0, -0.02, 1.0),
}


def _spec():
    argv = sys.argv[sys.argv.index("--") + 1:] if "--" in sys.argv else []
    with open(argv[0], encoding="utf-8") as fh:
        return json.load(fh)


def _import_stl(path):
    before = set(bpy.data.objects)
    if hasattr(bpy.ops.wm, "stl_import"):
        bpy.ops.wm.stl_import(filepath=path)
    else:  # Blender < 4.2: the legacy add-on
        bpy.ops.import_mesh.stl(filepath=path)
    objs = [o for o in bpy.data.objects if o not in before and o.type == "MESH"]
    if not objs:
        raise RuntimeError("the STL importer produced no mesh")
    return objs[0]


def _engine_id(name):
    items = {i.identifier for i in bpy.types.RenderSettings.bl_rna.properties["engine"].enum_items}
    if name == "eevee":
        for ident in ("BLENDER_EEVEE_NEXT", "BLENDER_EEVEE"):
            if ident in items:
                return ident
    if name == "cycles":
        return "CYCLES"
    return "BLENDER_WORKBENCH"


def _material(color, roughness):
    mat = bpy.data.materials.new("VulcanFilament")
    mat.diffuse_color = (*color, 1.0)  # what Workbench shows with color_type MATERIAL
    mat.use_nodes = True
    bsdf = mat.node_tree.nodes.get("Principled BSDF")
    if bsdf is not None:
        bsdf.inputs["Base Color"].default_value = (*color, 1.0)
        bsdf.inputs["Roughness"].default_value = roughness
    return mat


def _lin(c):
    """sRGB 0..1 -> linear (Blender colours are scene-linear)."""
    return tuple(v / 12.92 if v <= 0.04045 else ((v + 0.055) / 1.055) ** 2.4 for v in c)


def _place(obj, smooth):
    """Bottom centre on the origin, largest dimension 2 units (the camera and lights are sized for that)."""
    bpy.context.view_layer.objects.active = obj
    obj.select_set(True)
    bpy.ops.object.origin_set(type="ORIGIN_GEOMETRY", center="BOUNDS")
    obj.scale = (2.0 / max(max(obj.dimensions), 1e-9),) * 3
    bpy.ops.object.transform_apply(location=False, rotation=False, scale=True)
    obj.location = (0.0, 0.0, obj.dimensions.z / 2.0)
    if smooth:
        try:
            bpy.ops.object.shade_smooth_by_angle(angle=math.radians(35))
        except (AttributeError, RuntimeError, TypeError):  # Blender < 4.1
            bpy.ops.object.shade_smooth()
            if hasattr(obj.data, "use_auto_smooth"):
                obj.data.use_auto_smooth = True
                obj.data.auto_smooth_angle = math.radians(35)
    return Vector((0.0, 0.0, obj.dimensions.z / 2.0)), 0.5 * obj.dimensions.length


def _target(center):
    empty = bpy.data.objects.new("VulcanTarget", None)
    bpy.context.scene.collection.objects.link(empty)
    empty.location = center
    return empty


def _track(obj, target):
    con = obj.constraints.new(type="TRACK_TO")
    con.target = target
    con.track_axis = "TRACK_NEGATIVE_Z"
    con.up_axis = "UP_Y"


def _floor(spec, engine, bg, wall=3.2, radius=2.0, reach=40.0, steps=12):
    """A photo backdrop (cyclorama): floor, a curved sweep and a back wall, so no horizon line shows; turned per view."""
    if spec["transparent"] and engine != "CYCLES":
        return None  # nothing to catch the shadow without Cycles' shadow catcher
    profile = [(-reach, 0.0), (wall - radius, 0.0)]
    for i in range(1, steps + 1):
        a = (math.pi / 2) * i / steps
        profile.append((wall - radius + radius * math.sin(a), radius - radius * math.cos(a)))
    profile.append((wall, reach))
    verts = [(x, y, z) for x in (-reach, reach) for y, z in profile]
    n = len(profile)
    faces = [(i, i + 1, n + i + 1, n + i) for i in range(n - 1)]
    mesh = bpy.data.meshes.new("VulcanBackdrop")
    mesh.from_pydata(verts, [], faces)
    mesh.update()
    for poly in mesh.polygons:
        poly.use_smooth = True
    backdrop = bpy.data.objects.new("VulcanBackdrop", mesh)
    bpy.context.scene.collection.objects.link(backdrop)
    backdrop.data.materials.append(_material(bg, 0.9))
    if spec["transparent"] and hasattr(backdrop, "is_shadow_catcher"):
        backdrop.is_shadow_catcher = True
    return backdrop


def _lights(target):
    for name, loc, power, size in (("Key", (3.2, -3.4, 4.2), 340.0, 3.0), ("Fill", (-4.2, -2.0, 2.2), 100.0, 4.0),
                                   ("Rim", (0.4, 4.4, 3.4), 200.0, 2.5)):
        data = bpy.data.lights.new(name, type="AREA")
        data.energy = power
        data.size = size
        light = bpy.data.objects.new(name, data)
        bpy.context.scene.collection.objects.link(light)
        light.location = loc
        _track(light, target)


def _world_and_engine(spec, engine, bg):
    scene = bpy.context.scene
    world = bpy.data.worlds.new("VulcanWorld")
    scene.world = world
    world.color = bg  # Workbench background
    world.use_nodes = True
    node = world.node_tree.nodes.get("Background")
    if node is not None:
        node.inputs["Color"].default_value = (*bg, 1.0)
        node.inputs["Strength"].default_value = 0.55
    r = scene.render
    r.engine = engine
    r.resolution_x = r.resolution_y = int(spec["size"])
    r.resolution_percentage = 100
    r.film_transparent = bool(spec["transparent"])
    r.image_settings.file_format = "PNG"
    r.image_settings.color_mode = "RGBA" if spec["transparent"] else "RGB"
    if engine == "BLENDER_WORKBENCH":
        shading = scene.display.shading
        shading.light = "STUDIO"
        shading.color_type = "MATERIAL"
        shading.show_shadows = True
        shading.shadow_intensity = 0.35
        shading.show_cavity = True
        shading.cavity_type = "BOTH"
        scene.display.render_aa = "8"
        scene.view_settings.view_transform = "Standard"
        scene.view_settings.exposure = 0.75  # studio light lands at ~60 %: lift it so the backdrop reads as the chosen colour
    elif engine == "CYCLES":
        scene.cycles.device = "CPU"  # the GPUs stay free for the language models
        scene.cycles.samples = int(spec["samples"])
        scene.cycles.use_denoising = True
    else:
        try:
            scene.eevee.taa_render_samples = int(spec["samples"])
        except AttributeError:
            pass


def main():
    spec = _spec()
    started = time.time()
    bpy.ops.wm.read_factory_settings(use_empty=True)
    engine = _engine_id(spec["engine"])
    bg = _lin(spec["background"])
    obj = _import_stl(spec["mesh"])
    center, radius = _place(obj, spec.get("smooth", True))
    obj.data.materials.clear()
    obj.data.materials.append(_material(_lin(spec["color"]), float(spec.get("roughness", 0.42))))
    target = _target(center)
    backdrop = _floor(spec, engine, bg)
    if engine != "BLENDER_WORKBENCH":
        _lights(target)
    _world_and_engine(spec, engine, bg)
    cam_data = bpy.data.cameras.new("VulcanCamera")
    cam_data.lens = 50.0
    cam_data.sensor_width = 36.0
    cam = bpy.data.objects.new("VulcanCamera", cam_data)
    bpy.context.scene.collection.objects.link(cam)
    bpy.context.scene.camera = cam
    _track(cam, target)
    half = math.atan(18.0 / 50.0)
    distance = radius / math.sin(half) * float(spec.get("margin", 1.1))
    cam_data.clip_start = distance * 0.01
    cam_data.clip_end = distance * 6.0
    views = []
    for view in spec["views"]:
        direction = Vector(DIRECTIONS[view]).normalized()
        cam.location = center + direction * distance
        if backdrop is not None:  # the wall stands behind the model as seen from this camera
            backdrop.rotation_euler = (0.0, 0.0, math.atan2(direction.x, -direction.y))
        bpy.context.scene.render.filepath = spec["out"][view]
        t0 = time.time()
        bpy.ops.render.render(write_still=True)
        views.append({"view": view, "seconds": round(time.time() - t0, 2)})
    print("VULCAN_RESULT " + json.dumps({"ok": True, "engine": engine, "blender": bpy.app.version_string, "views": views,
                                         "triangles": len(obj.data.polygons), "seconds": round(time.time() - started, 2)}))


try:
    main()
except Exception as error:  # report, do not hang Blender
    print("VULCAN_RESULT " + json.dumps({"ok": False, "error": f"{type(error).__name__}: {error}"}))
    sys.exit(1)
