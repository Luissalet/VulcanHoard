"""Real Figure Tools preparation, UV bake, reload and render. Run inside Blender.

All mutations happen in a separate Blender process and its private output copy.
The installed Figure Tools operators and node groups are used unchanged.
"""

import bpy, json, sys, time, math, hashlib
from pathlib import Path
from mathutils import Vector
import numpy as np

config = json.loads(Path(sys.argv[sys.argv.index("--") + 1]).read_text("utf-8"))
OUT = Path(config["output"]).resolve()
OUT.mkdir(parents=True, exist_ok=True)
phase = config.get("phase", "prepare")
start = time.perf_counter()


def select(obj):
    if bpy.context.object and bpy.context.object.mode != "OBJECT":
        bpy.ops.object.mode_set(mode="OBJECT")
    bpy.ops.object.select_all(action="DESELECT")
    obj.select_set(True)
    bpy.context.view_layer.objects.active = obj


def sockets(mod):
    return {
        s.name: s.identifier
        for s in mod.node_group.interface.items_tree
        if s.item_type == "SOCKET" and s.in_out == "INPUT"
    }


def mesh_stats(obj):
    evaluated = obj.evaluated_get(bpy.context.evaluated_depsgraph_get())
    mesh = evaluated.to_mesh()
    try:
        edge_ids = np.empty(len(mesh.loops), np.int32)
        mesh.loops.foreach_get("edge_index", edge_ids)
        incidence = np.bincount(edge_ids, minlength=len(mesh.edges))
        if config.get("boundary_diagnostics"):
            vertices = np.empty(len(mesh.vertices) * 3, np.float32)
            mesh.vertices.foreach_get("co", vertices)
            edges = np.empty(len(mesh.edges) * 2, np.int32)
            mesh.edges.foreach_get("vertices", edges)
            positions = vertices.reshape(-1, 3)[edges.reshape(-1, 2)[incidence == 1]]
            np.savez_compressed(OUT / f"{config['label']}-boundaries.npz", positions=positions)
        return {
            "vertices": len(mesh.vertices),
            "faces": len(mesh.polygons),
            "boundary_edges": int(np.sum(incidence == 1)),
            "nonmanifold_edges": int(np.sum(incidence > 2)),
        }
    finally:
        evaluated.to_mesh_clear()


def texture_node(mat):
    if not mat.node_tree:
        return None
    nodes = [
        n
        for n in mat.node_tree.nodes
        if n.type == "TEX_IMAGE" and n.image and n.image.source == "FILE"
    ]
    return next(
        (
            n
            for n in nodes
            if any(l.to_socket.name == "Base Color" for l in n.outputs["Color"].links)
        ),
        nodes[0] if nodes else None,
    )


def smooth_inspection_normals(obj):
    # Last in the stack: changes display normals, not displacement or positions.
    name = "SculptorsHoard_InspectionSmooth"
    group = bpy.data.node_groups.new(name, "GeometryNodeTree")
    group.interface.new_socket(name="Geometry", in_out="INPUT", socket_type="NodeSocketGeometry")
    group.interface.new_socket(name="Geometry", in_out="OUTPUT", socket_type="NodeSocketGeometry")
    source = group.nodes.new("NodeGroupInput")
    target = group.nodes.new("NodeGroupOutput")
    smooth = group.nodes.new("GeometryNodeSetShadeSmooth")
    smooth.domain = "FACE"
    smooth.inputs["Selection"].default_value = True
    smooth.inputs["Shade Smooth"].default_value = True
    group.links.new(source.outputs["Geometry"], smooth.inputs["Geometry"])
    group.links.new(smooth.outputs["Geometry"], target.inputs["Geometry"])
    modifier = obj.modifiers.new(name, "NODES")
    modifier.node_group = group


def cameras_and_lights(objects):
    scene = bpy.context.scene
    bounds = [o.matrix_world @ Vector(c) for o in objects for c in o.bound_box]
    lo = Vector(tuple(min(v[i] for v in bounds) for i in range(3)))
    hi = Vector(tuple(max(v[i] for v in bounds) for i in range(3)))
    center = (lo + hi) / 2
    size = max(hi - lo)
    scene.render.engine = "CYCLES"
    scene.cycles.samples = 12
    scene.cycles.use_denoising = True
    scene.render.resolution_x = 768
    scene.render.resolution_y = 768
    scene.render.resolution_percentage = 100
    scene.view_settings.view_transform = "AgX"
    scene.view_settings.look = "AgX - Medium High Contrast"
    scene.view_settings.exposure = 0
    world = bpy.data.worlds.get("SculptorsHoard_Eval_World") or bpy.data.worlds.new(
        "SculptorsHoard_Eval_World"
    )
    world.use_nodes = True
    world.node_tree.nodes["Background"].inputs[0].default_value = (0.19, 0.21, 0.22, 1)
    world.node_tree.nodes["Background"].inputs[1].default_value = 0.5
    scene.world = world
    for name, offset, power in [
        ("Key", (-1, -1.4, 1.8), 70),
        ("Fill", (1, -0.7, 0.6), 22),
        ("Rim", (0, 1, 1.4), 45),
    ]:
        light = bpy.data.objects.get("SculptorsHoard_Eval_" + name)
        if light is None:
            data = bpy.data.lights.new("SculptorsHoard_Eval_" + name, "AREA")
            light = bpy.data.objects.new(data.name, data)
            scene.collection.objects.link(light)
        light.data.energy = power * 0.18 * size * size
        light.data.size = size
        light.location = center + Vector(offset) * size
        light.rotation_euler = (center - light.location).to_track_quat("-Z", "Y").to_euler()
    camera = bpy.data.objects.get("SculptorsHoard_Eval_Camera")
    if camera is None:
        data = bpy.data.cameras.new("SculptorsHoard_Eval_Camera")
        camera = bpy.data.objects.new(data.name, data)
        scene.collection.objects.link(camera)
    camera.data.type = "ORTHO"
    camera.data.ortho_scale = size * 1.14
    scene.camera = camera
    return camera, center, size


def render_views(objects, prefix, clay=True, front_only=False, focus=None):
    scene = bpy.context.scene
    camera, center, size = cameras_and_lights(objects)
    if focus:
        center = Vector(focus["center"])
        size = focus["size"]
        camera.data.ortho_scale = size * 1.14
        if focus.get("aspect"):
            scene.render.resolution_y = round(scene.render.resolution_x / focus["aspect"])
    if clay:
        material = bpy.data.materials.get("SculptorsHoard_Eval_Clay") or bpy.data.materials.new(
            "SculptorsHoard_Eval_Clay"
        )
        material.use_nodes = True
        bsdf = material.node_tree.nodes.get("Principled BSDF")
        bsdf.inputs["Base Color"].default_value = (0.55, 0.55, 0.55, 1)
        bsdf.inputs["Roughness"].default_value = 0.75
        bpy.context.view_layer.material_override = material
        if config.get("inspection_engine", "workbench") == "workbench":
            scene.render.engine = "BLENDER_WORKBENCH"
            shading = scene.display.shading
            scene.display.render_aa = "32"
            shading.light = "STUDIO"
            shading.color_type = "SINGLE"
            shading.single_color = (0.6, 0.6, 0.6)
            shading.show_cavity = True
            shading.cavity_type = "BOTH"
            shading.curvature_ridge_factor = 1.5
            shading.curvature_valley_factor = 1.2
            shading.cavity_ridge_factor = 1.2
            shading.cavity_valley_factor = 1.2
            shading.show_shadows = config.get("inspection_shadows", False)
    else:
        bpy.context.view_layer.material_override = None
    angles = [("front", (0, -2, 0.27)), ("three-quarter", (1, -1.65, 0.4)), ("back", (0, 2, 0.25))]
    angles = config.get("view_offsets", angles)
    for label, offset in angles[:1] if front_only else angles:
        camera.location = center + Vector(offset) * size
        camera.rotation_euler = (center - camera.location).to_track_quat("-Z", "Y").to_euler()
        scene.render.filepath = str(OUT / f"{prefix}-{label}.png")
        bpy.ops.render.render(write_still=True)
    bpy.context.view_layer.material_override = None
    return {"center": list(center), "size": size}


if phase == "assemble":
    target = set(config["target_materials"])
    originals = {m.name: m for m in bpy.data.materials}
    original_objects = {o.name: o for o in bpy.data.objects}
    context = []
    for mesh in [o for o in bpy.context.scene.objects if o.type == "MESH"]:
        used = {mesh.data.materials[f.material_index].name for f in mesh.data.polygons}
        if used and used <= target:
            context.append(mesh)
    if not context:
        raise ValueError("No se encontró la prenda de contexto que sustituir.")
    with bpy.data.libraries.load(config["garment_scene"], link=False) as (available, loaded):
        loaded.objects = available.objects
    garments = [
        o
        for o in loaded.objects
        if o
        and o.type == "MESH"
        and any(m.name.startswith("Dynamic_Displacement") for m in o.modifiers)
    ]
    if len(garments) != 1:
        raise ValueError("La escena de ropa debe tener una única prenda preparada.")
    garment = garments[0]
    # Link its rig before evaluating the imported transforms. Losing that parent
    # can otherwise move an apparently valid garment down to the world origin.
    for imported in loaded.objects:
        if imported and (imported == garment or imported.type == "ARMATURE"):
            bpy.context.scene.collection.objects.link(imported)
    bpy.context.view_layer.update()
    world = garment.matrix_world.copy()

    def base_name(name):
        return name.rsplit(".", 1)[0] if name.rsplit(".", 1)[-1].isdigit() else name

    if garment.parent:
        replacement = original_objects.get(base_name(garment.parent.name))
        if replacement:
            garment.parent = replacement
    for modifier in garment.modifiers:
        if modifier.type == "ARMATURE" and modifier.object:
            replacement = original_objects.get(base_name(modifier.object.name))
            if replacement:
                modifier.object = replacement
    garment.matrix_world = world
    # Preserve the exact material identities used by the body's texture inventory.
    remap = {}
    for i, material in enumerate(garment.data.materials):
        name = (
            material.name.rsplit(".", 1)[0]
            if material.name.rsplit(".", 1)[-1].isdigit()
            else material.name
        )
        if name in originals:
            remap[material] = originals[name]
            garment.data.materials[i] = originals[name]
    used = {garment.data.materials[f.material_index].name for f in garment.data.polygons}
    if used != target:
        raise ValueError(f"La pieza importada no coincide con los materiales solicitados: {used}")
    for modifier in garment.modifiers:
        if not modifier.name.startswith("Dynamic_Displacement"):
            continue
        for key in sockets(modifier).values():
            value = modifier.get(key)
            if isinstance(value, bpy.types.Material) and value in remap:
                modifier[key] = remap[value]
    for mesh in context:
        bpy.data.objects.remove(mesh, do_unlink=True)
    for obj in loaded.objects:
        if (
            obj
            and obj != garment
            and obj != garment.parent
            and not any(m.type == "ARMATURE" and m.object == obj for m in garment.modifiers)
        ):
            bpy.data.objects.remove(obj, do_unlink=True)
    garment.matrix_world = world
    bpy.context.view_layer.update()
    bpy.ops.wm.save_as_mainfile(filepath=str(OUT / "prepared.blend"))
    (OUT / "assemble-report.json").write_text(
        json.dumps(
            {
                "garment": garment.name,
                "materials": sorted(used),
                "seconds": round(time.perf_counter() - start, 2),
            },
            indent=2,
        ),
        "utf-8",
    )
    sys.exit(0)

if phase in {"prepare", "inspect"}:
    obj = max((o for o in bpy.data.objects if o.type == "MESH"), key=lambda o: len(o.data.vertices))
    select(obj)
    inventory = []
    obj.data.calc_loop_triangles()
    uv = obj.data.uv_layers.active.data
    for i, mat in enumerate(obj.data.materials):
        node = texture_node(mat)
        if not node:
            continue
        image = node.image
        path = Path(bpy.path.abspath(image.filepath)).resolve()
        if image.packed_file:
            raw = bytes(image.packed_file.data)
            packed_dir = OUT / "source_textures"
            packed_dir.mkdir(exist_ok=True)
            path = (
                packed_dir / f"{hashlib.sha256(raw).hexdigest()[:16]}_{path.name or 'texture.png'}"
            )
            if not path.exists():
                path.write_bytes(raw)
            unpacked = bpy.data.images.load(str(path), check_existing=True)
            unpacked.colorspace_settings.name = image.colorspace_settings.name
            node.image = unpacked
            image = unpacked
        triangles = [
            [[float(uv[j].uv.x), float(uv[j].uv.y)] for j in tri.loops]
            for tri in obj.data.loop_triangles
            if tri.material_index == i
        ]
        world_triangles = [
            [list(obj.matrix_world @ obj.data.vertices[j].co) for j in tri.vertices]
            for tri in obj.data.loop_triangles
            if tri.material_index == i
        ]
        inventory.append(
            {
                "material": mat.name,
                "source": str(path),
                "width": image.size[0],
                "height": image.size[1],
                "triangles": triangles,
                "world_triangles": world_triangles,
            }
        )
    (OUT / "materials.json").write_text(json.dumps(inventory), "utf-8")
    # Corresponding UV samples on opposite sides of actual material boundaries.
    # Matching a color elsewhere in the atlas is not evidence of a shared surface.
    edges = {}
    seams = []
    for face in obj.data.polygons:
        loops = list(face.loop_indices)
        center = sum((uv[j].uv for j in loops), Vector((0, 0))) / len(loops)
        for a, b in zip(loops, loops[1:] + loops[:1]):
            positions = [
                obj.matrix_world @ obj.data.vertices[obj.data.loops[j].vertex_index].co
                for j in [a, b]
            ]
            key = tuple(sorted(tuple(round(float(c), 3) for c in v) for v in positions))
            midpoint = (uv[a].uv + uv[b].uv) / 2
            sample = midpoint.lerp(center, 0.04)
            edges.setdefault(key, []).append(
                {
                    "material": obj.data.materials[face.material_index].name,
                    "uv": list(sample),
                    "normal": list(face.normal),
                }
            )
    for edge in edges.values():
        if (
            len(edge) == 2
            and edge[0]["material"] != edge[1]["material"]
            and Vector(edge[0]["normal"]).dot(Vector(edge[1]["normal"])) > 0.8
        ):
            seams.append(edge)
    (OUT / "seams.json").write_text(json.dumps(seams), "utf-8")
    if phase == "inspect":
        sys.exit(0)
    visibility = []
    for mesh in (o for o in bpy.data.objects if o.type == "MESH"):
        for modifier in mesh.modifiers:
            if modifier.name.startswith(("Dynamic_Displacement", "Corrective_Smooth")):
                visibility.append((modifier, modifier.show_viewport, modifier.show_render))
                modifier.show_viewport = False
                modifier.show_render = False
    # Bake the actual shader without lighting, using copies in this private process.
    # This avoids the existing bake's huge Python RGBA list at 8K; native export
    # later reads the original texture and exact UV footprint independently.
    saved_paths = []
    for image in list(bpy.data.images):
        if image.source == "FILE" and image.size[0]:
            path = Path(bpy.path.abspath(image.filepath))
            filename = (
                hashlib.sha256(str(path.resolve()).casefold().encode()).hexdigest()[:12] + ".png"
            )
            preview = OUT / "analysis_sources" / filename
            if not preview.is_file():
                preview = OUT / "analysis_sources" / path.name
            if preview.is_file():
                saved_paths.append((image, image.filepath))
                image.filepath = str(preview)
                image.reload()
    print("RELIEF_BAKE_START", flush=True)
    bake = OUT / "bake"
    bake.mkdir(exist_ok=True)
    bpy.context.scene.cycles.samples = 1
    target = set(config.get("target_materials", []))
    excluded = set(config.get("separate_materials", []))
    relevant = [
        item
        for item in inventory
        if (item["material"] in target if target else item["material"] not in excluded)
    ]
    for item in relevant:
        obj.active_material_index = next(
            i for i, m in enumerate(obj.data.materials) if m and m.name == item["material"]
        )
        result = bpy.ops.figure_tools.bake_scvi_material(
            directory=str(bake),
            bake_resolution=1024,
            margin=4,
            bake_all_materials=False,
            transparent_background=True,
            use_emit_trick=False,
        )
        assert result == {"FINISHED"}, result
    print("RELIEF_BAKE_DONE", flush=True)
    for image, path in saved_paths:
        image.filepath = path
        image.reload()
    objects = [o for o in bpy.data.objects if o.type == "MESH"]
    geometry = render_views(objects, "original", False)
    # The analyzed atlas stays in color; other materials become neutral gray.
    # This gives the VLM visible material scope, instead of asking it to infer
    # anatomical location from a filename or palette color alone.
    neutral = bpy.data.materials.new("SculptorsHoard_Context")
    neutral.use_nodes = True
    neutral.node_tree.nodes["Principled BSDF"].inputs["Base Color"].default_value = (
        0.25,
        0.25,
        0.25,
        1,
    )
    slots = [(mesh, i, mat) for mesh in objects for i, mat in enumerate(mesh.data.materials)]
    for item in [] if target else relevant:
        try:
            for mesh, i, mat in slots:
                mesh.data.materials[i] = mat if mat and mat.name == item["material"] else neutral
            render_views(objects, "focused-" + item["material"], False, front_only=True)
        finally:
            for mesh, i, mat in slots:
                mesh.data.materials[i] = mat
    for modifier, viewport, render in visibility:
        modifier.show_viewport = viewport
        modifier.show_render = render
    target = set(config.get("target_materials", []))
    if target - set(item["material"] for item in inventory):
        raise ValueError("El material solicitado no está en la malla seleccionada.")
    separate = (
        ({item["material"] for item in inventory} - target)
        if target
        else set(config.get("separate_materials", []))
    )
    if separate:
        bpy.ops.object.mode_set(mode="EDIT")
        bpy.ops.mesh.select_all(action="DESELECT")
        bpy.ops.object.mode_set(mode="OBJECT")
        for face in obj.data.polygons:
            face.select = obj.data.materials[face.material_index].name in separate
        bpy.ops.object.mode_set(mode="EDIT")
        bpy.ops.mesh.separate(type="SELECTED")
        bpy.ops.object.mode_set(mode="OBJECT")
    select(obj)
    if not any(m.name.startswith("Dynamic_Displacement") for m in obj.modifiers):
        if not obj.data.uv_layers.active:
            raise ValueError("La pieza no tiene un mapa UV activo.")
        uv_result = bpy.ops.object.add_uv_merger(distance=0.001)
        # The installed operator returns CANCELLED when no eligible pair exists.
        # A garment can already have continuous UVs and need no merge.
        if uv_result not in ({"FINISHED"}, {"CANCELLED"}):
            raise RuntimeError("UV Merger no terminó.")
        print("RELIEF_UV_MERGER", uv_result, flush=True)
        assert bpy.ops.object.add_merger() == {"FINISHED"}
        mod = next(m for m in obj.modifiers if m.name == "Merger")
        bpy.ops.object.modifier_apply(modifier=mod.name)
        assert bpy.ops.object.autoquad() == {"FINISHED"}
    obj.is_figure = True
    assert bpy.ops.figure_tools.auto_populate_displacement(rebuild=True) == {"FINISHED"}
    mod = next(m for m in obj.modifiers if m.name.startswith("Dynamic_Displacement"))
    keys = sockets(mod)
    for k, v in {
        "InitialMergeDistance": 0.01,
        "MergeDistance": 0.01,
        "Scale": 0.2,
        "SubdivisionLevel": 4,
    }.items():
        mod[keys[k]] = v
    obj.update_tag()
    bpy.context.view_layer.update()
    report = {
        "phase": phase,
        "object": obj.name,
        "settings": {
            "InitialMergeDistance": 0.01,
            "MergeDistance": 0.01,
            "Scale": 0.2,
            "SubdivisionLevel": 4,
        },
        "target_materials": list(target),
        "separated_materials": list(separate),
        "base_vertices": len(obj.data.vertices),
        "base_faces": len(obj.data.polygons),
        "geometry": geometry,
        "seconds": round(time.perf_counter() - start, 2),
    }
    bpy.ops.wm.save_as_mainfile(filepath=str(OUT / "prepared.blend"))
    (OUT / "prepare-report.json").write_text(json.dumps(report, indent=2), "utf-8")
    print("RELIEF_PREPARED", json.dumps(report))
else:
    objects = [o for o in bpy.context.scene.objects if o.type == "MESH"]
    figures = [
        o for o in objects if any(m.name.startswith("Dynamic_Displacement") for m in o.modifiers)
    ]
    if not figures:
        raise ValueError("La escena no contiene figuras preparadas.")
    bound = []
    for obj in figures:
        select(obj)
        mod = next(m for m in obj.modifiers if m.name.startswith("Dynamic_Displacement"))
        keys = sockets(mod)
        for i in range(1, obj.displacement_pairs + 1):
            mat = mod.get(keys.get(f"Material{i}", ""))
            path = config.get("maps", {}).get(mat.name if mat else "")
            if not path:
                continue
            image = bpy.data.images.load(str(Path(path).resolve()), check_existing=True)
            image.colorspace_settings.name = config.get("colorspace", "Non-Color")
            # Equivalent to Auto Reload + confirming Subdivision: explicitly reload
            # pixels, reassign the socket and tag the node tree/object for evaluation.
            image.reload()
            if config.get("neutral"):
                image = bpy.data.images.get("SculptorsHoard_Neutral") or bpy.data.images.new(
                    "SculptorsHoard_Neutral", width=8, height=8
                )
                image.colorspace_settings.name = "Non-Color"
                image.pixels[:] = [128 / 255, 128 / 255, 128 / 255, 1] * 64
            mod[keys[f"Image{i}"]] = image
            bound.append({"material": mat.name, "file": str(path), "object": obj.name})
        sub = mod[keys["SubdivisionLevel"]]
        mod[keys["SubdivisionLevel"]] = sub
        mod.node_group.update_tag()
        obj.data.update()
        obj.update_tag(refresh={"DATA"})
        bpy.context.view_layer.update()
    reference = config.get("reference", False)
    if reference:
        for mesh in (o for o in bpy.data.objects if o.type == "MESH"):
            for modifier in mesh.modifiers:
                if modifier.name.startswith(("Dynamic_Displacement", "Corrective_Smooth")):
                    modifier.show_viewport = False
                    modifier.show_render = False
        bpy.context.view_layer.update()
    else:
        for obj in figures:
            smooth_inspection_normals(obj)
        bpy.context.view_layer.update()
    if config.get("finish_voxel"):
        copies = []
        depsgraph = bpy.context.evaluated_depsgraph_get()
        for source in figures:
            mesh = bpy.data.meshes.new_from_object(
                source.evaluated_get(depsgraph), preserve_all_data_layers=False, depsgraph=depsgraph
            )
            copy = bpy.data.objects.new(source.name + "_Final", mesh)
            bpy.context.scene.collection.objects.link(copy)
            copy.matrix_world = source.matrix_world.copy()
            copies.append(copy)
            used = {source.data.materials[f.material_index].name for f in source.data.polygons}
            if used and used <= set(config.get("shell_materials", ["mTops"])):
                # Game clothing is often an open, zero-thickness sheet. Give it
                # inward thickness before volume union so the garment survives.
                if config.get("finish_caps"):
                    import bmesh

                    bm = bmesh.new()
                    bm.from_mesh(mesh)
                    boundary = [e for e in bm.edges if e.is_boundary]
                    # Relax the open rim along its own neighbours only. Smoothing
                    # all vertices would erase the garment's painted relief.
                    if config.get("finish_rim_smooth"):
                        neighbours = {
                            v: [e.other_vert(v) for e in v.link_edges if e.is_boundary]
                            for e in boundary
                            for v in e.verts
                        }
                        for _ in range(config["finish_rim_smooth"]):
                            coordinates = {
                                v: (
                                    v.co * 0.5 + sum((n.co for n in ns), Vector()) * (0.5 / len(ns))
                                )
                                for v, ns in neighbours.items()
                                if len(ns) == 2
                            }
                            for v, co in coordinates.items():
                                v.co = co
                    bmesh.ops.holes_fill(bm, edges=boundary, sides=0)
                    bmesh.ops.recalc_face_normals(bm, faces=list(bm.faces))
                    bm.to_mesh(mesh)
                    bm.free()
                else:
                    select(copy)
                    shell = copy.modifiers.new("SculptorsHoard_InnerShell", "SOLIDIFY")
                    shell.thickness = config.get("shell_thickness", 0.06)
                    shell.offset = -1
                    bpy.ops.object.modifier_apply(modifier=shell.name)
            source.hide_render = True
            source.hide_set(True)
        select(copies[0])
        for copy in copies:
            copy.select_set(True)
        bpy.ops.object.join()
        finished = bpy.context.object
        bpy.ops.object.transform_apply(location=False, rotation=False, scale=True)
        remesh = finished.modifiers.new("SculptorsHoard_Union", "REMESH")
        remesh.mode = "VOXEL"
        remesh.voxel_size = config["finish_voxel"]
        remesh.use_smooth_shade = True
        bpy.ops.object.modifier_apply(modifier=remesh.name)
        polish = finished.modifiers.new("SculptorsHoard_SurfacePolish", "SMOOTH")
        polish.factor = 0.5
        polish.iterations = 4
        bpy.ops.object.modifier_apply(modifier=polish.name)
        objects = [finished]
        figures = [finished]
        if config.get("export_stl"):
            bpy.ops.wm.stl_export(
                filepath=str(OUT / "figure-ready.stl"), export_selected_objects=True
            )
    label = config.get("label", "relief")
    pieces = {obj.name: mesh_stats(obj) for obj in figures}
    stats = {
        key: sum(piece[key] for piece in pieces.values()) for key in next(iter(pieces.values()))
    }
    geometry = render_views(
        objects,
        label,
        not reference,
        front_only=config.get("front_only", False),
        focus=config.get("focus"),
    )
    if config.get("detail_focus"):
        render_views(
            objects,
            label + "-detail",
            True,
            front_only=config["detail_focus"].get("scope") != "material",
            focus=config["detail_focus"],
        )
    bpy.ops.wm.save_as_mainfile(filepath=str(OUT / f"{label}.blend"))
    (OUT / f"{label}-report.json").write_text(
        json.dumps(
            {
                "phase": phase,
                "inspection_style": "smooth-v2-shadowless",
                "inspection_engine": bpy.context.scene.render.engine,
                "control_alpha": config.get("control_alpha"),
                "bound": bound,
                "mesh": stats,
                "pieces": pieces,
                "reloaded_images": len(bound),
                "subdivision_reconfirmed": sub,
                "geometry": geometry,
                "seconds": round(time.perf_counter() - start, 2),
            },
            indent=2,
        ),
        "utf-8",
    )
    print("RELIEF_RENDERED", label, len(bound))
