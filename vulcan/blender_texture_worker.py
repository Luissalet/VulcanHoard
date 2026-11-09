"""Runs INSIDE Blender (``blender -b --factory-startup --python blender_texture_worker.py -- spec.json``), never imported by
Vulcan (it needs ``bpy``). texture_project.py writes the spec and reads the ``VULCAN_RESULT`` line this prints.

Phases (``spec["phase"]``):

* ``guides``: the model (already normalised: largest side 2, centred, base on z=0) is rendered from the planned cameras with
  Cycles on the CPU (the GPUs stay free): a 16-bit depth map (``depth16-<cam>.png``), a camera-space normal map
  (``normal-<cam>.png``) and a grey clay shot (``base-<cam>.png``). With ``bake`` it also unwraps the mesh (smart project),
  bakes the 3D position and the normal of every texel into float images, saves them as ``bake.npz`` and keeps the unwrapped
  scene as ``scene.blend``.
* ``assemble``: opens ``scene.blend``, puts the finished texture on the mesh, renders preview shots and exports a GLB in metres.

Works on Blender 3.6 to 5.x.
"""

import json
import math
import sys
import time

import bpy
from mathutils import Vector

LENS = 50.0
SENSOR = 36.0


def _spec():
    argv = sys.argv[sys.argv.index("--") + 1:] if "--" in sys.argv else []
    with open(argv[0], encoding="utf-8") as fh:
        return json.load(fh)


def _import_stl(path):
    before = set(bpy.data.objects)
    if hasattr(bpy.ops.wm, "stl_import"):
        bpy.ops.wm.stl_import(filepath=path)
    else:  # Blender < 4.2
        bpy.ops.import_mesh.stl(filepath=path)
    objs = [o for o in bpy.data.objects if o not in before and o.type == "MESH"]
    if not objs:
        raise RuntimeError("the STL importer produced no mesh")
    return objs[0]


def _smooth(obj):
    bpy.context.view_layer.objects.active = obj
    obj.select_set(True)
    try:
        bpy.ops.object.shade_smooth_by_angle(angle=math.radians(40))
    except (AttributeError, RuntimeError, TypeError):
        bpy.ops.object.shade_smooth()


def _scene_cycles(size, samples, denoise):
    scene = bpy.context.scene
    r = scene.render
    r.engine = "CYCLES"
    r.resolution_x = r.resolution_y = int(size)
    r.resolution_percentage = 100
    r.film_transparent = False
    r.image_settings.file_format = "PNG"
    scene.cycles.device = "CPU"
    scene.cycles.samples = int(samples)
    for name, value in (("use_denoising", bool(denoise)), ("use_adaptive_sampling", False), ("max_bounces", 3)):
        try:
            setattr(scene.cycles, name, value)
        except (AttributeError, TypeError):
            pass
    return scene


def _transform(scene, raw):
    """Raw: the pixel is the linear value written by the shader (depth and normals); Standard: a normal sRGB picture."""
    ok = True
    try:
        scene.view_settings.view_transform = "Raw" if raw else "Standard"
    except TypeError:
        scene.view_settings.view_transform = "Standard"
        ok = not raw
    for name, value in (("look", "None"), ("exposure", 0.0), ("gamma", 1.0)):
        try:
            setattr(scene.view_settings, name, value)
        except (AttributeError, TypeError):
            pass
    return ok


def _world(color, strength):
    scene = bpy.context.scene
    world = bpy.data.worlds.new("VulcanWorld")
    scene.world = world
    world.use_nodes = True
    node = world.node_tree.nodes.get("Background")
    if node is not None:
        node.inputs["Color"].default_value = (*color, 1.0)
        node.inputs["Strength"].default_value = strength
    return world


def _set_world(world, color, strength):
    node = world.node_tree.nodes.get("Background")
    node.inputs["Color"].default_value = (*color, 1.0)
    node.inputs["Strength"].default_value = strength


def _new_material(name):
    mat = bpy.data.materials.new(name)
    mat.use_nodes = True
    mat.node_tree.nodes.clear()
    return mat


def _emission_material(name, builder):
    mat = _new_material(name)
    nt = mat.node_tree
    emit = nt.nodes.new("ShaderNodeEmission")
    out = nt.nodes.new("ShaderNodeOutputMaterial")
    source = builder(nt)
    nt.links.new(source, emit.inputs["Color"])
    nt.links.new(emit.outputs["Emission"], out.inputs["Surface"])
    return mat


def _depth_material(near, far):
    def build(nt):
        cam = nt.nodes.new("ShaderNodeCameraData")
        rng = nt.nodes.new("ShaderNodeMapRange")
        rng.clamp = True
        rng.inputs["From Min"].default_value = far
        rng.inputs["From Max"].default_value = near
        rng.inputs["To Min"].default_value = 0.05
        rng.inputs["To Max"].default_value = 1.0
        nt.links.new(cam.outputs["View Z Depth"], rng.inputs["Value"])
        return rng.outputs["Result"]

    return _emission_material("VulcanDepth", build)


def _normal_material():
    def build(nt):
        geo = nt.nodes.new("ShaderNodeNewGeometry")
        vt = nt.nodes.new("ShaderNodeVectorTransform")
        vt.vector_type = "NORMAL"
        vt.convert_from = "WORLD"
        vt.convert_to = "CAMERA"
        nt.links.new(geo.outputs["Normal"], vt.inputs["Vector"])
        return _pack(nt, vt.outputs["Vector"], 0.5, 0.5)

    return _emission_material("VulcanNormal", build)


def _pack(nt, socket, scale, offset):
    """value * scale + offset on every channel, so negative numbers survive an emission colour."""
    node = nt.nodes.new("ShaderNodeVectorMath")
    node.operation = "MULTIPLY_ADD"
    node.inputs[1].default_value = (scale, scale, scale)
    node.inputs[2].default_value = (offset, offset, offset)
    nt.links.new(socket, node.inputs[0])
    return node.outputs["Vector"]


def _position_material():
    def build(nt):
        geo = nt.nodes.new("ShaderNodeNewGeometry")
        return _pack(nt, geo.outputs["Position"], 0.25, 0.5)  # [-1, 1] x [-1, 1] x [0, 2] -> [0.25, 0.75] x [0.25, 0.75] x [0.5, 1]

    return _emission_material("VulcanPosition", build)


def _world_normal_material():
    def build(nt):
        geo = nt.nodes.new("ShaderNodeNewGeometry")
        return _pack(nt, geo.outputs["Normal"], 0.5, 0.5)

    return _emission_material("VulcanWorldNormal", build)


def _clay_material(color=(0.62, 0.60, 0.57)):
    mat = bpy.data.materials.new("VulcanClay")
    mat.use_nodes = True
    bsdf = mat.node_tree.nodes.get("Principled BSDF")
    bsdf.inputs["Base Color"].default_value = (*color, 1.0)
    bsdf.inputs["Roughness"].default_value = 0.75
    return mat


def _assign(obj, mat):
    obj.data.materials.clear()
    obj.data.materials.append(mat)


def _lights(target, power=1.0):
    made = []
    for name, loc, energy, size in (("Key", (3.2, -3.4, 4.2), 340.0, 3.0), ("Fill", (-4.2, -2.0, 2.2), 120.0, 4.0),
                                    ("Rim", (0.4, 4.4, 3.4), 200.0, 2.5)):
        data = bpy.data.lights.new(name, type="AREA")
        data.energy = energy * power
        data.size = size
        light = bpy.data.objects.new(name, data)
        bpy.context.scene.collection.objects.link(light)
        light.location = loc
        con = light.constraints.new(type="TRACK_TO")
        con.target = target
        con.track_axis = "TRACK_NEGATIVE_Z"
        con.up_axis = "UP_Y"
        made.append(light)
    return made


def _camera(center, distance):
    data = bpy.data.cameras.new("VulcanCamera")
    data.lens = LENS
    data.sensor_width = SENSOR
    data.clip_start = distance * 0.01
    data.clip_end = distance * 6.0
    cam = bpy.data.objects.new("VulcanCamera", data)
    bpy.context.scene.collection.objects.link(cam)
    bpy.context.scene.camera = cam
    target = bpy.data.objects.new("VulcanTarget", None)
    bpy.context.scene.collection.objects.link(target)
    target.location = center
    con = cam.constraints.new(type="TRACK_TO")
    con.target = target
    con.track_axis = "TRACK_NEGATIVE_Z"
    con.up_axis = "UP_Y"
    return cam, target


def _render(cam, location, path):
    cam.location = location
    bpy.context.scene.render.filepath = path
    bpy.ops.render.render(write_still=True)


def _unwrap(obj):
    bpy.context.view_layer.objects.active = obj
    obj.select_set(True)
    bpy.ops.object.mode_set(mode="EDIT")
    bpy.ops.mesh.select_all(action="SELECT")
    try:
        bpy.ops.uv.smart_project(angle_limit=math.radians(66.0), island_margin=0.003, scale_to_bounds=True)  # fill the atlas
    except TypeError:
        bpy.ops.uv.smart_project(angle_limit=math.radians(66.0), island_margin=0.003)
    bpy.ops.object.mode_set(mode="OBJECT")


def _bake_emission(obj, mat, size):
    import numpy

    image = bpy.data.images.new("VulcanBake", int(size), int(size), alpha=True, float_buffer=True)
    image.colorspace_settings.name = "Non-Color"
    tex = mat.node_tree.nodes.new("ShaderNodeTexImage")
    tex.image = image
    mat.node_tree.nodes.active = tex
    _assign(obj, mat)
    scene = bpy.context.scene
    try:
        scene.render.bake.margin = 24
        scene.render.bake.margin_type = "EXTEND"
    except (AttributeError, TypeError):
        pass
    bpy.context.view_layer.objects.active = obj
    obj.select_set(True)
    bpy.ops.object.bake(type="EMIT", use_clear=True)
    data = numpy.empty(int(size) * int(size) * 4, dtype=numpy.float32)
    image.pixels.foreach_get(data)
    return data.reshape(int(size), int(size), 4)[:, :, :3].copy()


def _bake(obj, size, out):
    import numpy

    scene = bpy.context.scene
    scene.cycles.samples = 1
    try:
        scene.cycles.use_denoising = False
    except (AttributeError, TypeError):
        pass
    _transform(scene, True)
    position = (_bake_emission(obj, _position_material(), size) - 0.5) * 4.0
    normal = _bake_emission(obj, _world_normal_material(), size) * 2.0 - 1.0
    covered = numpy.linalg.norm(normal, axis=2) > 0.2  # a baked texel holds a unit normal; the untouched ones stay at 0
    numpy.savez_compressed(out, position=position, normal=normal, covered=covered)
    return float(covered.mean())


def _guides(spec):
    started = time.time()
    bpy.ops.wm.read_factory_settings(use_empty=True)
    obj = _import_stl(spec["mesh"])
    if spec.get("smooth", True):
        _smooth(obj)
    center = Vector(spec["center"])
    cam, target = _camera(center, spec["distance"])
    scene = _scene_cycles(spec["size"], spec.get("samples", 6), False)
    world = _world((0.0, 0.0, 0.0), 0.0)
    lights = _lights(target, 0.4)
    cams = spec["cameras"]
    raw_ok = True

    def each(kind, mat, raw, color_mode, depth, bg, strength, lit, denoise=False, samples=None):
        nonlocal raw_ok
        _assign(obj, mat)
        _set_world(world, bg, strength)
        for light in lights:
            light.hide_render = not lit
        raw_ok = _transform(scene, raw) and raw_ok
        scene.cycles.samples = int(samples or spec.get("samples", 6))
        try:
            scene.cycles.use_denoising = denoise
        except (AttributeError, TypeError):
            pass
        scene.render.image_settings.color_mode = color_mode
        scene.render.image_settings.color_depth = depth
        for item in cams:
            _render(cam, item["location"], spec["out"][kind][item["name"]])

    each("depth", _depth_material(spec["near"], spec["far"]), True, "BW", "16", (0.0, 0.0, 0.0), 0.0, False)
    each("normal", _normal_material(), True, "RGB", "8", (0.0, 0.0, 0.0), 0.0, False)
    each("base", _clay_material(), False, "RGB", "8", (0.4, 0.4, 0.4), 0.7, True, denoise=True, samples=spec.get("base_samples", 24))
    baked = None
    if spec.get("bake"):
        _unwrap(obj)
        baked = _bake(obj, spec["texture_size"], spec["bake_out"])
        bpy.ops.wm.save_as_mainfile(filepath=spec["blend_out"], compress=True)
    return {"ok": True, "blender": bpy.app.version_string, "raw_ok": raw_ok, "cameras": len(cams), "triangles": len(obj.data.polygons),
            "baked_fraction": baked, "seconds": round(time.time() - started, 2)}


def _assemble(spec):
    started = time.time()
    obj = next(o for o in bpy.data.objects if o.type == "MESH")
    image = bpy.data.images.load(spec["texture"])
    image.colorspace_settings.name = "sRGB"
    mat = bpy.data.materials.new("VulcanTexture")
    mat.use_nodes = True
    nt = mat.node_tree
    bsdf = nt.nodes.get("Principled BSDF")
    tex = nt.nodes.new("ShaderNodeTexImage")
    tex.image = image
    tex.extension = "EXTEND"
    nt.links.new(tex.outputs["Color"], bsdf.inputs["Base Color"])
    bsdf.inputs["Roughness"].default_value = 0.7
    _assign(obj, mat)
    center = Vector(spec["center"])
    cam, target = _camera(center, spec["distance"])
    scene = _scene_cycles(spec["preview_size"], 24, True)
    _transform(scene, False)
    _world((0.75, 0.75, 0.75), 1.0)
    _lights(target)
    scene.render.image_settings.color_mode = "RGB"
    scene.render.image_settings.color_depth = "8"
    for item in spec["preview_cameras"]:
        _render(cam, item["location"], spec["preview_out"][item["name"]])
    bpy.ops.object.select_all(action="DESELECT")
    obj.select_set(True)
    bpy.context.view_layer.objects.active = obj
    factor = float(spec["export_scale"])
    obj.scale = (factor, factor, factor)
    bpy.ops.object.transform_apply(location=False, rotation=False, scale=True)
    bpy.ops.export_scene.gltf(filepath=spec["glb_out"], export_format="GLB", use_selection=True)
    return {"ok": True, "blender": bpy.app.version_string, "seconds": round(time.time() - started, 2)}


try:
    _spec_data = _spec()
    _result = _assemble(_spec_data) if _spec_data["phase"] == "assemble" else _guides(_spec_data)
    print("VULCAN_RESULT " + json.dumps(_result))
except Exception as error:  # report, do not hang Blender
    print("VULCAN_RESULT " + json.dumps({"ok": False, "error": f"{type(error).__name__}: {error}"}))
    sys.exit(1)
