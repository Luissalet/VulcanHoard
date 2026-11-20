"""Colours, materials and textures a 3MF file declares (its own XML, read with the standard library; no trimesh).

Reads ``basematerials`` (named base colours), the materials-extension ``colorgroup`` (per-triangle colours) and
``texture2d`` / ``texture2dgroup`` (image textures and their UV coordinates) from every model part, counts how many
triangles point at a property group (``pid`` on the triangle or on its object), and decodes the texture images for their
pixel size. What is returned is what the file DECLARES, not a render of it."""

from __future__ import annotations

from array import array
import posixpath
import zipfile
from xml.parsers import expat

MAX_COLOURS_LISTED = 64


def _local(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def _hex(value: str | None) -> str | None:
    text = (value or "").strip().lstrip("#").upper()
    return "#" + text if len(text) in (6, 8) and all(c in "0123456789ABCDEF" for c in text) else None


def _parse(stream, start, end=None):
    parser = expat.ParserCreate(namespace_separator="}")
    parser.StartElementHandler = start
    parser.EndElementHandler = end

    def reject_doctype(*_args):
        raise ValueError("Unexpected DTD in 3MF model")

    parser.StartDoctypeDeclHandler = reject_doctype
    while chunk := stream.read(256 * 1024):
        parser.Parse(chunk, False)
    parser.Parse(b"", True)


def direct_meshes(path):
    """Read indexed Core meshes of a simple build without retaining its XML tree.

    Components and build transforms go through the general-purpose loader.
    """
    import numpy as np

    objects, build = {}, []
    unit = points = faces = None
    unsupported = False

    def start(tag, attributes):
        nonlocal unit, points, faces, unsupported
        name = _local(tag)
        if name == "model":
            unit = attributes.get("unit", "millimeter")
        elif name == "object":
            points, faces = array("d"), array("q")
            objects[attributes["id"]] = (points, faces)
        elif name == "vertex":
            points.extend((float(attributes["x"]), float(attributes["y"]), float(attributes["z"])))
        elif name == "triangle":
            faces.extend((int(attributes["v1"]), int(attributes["v2"]), int(attributes["v3"])))
        elif name == "component":
            unsupported = True
        elif name == "item":
            unsupported |= any(key == "transform" or key.endswith("}path") for key in attributes)
            build.append(attributes["objectid"])

    with zipfile.ZipFile(path) as archive:
        models = [name for name in archive.namelist() if name.lower().endswith(".model")]
        if len(models) != 1:
            return None
        try:
            with archive.open(models[0]) as stream:
                _parse(stream, start)
        except (KeyError, expat.ExpatError) as exc:
            raise ValueError(f"Invalid 3MF Core model: {exc}") from exc
    if unsupported or not build or any(ident not in objects or not objects[ident][1] for ident in build):
        return None
    meshes = [(np.frombuffer(objects[ident][0], dtype=np.float64).reshape(-1, 3),
               np.frombuffer(objects[ident][1], dtype=np.int64).reshape(-1, 3)) for ident in build]
    if any(not len(vertices) or not np.isfinite(vertices).all() or faces.min() < 0 or faces.max() >= len(vertices)
           for vertices, faces in meshes):
        raise ValueError("Invalid 3MF mesh vertices or triangle indices")
    return unit, meshes


def read_3mf_materials(path) -> dict:
    """{base_materials[], colour_groups, colours[], texture_groups, uv_coordinates, textures[], triangles, triangles_with_property,
    objects_with_property}. Raises ValueError when the archive or its model XML cannot be read."""
    try:
        archive = zipfile.ZipFile(path)
    except (zipfile.BadZipFile, OSError) as error:
        raise ValueError(f"not a readable 3MF archive: {error}") from error
    with archive:
        parts = [n for n in archive.namelist() if n.lower().endswith(".model")]
        if not parts:
            raise ValueError("no .model part in the 3MF archive")
        base, colour_groups, colours, texture_groups, uv_total = [], 0, [], 0, 0
        texture_refs: list[tuple[str, str | None]] = []
        triangles = with_property = objects_with_property = 0
        stack = []

        def start(tag, attributes):
            nonlocal colour_groups, texture_groups, uv_total, triangles, with_property, objects_with_property
            name = _local(tag)
            parent = stack[-1] if stack else ""
            stack.append(name)
            if name == "base" and parent == "basematerials":
                base.append({"name": attributes.get("name") or "", "color": _hex(attributes.get("displaycolor"))})
            elif name == "colorgroup":
                colour_groups += 1
            elif name == "color" and parent == "colorgroup":
                color = _hex(attributes.get("color"))
                if color:
                    colours.append(color)
            elif name == "texture2dgroup":
                texture_groups += 1
            elif name == "tex2coord" and parent == "texture2dgroup":
                uv_total += 1
            elif name == "texture2d":
                texture_refs.append((attributes.get("path") or "", attributes.get("contenttype")))
            elif name == "object":
                objects_with_property += "pid" in attributes
            elif name == "triangle":
                triangles += 1
                with_property += "pid" in attributes or "p1" in attributes

        for part in parts:
            try:
                with archive.open(part) as stream:
                    _parse(stream, start, lambda _tag: stack.pop())
            except expat.ExpatError as error:
                raise ValueError(f"{part}: invalid XML ({error})") from error
        textures, seen = [], set()
        for ref, content_type in texture_refs:
            if ref in seen:
                continue
            seen.add(ref)
            member = posixpath.normpath(ref).lstrip("/")
            size = None
            if member in archive.namelist():
                from .asset_inspection import _image_size

                size = _image_size(archive.read(member))
            textures.append({"path": ref, "content_type": content_type, "px": size, "in_archive": member in archive.namelist()})
    unique = list(dict.fromkeys(colours))
    return {"base_materials": base, "colour_groups": colour_groups, "colours": unique[:MAX_COLOURS_LISTED], "colours_total": len(unique),
            "texture_groups": texture_groups, "uv_coordinates": uv_total, "textures": textures, "triangles": triangles,
            "triangles_with_property": with_property, "objects_with_property": objects_with_property}
