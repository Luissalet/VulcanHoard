"""Extract the visible, evaluated Blender scene and its assigned color images.

Run in a separate Blender process. Never save over the supplied .blend.
Native images remain full resolution; only the embedded preview is reduced.
"""

import json
import shutil
import sys
import time
from pathlib import Path

import bpy


def upstream_images(socket, visited=None):
    visited = set() if visited is None else visited
    found = set()
    for link in socket.links:
        node = link.from_node
        if node in visited:
            continue
        visited.add(node)
        if node.type == "TEX_IMAGE" and node.image:
            found.add(node.image)
        else:
            for value in node.inputs:
                found.update(upstream_images(value, visited))
    return found


def color_image(material):
    if not material or not material.use_nodes:
        return None
    outputs = [
        n for n in material.node_tree.nodes if n.type == "OUTPUT_MATERIAL" and n.is_active_output
    ]
    pending = [link.from_node for n in outputs for link in n.inputs["Surface"].links]
    visited = set()
    images = set()
    while pending:
        node = pending.pop()
        if node in visited:
            continue
        visited.add(node)
        if node.type in {"BSDF_PRINCIPLED", "BSDF_DIFFUSE", "EMISSION"}:
            socket = node.inputs.get("Base Color") or node.inputs.get("Color")
            if socket:
                images.update(upstream_images(socket))
        else:
            pending.extend(link.from_node for socket in node.inputs for link in socket.links)
    if len(images) > 1:
        raise RuntimeError(
            f"El material {material.name} combina varias imágenes de color. Hornea ese material antes de importarlo."
        )
    return next(iter(images), None)


def extract(config):
    started = time.perf_counter()
    out = Path(config["output"]).resolve()
    textures = out / "textures"
    textures.mkdir(parents=True, exist_ok=True)
    objects = [o for o in bpy.context.scene.objects if o.type == "MESH" and o.visible_get()]
    if not objects:
        raise RuntimeError("El archivo Blender no contiene mallas visibles en la escena activa.")
    depsgraph = bpy.context.evaluated_depsgraph_get()
    materials = {m for o in objects for m in o.data.materials if m}
    entries, saved_images, color_images = [], {}, {}
    warnings = []
    for material in sorted(materials, key=lambda m: m.name):
        image = color_image(material)
        if image is None:
            warnings.append(
                f"{material.name}: sin imagen conectada al color; se conserva su geometría."
            )
            continue
        color_images[material] = image
        source = Path(bpy.path.abspath(image.filepath, library=image.library))
        if image not in saved_images:
            # Exact assigned image wins: never substitute a basename match from
            # another directory (notably low-resolution originals).
            if image.source == "TILED":
                raise RuntimeError(f"{material.name}: las texturas UDIM requieren horneado previo.")
            if image.source == "FILE" and not image.packed_file and not source.is_file():
                raise RuntimeError(
                    f"Falta la textura aplicada de {material.name}: {image.filepath}. Empaqueta los recursos en Blender y vuelve a guardar el .blend."
                )
            suffix = source.suffix.lower() if source.suffix else ".png"
            target = textures / f"image-{len(saved_images):03d}{suffix}"
            if image.packed_file and not image.is_dirty:
                target.write_bytes(image.packed_file.data)
            elif source.is_file() and not image.is_dirty:
                shutil.copyfile(source, target)
            else:
                target = target.with_suffix(".png")
                clone = image.copy()
                clone.filepath_raw = str(target)
                clone.file_format = "PNG"
                clone.save()
                bpy.data.images.remove(clone)
            saved_images[image] = target
        entries.append(
            {
                "material": material.name,
                "name": source.name or image.name,
                "path": str(saved_images[image]),
                "originalPath": image.filepath,
                "width": image.size[0],
                "height": image.size[1],
            }
        )
    # Make the evaluation copy independent of the original directory.
    for image, target in saved_images.items():
        if image.packed_file:
            image.unpack(method="REMOVE")
        image.filepath = str(target)
    snapshot = out / "prepared.blend"
    bpy.ops.wm.save_as_mainfile(filepath=str(snapshot), copy=True, relative_remap=False)

    bpy.ops.object.select_all(action="DESELECT")
    preview_materials = {}
    copies = []
    for original in objects:
        mesh = bpy.data.meshes.new_from_object(
            original.evaluated_get(depsgraph), preserve_all_data_layers=True, depsgraph=depsgraph
        )
        obj = bpy.data.objects.new(original.name + "_Preview", mesh)
        bpy.context.collection.objects.link(obj)
        obj.matrix_world = original.matrix_world.copy()
        obj.select_set(True)
        copies.append(obj)
        for index, material in enumerate(list(mesh.materials)):
            if not material:
                continue
            if material not in preview_materials:
                clone = material.copy()
                clone.name = material.name + "_Preview"
                if clone.node_tree:
                    for node in clone.node_tree.nodes:
                        if node.type != "TEX_IMAGE" or not node.image:
                            continue
                        image = node.image.copy()
                        node.image = image
                        if max(image.size) > 1536:
                            factor = 1536 / max(image.size)
                            image.scale(
                                max(1, int(image.size[0] * factor)),
                                max(1, int(image.size[1] * factor)),
                            )
                # Preserve a stable material identity in glTF despite Blender's
                # automatic .001 suffix for simultaneously existing materials.
                clone["sculptorsHoardMaterial"] = material.name
                preview_materials[material] = clone
            mesh.materials[index] = preview_materials[material]
    bpy.context.view_layer.objects.active = copies[0]
    result = bpy.ops.export_scene.gltf(
        filepath=str(out / "preview.glb"),
        export_format="GLB",
        use_selection=True,
        export_animations=False,
        export_skins=False,
        export_extras=True,
    )
    if result != {"FINISHED"}:
        raise RuntimeError("Blender no pudo crear la vista previa del modelo.")
    return {
        "objects": [o.name for o in objects],
        "textures": entries,
        "warnings": warnings,
        "seconds": round(time.perf_counter() - started, 2),
    }


if __name__ == "__main__":
    config = json.loads(Path(sys.argv[sys.argv.index("--") + 1]).read_text("utf-8"))
    try:
        report = extract(config)
    except Exception as error:
        report = {"error": str(error)}
    Path(config["output"]).joinpath("import-report.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2), "utf-8"
    )
