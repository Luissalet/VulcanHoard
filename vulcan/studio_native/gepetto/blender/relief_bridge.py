"""Sculptor’s Hoard bridge. Install this file as a Blender add-on.

Figure Tools is used for material separation, UV binding and subdivision.
Only project-local node-group copies are adapted for normalized signed maps.
"""

bl_info = {
    "name": "Sculptor’s Hoard Bridge",
    "author": "Sculptor’s Hoard",
    "version": (0, 1, 0),
    "blender": (4, 3, 0),
    "category": "Object",
    "location": "View3D > Sidebar > Sculptor’s Hoard",
}
import bpy
from bpy_extras.io_utils import ImportHelper
import json
import re
import tempfile
from pathlib import Path
import urllib.request
import uuid
import zipfile

API = "http://127.0.0.1:8767/api"


def request(path, data=None, content_type="application/json"):
    req = urllib.request.Request(API + path, data=data, headers={"Content-Type": content_type})
    with urllib.request.urlopen(req, timeout=180) as r:
        return json.loads(r.read())


def upload(pid, paths):
    boundary = "sculptors-hoard-" + uuid.uuid4().hex
    body = bytearray()
    for p in paths:
        safe_name = p.name.replace('"', "_").replace("\r", "").replace("\n", "")
        body.extend(
            f'--{boundary}\r\nContent-Disposition: form-data; name="files"; filename="{safe_name}"\r\nContent-Type: application/octet-stream\r\n\r\n'.encode()
        )
        body.extend(p.read_bytes())
        body.extend(b"\r\n")
    body.extend(f"--{boundary}--\r\n".encode())
    return request(
        f"/projects/{pid}/import", bytes(body), f"multipart/form-data; boundary={boundary}"
    )


def signed_displacement_group(original):
    """Preserve Figure Tools routing, replace its RGB weighting with R - 128/255."""
    group = original.copy()
    group.name = "SculptorsHoard_ImageDisplacement"
    transfer = bpy.data.node_groups.new("SculptorsHoard_SignedHeight", "GeometryNodeTree")
    transfer.interface.new_socket(name="Color", in_out="INPUT", socket_type="NodeSocketColor")
    transfer.interface.new_socket(name="Value", in_out="OUTPUT", socket_type="NodeSocketFloat")
    inp = transfer.nodes.new("NodeGroupInput")
    out = transfer.nodes.new("NodeGroupOutput")
    sep = transfer.nodes.new("FunctionNodeSeparateColor")
    sub = transfer.nodes.new("ShaderNodeMath")
    sub.operation = "SUBTRACT"
    sub.inputs[1].default_value = 128 / 255
    transfer.links.new(inp.outputs["Color"], sep.inputs["Color"])
    transfer.links.new(sep.outputs["Red"], sub.inputs[0])
    transfer.links.new(sub.outputs[0], out.inputs["Value"])
    targets = [
        n
        for n in group.nodes
        if n.type == "GROUP" and n.node_tree and n.node_tree.name.startswith("GrayScale")
    ]
    if len(targets) != 1:
        raise RuntimeError(
            "La versión de ImageDisplacement no tiene la conversión GrayScale esperada. No se ha modificado el addon original."
        )
    targets[0].node_tree = transfer
    return group


def normalized_material_name(name):
    return re.sub(r"_(alb|col).*", "", re.sub(r"\.\d+$", "", name.lower()))


def apply_export(obj, manifest, directory, strength=0.005, subdivision=2):
    if obj.type != "MESH" or not obj.data.uv_layers:
        raise RuntimeError("Selecciona una malla con UV.")
    if not hasattr(bpy.ops.figure_tools, "auto_populate_displacement"):
        raise RuntimeError("Activa Figure Tools antes de importar los mapas.")
    # Resolve every material before mutating the object.
    matches = []
    for material in obj.data.materials:
        if not material:
            continue
        candidates = [a for a in manifest["textures"] if a.get("material") == material.name]
        if not candidates:
            candidates = [
                a
                for a in manifest["textures"]
                if normalized_material_name(a["name"]) == normalized_material_name(material.name)
            ]
        if len(candidates) != 1:
            images = (
                {
                    Path(bpy.path.abspath(n.image.filepath)).name.lower()
                    for n in material.node_tree.nodes
                    if n.type == "TEX_IMAGE" and n.image
                }
                if material.node_tree
                else set()
            )
            candidates = [a for a in manifest["textures"] if a["name"].lower() in images]
        if len(candidates) != 1:
            raise RuntimeError(
                f"No hay una correspondencia única para {material.name}. Revisa los nombres de las texturas."
            )
        a = candidates[0]
        path = Path(directory) / f"{Path(a['name']).stem}_{a['id']}_height.png"
        if not path.is_file():
            raise RuntimeError(f"Falta {path.name}")
        matches.append((material, a, path))
    bpy.ops.object.select_all(action="DESELECT")
    obj.select_set(True)
    bpy.context.view_layer.objects.active = obj
    obj.is_figure = True
    result = bpy.ops.figure_tools.auto_populate_displacement(rebuild=True)
    if result != {"FINISHED"}:
        raise RuntimeError(f"Figure Tools devolvió {result}")
    mod = next(m for m in obj.modifiers if m.name.startswith("Dynamic_Displacement"))
    sockets = {
        s.name: s.identifier
        for s in mod.node_group.interface.items_tree
        if s.item_type == "SOCKET" and s.in_out == "INPUT"
    }
    if manifest.get("profile") == "figure-tools-native":
        settings = manifest.get("settings", {})
        strength = settings.get("Scale", strength)
        subdivision = settings.get("SubdivisionLevel", subdivision)
        for key in ["InitialMergeDistance", "MergeDistance"]:
            if key in settings and key in sockets:
                mod[sockets[key]] = settings[key]
    else:
        group_copy = mod.node_group.copy()
        group_copy.name = "SculptorsHoard_FigureTools"
        mod.node_group = group_copy
        for node in group_copy.nodes:
            if (
                node.type == "GROUP"
                and node.node_tree
                and node.node_tree.name == "ImageDisplacement"
            ):
                node.node_tree = signed_displacement_group(node.node_tree)
    mod[sockets["Scale"]] = strength
    mod[sockets["SubdivisionLevel"]] = subdivision
    bound = []
    for material, a, path in matches:
        pair = next(
            (
                i
                for i in range(1, obj.displacement_pairs + 1)
                if mod.get(sockets.get(f"Material{i}", "")) == material
            ),
            None,
        )
        if pair is None:
            raise RuntimeError(f"Figure Tools no ha creado pareja para {material.name}.")
        image = bpy.data.images.load(str(path.resolve()), check_existing=False)
        image.colorspace_settings.name = "Non-Color"
        image.name = f"SculptorsHoard_{a['name']}"
        mod[sockets[f"Image{pair}"]] = image
        mod[sockets[f"AddScale{pair}"]] = 0.0
        mod[sockets[f"MaterialSubdiv{pair}"]] = 0
        bound.append(
            {
                "material": material.name,
                "texture": a["name"],
                "size": list(image.size),
                "pair": pair,
            }
        )
    for i in range(1, obj.displacement_pairs + 1):
        image = mod.get(sockets.get(f"Image{i}", ""))
        if image:
            image.reload()
            mod[sockets[f"Image{i}"]] = image
    mod[sockets["SubdivisionLevel"]] = subdivision
    mod.node_group.update_tag()
    obj.data.update()
    obj.update_tag(refresh={"DATA"})
    bpy.context.view_layer.update()
    return {
        "bound": bound,
        "uv_attribute": mod[sockets["UVMap"] + "_attribute_name"],
        "strength": strength,
        "subdivision": subdivision,
        "neutral": 128 / 255,
        "group": mod.node_group.name,
    }


class RELIEF_OT_send(bpy.types.Operator):
    bl_idname = "relief.send_scene"
    bl_label = "Abrir en Sculptor’s Hoard"
    bl_options = {"REGISTER"}

    def execute(self, context):
        sources = [o for o in context.selected_objects if o.type == "MESH"] or [
            o for o in context.scene.objects if o.type == "MESH"
        ]
        if not sources:
            self.report({"ERROR"}, "No hay mallas para enviar.")
            return {"CANCELLED"}
        originals = list(context.selected_objects)
        active = context.view_layer.objects.active
        copies = []
        materials = []
        images = []
        files = {}
        try:
            request("/health")
            with tempfile.TemporaryDirectory(prefix="sculptors-hoard-") as temp:
                snapshot = Path(temp) / "figure-source.blend"
                saved_paths = [(im, im.filepath) for im in bpy.data.images if im.source == "FILE"]
                try:
                    for im, path in saved_paths:
                        im.filepath = bpy.path.abspath(path)
                    bpy.ops.wm.save_as_mainfile(
                        filepath=str(snapshot), copy=True, relative_remap=False
                    )
                finally:
                    for im, path in saved_paths:
                        im.filepath = path
                bpy.ops.object.select_all(action="DESELECT")
                for source in sources:
                    mesh = bpy.data.meshes.new_from_object(
                        source.evaluated_get(context.evaluated_depsgraph_get()),
                        preserve_all_data_layers=True,
                        depsgraph=context.evaluated_depsgraph_get(),
                    )
                    obj = bpy.data.objects.new(source.name + "_SculptorsHoardPreview", mesh)
                    context.collection.objects.link(obj)
                    obj.matrix_world = source.matrix_world.copy()
                    copies.append(obj)
                    obj.select_set(True)
                    for i, mat in enumerate(obj.data.materials):
                        if not mat:
                            continue
                        clone = mat.copy()
                        clone.name = mat.name
                        materials.append(clone)
                        obj.data.materials[i] = clone
                        if not clone.node_tree:
                            continue
                        for node in clone.node_tree.nodes:
                            if node.type != "TEX_IMAGE" or not node.image:
                                continue
                            original = node.image
                            path = Path(bpy.path.abspath(original.filepath))
                            if path.is_file():
                                files[str(path)] = path
                            copied = original.copy()
                            images.append(copied)
                            node.image = copied
                            if max(copied.size) > 1536:
                                factor = 1536 / max(copied.size)
                                copied.scale(
                                    max(1, int(copied.size[0] * factor)),
                                    max(1, int(copied.size[1] * factor)),
                                )
                context.view_layer.objects.active = copies[0]
                path = Path(temp) / "scene.glb"
                bpy.ops.export_scene.gltf(
                    filepath=str(path),
                    export_format="GLB",
                    use_selection=True,
                    export_animations=False,
                    export_skins=False,
                )
                p = request(
                    "/projects",
                    json.dumps(
                        {"name": Path(bpy.data.filepath).stem or "Figura de Blender"}
                    ).encode(),
                )
                result = upload(p["id"], [snapshot, path, *files.values()])
                if result.get("errors"):
                    raise RuntimeError("; ".join(result["errors"]))
                request("/desktop/open", json.dumps({"project": p["id"]}).encode())
            self.report({"INFO"}, "Modelo y texturas enviados. Los originales se conservan.")
            return {"FINISHED"}
        except Exception as e:
            self.report({"ERROR"}, str(e))
            return {"CANCELLED"}
        finally:
            for obj in copies:
                mesh = obj.data
                bpy.data.objects.remove(obj, do_unlink=True)
                bpy.data.meshes.remove(mesh)
            for mat in materials:
                bpy.data.materials.remove(mat)
            for im in images:
                bpy.data.images.remove(im)
            for o in originals:
                o.select_set(True)
            context.view_layer.objects.active = active


class RELIEF_OT_import(bpy.types.Operator, ImportHelper):
    bl_idname = "relief.import_maps"
    bl_label = "Cargar mapas en Figure Tools"
    bl_options = {"REGISTER", "UNDO"}
    filename_ext = ".zip"
    filter_glob: bpy.props.StringProperty(default="*.zip", options={"HIDDEN"})
    strength: bpy.props.FloatProperty(name="Fuerza de relieve", default=0.005, min=0.00001, max=1)
    subdivision: bpy.props.IntProperty(name="Subdivisión inicial", default=2, min=0, max=5)

    def execute(self, context):
        try:
            obj = context.active_object
            if not obj or obj.type != "MESH":
                raise RuntimeError("Selecciona la malla de la figura.")
            base = Path(self.filepath).resolve()
            directory = base.parent / (base.stem + "-maps-" + uuid.uuid4().hex[:6])
            directory.mkdir()
            with zipfile.ZipFile(base) as archive:
                if sum(i.file_size for i in archive.infolist()) > 3_000_000_000:
                    raise RuntimeError("El archivo excede el tamaño permitido.")
                for item in archive.infolist():
                    if Path(item.filename).name != item.filename or "\\" in item.filename:
                        raise RuntimeError("El ZIP contiene rutas no permitidas.")
                archive.extractall(directory)
            manifest_path = directory / "sculptors-hoard-project.json"
            if not manifest_path.is_file():
                manifest_path = directory / "relief-project.json"  # Legacy exported projects.
            manifest = json.loads(manifest_path.read_text("utf-8"))
            report = apply_export(obj, manifest, directory, self.strength, self.subdivision)
            self.report(
                {"INFO"},
                f"{len(report['bound'])} mapas vinculados. Ajusta fuerza y subdivisión en Figure Tools.",
            )
            return {"FINISHED"}
        except Exception as e:
            self.report({"ERROR"}, str(e))
            return {"CANCELLED"}


class RELIEF_PT_panel(bpy.types.Panel):
    bl_label = "Sculptor’s Hoard"
    bl_idname = "RELIEF_PT_panel"
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_category = "Sculptor’s Hoard"

    def draw(self, context):
        layout = self.layout
        layout.label(text="Modelo + UV + contexto")
        layout.operator("relief.send_scene", icon="EXPORT")
        layout.separator()
        layout.operator("relief.import_maps", icon="IMPORT")
        layout.label(text="Servidor local: puerto 8767", icon="INFO")


CLASSES = [RELIEF_OT_send, RELIEF_OT_import, RELIEF_PT_panel]


def register():
    for cls in CLASSES:
        bpy.utils.register_class(cls)


def unregister():
    for cls in reversed(CLASSES):
        bpy.utils.unregister_class(cls)


if __name__ == "__main__":
    register()
