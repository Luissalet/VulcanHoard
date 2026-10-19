"""Small untextured GLB from evaluated source triangles; maps stay external.

The workshop binds its own texture assets by material name. UV coordinates retain
Blender's bottom-left origin because these materials have no embedded glTF image.
"""

import json
import struct
import numpy as np


def preview_glb(inventory):
    doc = {
        "asset": {"version": "2.0", "generator": "Sculptor’s Hoard"},
        "scene": 0,
        "scenes": [{"nodes": []}],
        "nodes": [],
        "meshes": [],
        "materials": [],
        "bufferViews": [],
        "accessors": [],
    }
    binary = bytearray()

    def attribute(values, kind):
        values = np.asarray(values, dtype="<f4")
        raw = values.tobytes()
        view = len(doc["bufferViews"])
        doc["bufferViews"].append(
            {"buffer": 0, "byteOffset": len(binary), "byteLength": len(raw), "target": 34962}
        )
        binary.extend(raw)
        index = len(doc["accessors"])
        doc["accessors"].append(
            {
                "bufferView": view,
                "componentType": 5126,
                "count": len(values),
                "type": kind,
                "min": values.min(axis=0).tolist(),
                "max": values.max(axis=0).tolist(),
            }
        )
        return index

    for item in inventory:
        xyz = np.asarray(item.get("world_triangles", []), dtype=np.float32)
        uv = np.asarray(item.get("triangles", []), dtype=np.float32)
        if not xyz.size:
            continue
        if xyz.shape != (len(uv), 3, 3) or uv.shape[1:] != (3, 2):
            raise ValueError("Preview geometry and UV triangles do not match.")
        if not np.isfinite(xyz).all() or not np.isfinite(uv).all():
            raise ValueError("Preview geometry contains non-finite coordinates.")
        xyz = xyz[..., [0, 2, 1]].copy()
        xyz[..., 2] *= -1  # Blender Z-up to glTF Y-up; winding is preserved.
        normal = np.cross(xyz[:, 1] - xyz[:, 0], xyz[:, 2] - xyz[:, 0])
        normal /= np.maximum(np.linalg.norm(normal, axis=1, keepdims=True), 1e-12)
        attributes = {
            "POSITION": attribute(xyz.reshape(-1, 3), "VEC3"),
            "NORMAL": attribute(np.repeat(normal, 3, axis=0), "VEC3"),
            "TEXCOORD_0": attribute(uv.reshape(-1, 2), "VEC2"),
        }
        index = len(doc["meshes"])
        doc["materials"].append(
            {
                "name": item["material"],
                "doubleSided": True,
                "pbrMetallicRoughness": {
                    "baseColorFactor": [1, 1, 1, 1],
                    "metallicFactor": 0,
                    "roughnessFactor": 0.8,
                },
            }
        )
        doc["meshes"].append(
            {
                "name": item["material"],
                "primitives": [{"attributes": attributes, "material": index}],
            }
        )
        doc["nodes"].append({"name": item["material"], "mesh": index})
        doc["scenes"][0]["nodes"].append(index)
    if not doc["meshes"]:
        return None
    doc["buffers"] = [{"byteLength": len(binary)}]
    header = json.dumps(doc, separators=(",", ":")).encode("utf-8")
    header += b" " * (-len(header) % 4)
    binary.extend(b"\0" * (-len(binary) % 4))
    return (
        struct.pack("<4sII", b"glTF", 2, 28 + len(header) + len(binary))
        + struct.pack("<I4s", len(header), b"JSON")
        + header
        + struct.pack("<I4s", len(binary), b"BIN\0")
        + binary
    )
