"""Colours, materials and textures a 3MF file declares (its own XML, read with the standard library; no trimesh).

Reads ``basematerials`` (named base colours), the materials-extension ``colorgroup`` (per-triangle colours) and
``texture2d`` / ``texture2dgroup`` (image textures and their UV coordinates) from every model part, counts how many
triangles point at a property group (``pid`` on the triangle or on its object), and decodes the texture images for their
pixel size. What is returned is what the file DECLARES, not a render of it."""

from __future__ import annotations

import io
import posixpath
import zipfile
from xml.etree import ElementTree as ET

MAX_COLOURS_LISTED = 64


def _local(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def _hex(value: str | None) -> str | None:
    text = (value or "").strip().lstrip("#").upper()
    return "#" + text if len(text) in (6, 8) and all(c in "0123456789ABCDEF" for c in text) else None


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
        for part in parts:
            try:
                root = ET.fromstring(archive.read(part))
            except ET.ParseError as error:
                raise ValueError(f"{part}: invalid XML ({error})") from error
            for node in root.iter():
                name = _local(node.tag)
                if name == "basematerials":
                    for item in node:
                        if _local(item.tag) == "base":
                            base.append({"name": item.get("name") or "", "color": _hex(item.get("displaycolor"))})
                elif name == "colorgroup":
                    colour_groups += 1
                    colours.extend(c for c in (_hex(i.get("color")) for i in node if _local(i.tag) == "color") if c)
                elif name == "texture2dgroup":
                    texture_groups += 1
                    uv_total += sum(1 for i in node if _local(i.tag) == "tex2coord")
                elif name == "texture2d":
                    texture_refs.append((node.get("path") or "", node.get("contenttype")))
                elif name == "object":
                    objects_with_property += int(node.get("pid") is not None)
                elif name == "triangle":
                    triangles += 1
                    with_property += int(node.get("pid") is not None or node.get("p1") is not None)
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
