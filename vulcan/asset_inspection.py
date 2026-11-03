"""Inspect a 3D asset against a destination profile, and prepare a variant that satisfies it.

Three separate things, never mixed:

* **Measurements** (``inspect_file``): what the file actually contains, each value tagged ``observed`` (read from the
  file), ``not_available`` (the format cannot carry it: an STL has no UVs, materials or units) or ``not_measured`` (the
  format can carry it but this version does not read it). Nothing is guessed: an assumed unit says it is assumed.
* **Report** (``evaluate`` / ``report``): a DRAFT. Every rule of the profile becomes a check linked to the measurement it
  used (``pass``, ``fail`` or ``not_evaluated``); failed checks are DEFECTS; RECOMMENDATIONS say what could fix them,
  whether the app can do it automatically and which attributes that operation would lose; the PLAN lists the
  operations a preparation would run, the losses it declares up front and the defects nothing automatic fixes.
* **Preparation** (``prepare``): the OPERATION. It writes a NEW file (the original is hashed before and after and must
  be byte-identical), re-imports it from disk with a fresh loader, re-checks every rule on that re-import, measures
  the deviation from the original with a sampled two-way surface distance (an estimate from N samples, not a bound)
  and stores a receipt: recipe, SHA-256 of source and output, measurements before/after, effective changes, losses
  declared and observed. A budget that is not met is an explained failure, never "success with fewer triangles".

Inspection is pure CPU (trimesh + numpy + the file's own glTF JSON): it never starts a subprocess, GPU or Blender. The
preparation reuses the LOD engines of ``simplify.simplify_mesh`` and its ``measure_error``.
"""

from __future__ import annotations

import base64
import hashlib
import io
import json
import re
import struct
import time
import uuid
from pathlib import Path

import numpy as np

from . import asset_3mf, asset_targets, asset_walls
from .asset_texts import _, language, show
from .hoard_link import atomic

FORMATS = {".stl": "stl", ".obj": "obj", ".3mf": "3mf", ".glb": "glb", ".gltf": "gltf"}
UNIT_TO_MM = {"mm": 1.0, "cm": 10.0, "m": 1000.0, "in": 25.4}
TRIMESH_UNITS = {"millimeter": "mm", "millimeters": "mm", "mm": "mm", "centimeter": "cm", "centimeters": "cm", "cm": "cm",
                 "meter": "m", "meters": "m", "m": "m", "inch": "in", "inches": "in", "in": "in"}
DEVIATION_SAMPLES = 1_500  # surface samples per side (plus up to 30k vertices per side), see simplify.measure_error
ATTRIBUTE_KEYS = ("attributes.uvs", "attributes.vertex_colors", "attributes.materials", "attributes.textures", "attributes.animations",
                  "attributes.scene_nodes", "attributes.normals")


def label(key: str) -> str:
    """Human name of an attribute measurement in the current language."""
    return _("l." + key)


# ---------- measurement records ----------
def observed(value, source: str) -> dict:
    return {"value": value, "status": "observed", "source": source}


def not_available(reason: str) -> dict:
    return {"value": None, "status": "not_available", "source": reason}


def not_measured(reason: str) -> dict:
    return {"value": None, "status": "not_measured", "source": reason}


def sha256_of(path: Path) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for block in iter(lambda: handle.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def format_of(path: Path) -> str:
    kind = FORMATS.get(Path(path).suffix.lower())
    if kind is None:
        raise ValueError(_("e.format", ext=Path(path).suffix, formats=", ".join(sorted(FORMATS))))
    return kind


# ---------- glTF / OBJ side readers (the file's own declarations, no trimesh) ----------
def read_gltf(path: Path) -> tuple[dict, bytes]:
    """(glTF JSON, BIN chunk) of a .glb, or (JSON, b'') of a .gltf."""
    data = Path(path).read_bytes()
    if Path(path).suffix.lower() == ".gltf":
        return json.loads(data.decode("utf-8-sig")), b""
    if len(data) < 20 or data[:4] != b"glTF":
        raise ValueError(_("e.not_glb"))
    offset, document, binary = 12, None, b""
    while offset + 8 <= len(data):
        length, kind = struct.unpack_from("<I4s", data, offset)
        chunk = data[offset + 8: offset + 8 + length]
        if kind == b"JSON":
            document = json.loads(chunk.decode("utf-8"))
        elif kind == b"BIN\x00":
            binary = chunk
        offset += 8 + length
    if document is None:
        raise ValueError(_("e.no_json"))
    return document, binary


def _image_bytes(document: dict, binary: bytes, image: dict, base: Path) -> bytes | None:
    if "bufferView" in image:
        view = document["bufferViews"][image["bufferView"]]
        start = view.get("byteOffset", 0)
        return binary[start:start + view["byteLength"]] if binary else None
    uri = image.get("uri") or ""
    if uri.startswith("data:"):
        return base64.b64decode(uri.split(",", 1)[1])
    file = base / uri
    return file.read_bytes() if uri and file.is_file() else None


def _image_size(blob: bytes | None) -> list[int] | None:
    if not blob:
        return None
    from PIL import Image

    try:
        with Image.open(io.BytesIO(blob)) as image:
            return [int(image.size[0]), int(image.size[1])]
    except Exception:  # noqa: BLE001 - undecodable image: reported as unknown size
        return None


def gltf_facts(path: Path) -> dict:
    """What the glTF file declares: triangles from its accessors (per node instance), attributes per primitive,
    materials, textures with pixel sizes, animations, skins, nodes, extensions, external resource bytes."""
    document, binary = read_gltf(path)
    accessors = document.get("accessors", [])
    meshes = document.get("meshes", [])
    mesh_tris, primitives, with_attr = [], 0, {"TEXCOORD_0": 0, "NORMAL": 0, "COLOR_0": 0, "TANGENT": 0}
    for mesh in meshes:
        total = 0
        for primitive in mesh.get("primitives", []):
            primitives += 1
            attrs = primitive.get("attributes", {})
            for key in with_attr:
                with_attr[key] += int(key in attrs)
            if primitive.get("mode", 4) != 4:
                continue
            source = primitive.get("indices", attrs.get("POSITION"))
            total += int(accessors[source]["count"]) // 3 if source is not None else 0
        mesh_tris.append(total)
    nodes = document.get("nodes", [])
    instanced = [n["mesh"] for n in nodes if "mesh" in n]
    triangles = sum(mesh_tris[i] for i in instanced) if instanced else sum(mesh_tris)
    base = Path(path).parent
    sizes = [_image_size(_image_bytes(document, binary, image, base)) for image in document.get("images", [])]
    resource_bytes = 0
    for item in document.get("buffers", []) + document.get("images", []):
        uri = item.get("uri") or ""
        if uri and not uri.startswith("data:") and (base / uri).is_file():
            resource_bytes += (base / uri).stat().st_size
    return {"triangles": triangles, "primitives": primitives, "with_attr": with_attr, "materials": len(document.get("materials", [])),
            "textures": len(document.get("textures", [])), "images": len(document.get("images", [])), "image_sizes": sizes,
            "animations": len(document.get("animations", [])), "skins": len(document.get("skins", [])), "nodes": len(nodes),
            "extensions_used": document.get("extensionsUsed", []), "extensions_required": document.get("extensionsRequired", []),
            "resource_bytes": resource_bytes}


def obj_facts(path: Path) -> dict:
    """Counts straight from the OBJ text (vt, vn, colour components on v lines, usemtl) and its MTL texture maps."""
    uv = normals = colored = usemtl = 0
    libraries: list[str] = []
    with Path(path).open("r", encoding="utf-8", errors="replace") as handle:
        for line in handle:
            if line.startswith("vt "):
                uv += 1
            elif line.startswith("vn "):
                normals += 1
            elif line.startswith("v "):
                colored += int(len(line.split()) >= 7)
            elif line.startswith("usemtl "):
                usemtl += 1
            elif line.startswith("mtllib "):
                libraries.append(line.split(None, 1)[1].strip())
    base, materials, maps, resource_bytes = Path(path).parent, 0, [], 0
    for name in libraries:
        lib = base / name
        if not lib.is_file():
            continue
        resource_bytes += lib.stat().st_size
        for line in lib.read_text(encoding="utf-8", errors="replace").splitlines():
            parts = line.strip().split()
            if parts and parts[0] == "newmtl":
                materials += 1
            elif parts and parts[0].lower().startswith("map_") and len(parts) > 1:
                maps.append(base / parts[-1])
    sizes = []
    for image in dict.fromkeys(maps):
        if image.is_file():
            resource_bytes += image.stat().st_size
            sizes.append(_image_size(image.read_bytes()))
        else:
            sizes.append(None)
    return {"uv": uv, "normals": normals, "colored_vertices": colored, "usemtl": usemtl, "materials": materials,
            "textures": len(sizes), "image_sizes": sizes, "libraries": libraries, "resource_bytes": resource_bytes}


# ---------- geometry ----------
def load_geometry(path: Path):
    """(raw meshes in world space, welded positions-only trimesh, declared unit or None) from a fresh load of the file."""
    import trimesh

    kind = format_of(path)
    loaded = trimesh.load(str(path), file_type=kind, process=False)
    if isinstance(loaded, trimesh.Scene):
        meshes = [g for g in loaded.dump() if isinstance(g, trimesh.Trimesh) and len(g.faces)]
    else:
        meshes = [loaded] if isinstance(loaded, trimesh.Trimesh) and len(loaded.faces) else []
    if not meshes:
        raise ValueError(_("e.no_triangles"))
    offset, vertices, faces = 0, [], []
    for mesh in meshes:
        vertices.append(np.asarray(mesh.vertices, dtype=np.float64))
        faces.append(np.asarray(mesh.faces, dtype=np.int64) + offset)
        offset += len(mesh.vertices)
    welded = trimesh.Trimesh(vertices=np.vstack(vertices), faces=np.vstack(faces), process=True, validate=False)
    declared = "m" if kind in ("glb", "gltf") else TRIMESH_UNITS.get(str(getattr(loaded, "units", None) or "").lower())
    return meshes, welded, declared, loaded


def _edge_counts(faces: np.ndarray) -> tuple[int, int]:
    edges = np.sort(np.asarray(faces)[:, [0, 1, 1, 2, 2, 0]].reshape(-1, 2), axis=1)
    _unused, counts = np.unique(edges, axis=0, return_counts=True)
    return int((counts == 1).sum()), int((counts > 2).sum())


def welded_mm(path: Path):
    """The welded geometry of a file in millimetres (declared unit, or mm assumed), for deviation measurements."""
    _meshes, welded, declared, _loaded = load_geometry(path)
    mesh = welded.copy()
    mesh.apply_scale(UNIT_TO_MM[declared or "mm"])
    return mesh


def inspect_file(path: Path, max_mb: int | None = None, lang: str | None = None, with_walls: bool = False) -> dict:
    """Measurements of one file. Returns {file, measurements{id: record}, locations, notes}. Never writes, never spawns.
    `with_walls` also samples the wall thickness (a profile with min_wall_mm asks for it; it costs a ray index)."""
    with language(lang):
        return _inspect_file(Path(path), max_mb, with_walls)


def _wall_records(welded, to_mm: float, open_edges: int, nonmanifold: int) -> tuple[dict, dict, dict]:
    """(geometry.wall_thickness record, geometry.min_wall_mm record, raw result) for a welded mesh."""
    if open_edges or nonmanifold:
        result = {"status": "open"}
    else:
        result = asset_walls.wall_thickness(welded, to_mm)
    status = result["status"]
    if status == "ok":
        summary = {k: result[k] for k in ("min_mm", "p5_mm", "median_mm", "samples", "samples_requested", "rays_without_hit", "seed", "weighting")}
        summary.update(method="inward_ray_sample", basis="p5", is_bound=False, worst=result["worst"])
        source = _("m.wall_sampled", n=result["samples"], seed=result["seed"], min=result["min_mm"], p5=result["p5_mm"], med=result["median_mm"])
        return observed(summary, source), observed(result["p5_mm"], source), result
    reason = {"open": _("m.wall_open"), "no_engine": _("m.wall_no_engine"), "too_big": _("m.wall_too_big", n=result.get("triangles"), limit=result.get("limit")),
              "no_samples": _("m.wall_no_samples"), "engine_error": _("m.wall_engine_error", error=result.get("error"))}[status]
    return not_measured(reason), not_measured(reason), result


def _inspect_file(path: Path, max_mb: int | None, with_walls: bool = False) -> dict:
    if not path.is_file():
        raise LookupError(_("e.file_missing", path=path))
    kind = format_of(path)
    size = path.stat().st_size
    if max_mb and size > max_mb * 1024 * 1024:
        raise ValueError(_("e.too_big", mb=f"{size / 1048576:.0f}", limit=max_mb))
    started = time.perf_counter()
    meshes, welded, declared, loaded = load_geometry(path)
    m: dict[str, dict] = {}
    triangles = int(len(welded.faces))
    m["geometry.triangles"] = observed(triangles, _("m.triangles"))
    m["geometry.vertices_stored"] = observed(int(sum(len(x.vertices) for x in meshes)), _("m.vertices_stored"))
    m["geometry.vertices_welded"] = observed(int(len(welded.vertices)), _("m.vertices_welded"))
    open_edges, nonmanifold = _edge_counts(welded.faces)
    m["geometry.open_edges"] = observed(open_edges, _("m.open_edges"))
    m["geometry.nonmanifold_edges"] = observed(nonmanifold, _("m.nonmanifold_edges"))
    m["geometry.watertight"] = observed(bool(open_edges == 0 and nonmanifold == 0), _("m.watertight"))
    m["geometry.winding_consistent"] = observed(bool(welded.is_winding_consistent), _("m.winding"))
    try:
        bodies = int(welded.body_count)
    except Exception:  # noqa: BLE001 - graph library missing
        bodies = None
    m["geometry.bodies"] = observed(bodies, _("m.bodies")) if bodies else not_measured(_("m.bodies_unavailable"))
    areas = welded.area_faces
    m["geometry.degenerate_triangles"] = observed(int((areas <= 1e-12).sum()), _("m.degenerate"))
    unit = declared or "mm"
    extents = [round(float(x), 6) for x in welded.extents]
    m["geometry.bbox"] = observed(extents, _("m.bbox", unit=unit))
    m["geometry.bbox_mm"] = observed([round(x * UNIT_TO_MM[unit], 4) for x in extents], _("m.bbox_mm_assumed" if declared is None else "m.bbox_mm", unit=unit))
    m["geometry.volume_mm3"] = (observed(round(abs(float(welded.volume)) * UNIT_TO_MM[unit] ** 3, 4), _("m.volume"))
                                if open_edges == 0 and nonmanifold == 0 else not_measured(_("m.volume_open")))
    locations = asset_walls.locate_defects(welded, UNIT_TO_MM[unit])
    locations.update(unit=unit, unit_to_mm=UNIT_TO_MM[unit], thin_regions=[])
    if with_walls:
        m["geometry.wall_thickness"], m["geometry.min_wall_mm"], wall = _wall_records(welded, UNIT_TO_MM[unit], open_edges, nonmanifold)
        if wall["status"] == "ok":
            locations["thin_regions"] = [{k: w[k] for k in ("face", "thickness_mm", "centroid_mm")} for w in wall["worst"]]
            locations["overlay"]["points"] = [{"label": f"{w['thickness_mm']} mm", "position": w["centroid"]} for w in wall["worst"]]
    from .geometry import guess_units

    m["units.declared"] = (observed("m", _("m.unit_gltf")) if kind in ("glb", "gltf") else
                           observed(declared, _("m.unit_3mf")) if declared else
                           not_available(_("m.unit_none_format", fmt=kind.upper())) if kind in ("stl", "obj") else
                           observed(None, _("m.unit_none_file")))
    m["units.effective"] = observed(unit, _("m.unit_declared") if declared else _("m.unit_assumed"))
    m["units.assumed"] = observed(declared is None, _("m.assumed_flag"))
    m["units.size_hint"] = observed(guess_units(np.asarray(m["geometry.bbox_mm"]["value"])), _("m.size_hint"))

    m["file.format"] = observed(kind, _("m.format"))
    m["file.bytes"] = observed(size, _("m.bytes"))
    notes: list[str] = []
    resource_bytes = 0
    if kind == "stl":
        for key in ("uvs", "vertex_colors", "materials", "textures", "animations"):
            m[f"attributes.{key}"] = not_available(_("m.stl_cannot", what=_("w." + key)))
        m["attributes.texture_max_px"] = not_available(_("m.stl_cannot", what=_("w.textures")))
        m["attributes.normals"] = observed("facet", _("m.stl_normals"))
        m["attributes.scene_nodes"] = not_available(_("m.stl_scene"))
    elif kind == "obj":
        facts = obj_facts(path)
        resource_bytes = facts["resource_bytes"]
        m["attributes.uvs"] = observed(facts["uv"] > 0, _("m.obj_vt", n=facts["uv"]))
        m["attributes.normals"] = observed("vertex" if facts["normals"] else None, _("m.obj_vn", n=facts["normals"]))
        m["attributes.vertex_colors"] = observed(facts["colored_vertices"] > 0, _("m.obj_colors", n=facts["colored_vertices"]))
        m["attributes.materials"] = observed(facts["materials"], _("m.obj_materials", libs=", ".join(facts["libraries"]) or _("m.obj_no_mtl")))
        m["attributes.textures"] = observed(facts["textures"], _("m.obj_maps"))
        m["attributes.texture_sizes"] = observed(facts["image_sizes"], _("m.obj_sizes"))
        m["attributes.animations"] = not_available(_("m.obj_animations"))
        m["attributes.scene_nodes"] = not_available(_("m.obj_scene"))
    elif kind in ("glb", "gltf"):
        facts = gltf_facts(path)
        resource_bytes = facts["resource_bytes"]
        prims = facts["primitives"] or 1
        m["geometry.triangles_declared"] = observed(facts["triangles"], _("m.gltf_tris"))
        m["attributes.uvs"] = observed(facts["with_attr"]["TEXCOORD_0"] > 0, _("m.gltf_attr", attr="TEXCOORD_0", n=facts["with_attr"]["TEXCOORD_0"], total=prims))
        m["attributes.normals"] = observed("vertex" if facts["with_attr"]["NORMAL"] else None, _("m.gltf_attr", attr="NORMAL", n=facts["with_attr"]["NORMAL"], total=prims))
        m["attributes.vertex_colors"] = observed(facts["with_attr"]["COLOR_0"] > 0, _("m.gltf_attr", attr="COLOR_0", n=facts["with_attr"]["COLOR_0"], total=prims))
        m["attributes.materials"] = observed(facts["materials"], _("m.gltf_materials"))
        m["attributes.textures"] = observed(facts["images"], _("m.gltf_images"))
        m["attributes.texture_sizes"] = observed(facts["image_sizes"], _("m.gltf_sizes"))
        m["attributes.animations"] = observed(facts["animations"], _("m.gltf_animations"))
        m["attributes.skins"] = observed(facts["skins"], _("m.gltf_skins"))
        m["attributes.scene_nodes"] = observed(facts["nodes"], _("m.gltf_nodes"))
        m["file.extensions_required"] = observed(facts["extensions_required"], _("m.gltf_required"))
        if facts["triangles"] != triangles:
            notes.append(_("m.gltf_mismatch", a=triangles, b=facts["triangles"]))
    else:  # 3mf
        try:
            facts = asset_3mf.read_3mf_materials(path)
        except ValueError as error:
            for key in ("uvs", "vertex_colors", "materials", "textures"):
                m[f"attributes.{key}"] = not_measured(_("m.3mf_unreadable", error=error))
        else:
            sizes_3mf = [t["px"] for t in facts["textures"]]
            m["attributes.materials"] = observed(len(facts["base_materials"]), _("m.3mf_base", n=len(facts["base_materials"])))
            m["attributes.vertex_colors"] = observed(facts["colours_total"] > 0, _("m.3mf_colours", n=facts["colours_total"], groups=facts["colour_groups"]))
            m["attributes.textures"] = observed(len(facts["textures"]), _("m.3mf_textures"))
            m["attributes.texture_sizes"] = observed(sizes_3mf, _("m.gltf_sizes"))
            m["attributes.uvs"] = observed(facts["uv_coordinates"] > 0, _("m.3mf_uvs", n=facts["uv_coordinates"], groups=facts["texture_groups"]))
            m["attributes.colors_3mf"] = observed(
                {"base_materials": facts["base_materials"], "colours": facts["colours"], "colours_total": facts["colours_total"],
                 "textures": facts["textures"], "triangles": facts["triangles"], "triangles_with_property": facts["triangles_with_property"],
                 "objects_with_property": facts["objects_with_property"]}, _("m.3mf_declared"))
        m["attributes.animations"] = not_available(_("m.3mf_animations"))
        m["attributes.normals"] = not_available(_("m.3mf_normals"))
        m["attributes.scene_nodes"] = observed(len(meshes), _("m.3mf_items"))
    sizes = (m.get("attributes.texture_sizes") or {}).get("value")
    if "attributes.texture_max_px" not in m:
        m["attributes.texture_max_px"] = (observed(max((max(s) for s in sizes if s), default=0), _("m.texture_max"))
                                          if sizes is not None else not_measured(_("m.texture_sizes_unread")))
    m["file.resource_bytes"] = observed(resource_bytes, _("m.resource_bytes"))
    m["file.total_bytes"] = observed(size + resource_bytes, _("m.total_bytes"))
    return {"file": {"path": str(path), "format": kind, "bytes": size, "sha256": sha256_of(path)}, "measurements": m, "locations": locations, "notes": notes,
            "seconds": round(time.perf_counter() - started, 3), "method": _("method")}


# ---------- report (draft) ----------
def _value(m: dict, key: str):
    record = m.get(key)
    return record["value"] if record and record["status"] == "observed" else None


def _textured(m: dict) -> bool | None:
    textures = m.get("attributes.textures")
    if not textures or textures["status"] != "observed":
        return None
    return bool(textures["value"])


def evaluate(profile: dict, m: dict) -> list[dict]:
    """One check per rule of the profile: pass / fail / not_evaluated, each linked to its measurement."""
    rules = profile["rules"]
    checks: list[dict] = []

    def check(rule, limit, key, ok, observed_value=None, reason=""):
        record = m.get(key) or not_measured(_("m.no_such"))
        if ok is None:
            status = "not_evaluated"
            reason = reason or _("r.status", what=key, status=_("st." + record["status"]), source=record["source"])
        else:
            status = "pass" if ok else "fail"
            if record["status"] == "not_available" and not reason:
                reason = _("r.not_in_format", fmt=(_value(m, "file.format") or "").upper() or _("r.this_format"))
        checks.append({"rule": rule, "limit": limit, "measurement": key,
                       "observed": record["value"] if observed_value is None else observed_value, "status": status, "reason": reason})

    def cmp(value, test):
        return None if value is None else bool(test(value))

    if rules.get("accepted_formats"):
        check("accepted_formats", rules["accepted_formats"], "file.format", _value(m, "file.format") in rules["accepted_formats"])
    if rules.get("max_triangles") is not None:
        check("max_triangles", rules["max_triangles"], "geometry.triangles", cmp(_value(m, "geometry.triangles"), lambda v: v <= rules["max_triangles"]))
    if rules.get("max_file_bytes") is not None:
        check("max_file_bytes", rules["max_file_bytes"], "file.total_bytes", cmp(_value(m, "file.total_bytes"), lambda v: v <= rules["max_file_bytes"]))
    if rules.get("max_textures") is not None:
        check("max_textures", rules["max_textures"], "attributes.textures",
              None if m["attributes.textures"]["status"] == "not_measured" else (_value(m, "attributes.textures") or 0) <= rules["max_textures"])
    if rules.get("max_texture_px") is not None:
        record = m["attributes.texture_max_px"]
        ok = None if record["status"] == "not_measured" else (record["value"] or 0) <= rules["max_texture_px"]
        check("max_texture_px", rules["max_texture_px"], "attributes.texture_max_px", ok)
    if rules.get("require_uvs_if_textured"):
        textured = _textured(m)
        if textured is None and m["attributes.textures"]["status"] == "not_available":
            textured = False
        uvs = m["attributes.uvs"]
        ok = True if textured is False else (None if textured is None or uvs["status"] != "observed" else bool(uvs["value"]))
        check("require_uvs_if_textured", True, "attributes.uvs", ok, reason="" if ok is not None else _("r.uv_unknown"))
    if rules.get("max_bodies") is not None:
        check("max_bodies", rules["max_bodies"], "geometry.bodies", cmp(_value(m, "geometry.bodies"), lambda v: v <= rules["max_bodies"]))
    if rules.get("require_watertight"):
        check("require_watertight", True, "geometry.watertight", _value(m, "geometry.watertight"),
              observed_value={"open_edges": _value(m, "geometry.open_edges"), "nonmanifold_edges": _value(m, "geometry.nonmanifold_edges")})
    if rules.get("max_extent_mm"):
        bbox = _value(m, "geometry.bbox_mm")
        check("max_extent_mm", rules["max_extent_mm"], "geometry.bbox_mm",
              None if bbox is None else all(a <= b + 1e-9 for a, b in zip(sorted(bbox), sorted(rules["max_extent_mm"]))))
    if rules.get("units"):
        effective = _value(m, "units.effective")
        assumed = _value(m, "units.assumed")
        if assumed:
            check("units", rules["units"], "units.effective", None, reason=_("r.unit_assumed", unit=effective))
        else:
            check("units", rules["units"], "units.effective", effective == rules["units"])
    if rules.get("min_wall_mm") is not None:
        p5 = _value(m, "geometry.min_wall_mm")
        detail = _value(m, "geometry.wall_thickness")
        if p5 is None:
            check("min_wall_mm", rules["min_wall_mm"], "geometry.min_wall_mm", None)
        else:
            ok = p5 >= rules["min_wall_mm"] - 1e-9
            note = _("r.wall_basis", n=detail["samples"], p5=p5, min=detail["min_mm"], med=detail["median_mm"])
            if ok and detail["min_mm"] < rules["min_wall_mm"] - 1e-9:
                note += _("r.wall_min_below", min=detail["min_mm"])
            check("min_wall_mm", rules["min_wall_mm"], "geometry.min_wall_mm", ok, observed_value=p5, reason=note)
    return checks


def _losses_of_rebuild(m: dict) -> list[dict]:
    """Attributes present in the source that a positions-only rebuild (simplification) drops."""
    losses = []
    for key in ("attributes.uvs", "attributes.vertex_colors", "attributes.materials", "attributes.textures", "attributes.animations"):
        record = m.get(key)
        if record and record["status"] == "observed" and record["value"]:
            losses.append({"measurement": key, "label": label(key), "before": record["value"]})
    record = m.get("attributes.normals")
    if record and record["status"] == "observed" and record["value"] == "vertex":
        losses.append({"measurement": "attributes.normals", "label": label("attributes.normals"), "before": "vertex",
                       "note": _("ln.recomputed")})
    nodes = m.get("attributes.scene_nodes")
    if nodes and nodes["status"] == "observed" and isinstance(nodes["value"], int) and nodes["value"] > 1:
        losses.append({"measurement": "attributes.scene_nodes", "label": label("attributes.scene_nodes"), "before": nodes["value"],
                       "note": _("ln.merged")})
    for key in ("attributes.materials", "attributes.textures", "attributes.uvs", "attributes.vertex_colors"):
        if m.get(key, {}).get("status") == "not_measured":
            losses.append({"measurement": key, "label": label(key), "before": None, "note": _("ln.unmeasured")})
    return losses


def estimate_bytes(output_format: str, triangles: int, vertices: int, texture_bytes: int = 0) -> int:
    """Rough size of a plain export (positions float32 + uint32 indices for GLB; 50 bytes per triangle for binary STL)."""
    if output_format == "stl":
        return 84 + 50 * triangles
    return 2048 + 12 * vertices + 12 * triangles + texture_bytes


def plan_for(profile: dict, m: dict, checks: list[dict]) -> dict:
    """The draft plan: defects, recommendations (automatic or not, with losses) and the operations a preparation runs."""
    rules, output = profile["rules"], profile["output_format"]
    failed = {c["rule"]: c for c in checks if c["status"] == "fail"}
    defects = [{"id": f"D{i + 1}", "rule": c["rule"], "measurement": c["measurement"], "observed": c["observed"], "limit": c["limit"],
                "message": _defect_message(c)} for i, c in enumerate(failed.values())]
    by_rule = {d["rule"]: d["id"] for d in defects}
    recommendations, operations, blockers = [], [], []
    triangles = _value(m, "geometry.triangles") or 0
    vertices = _value(m, "geometry.vertices_welded") or 0
    source_format = _value(m, "file.format")
    target_tris = rules.get("max_triangles")
    textured = bool(_textured(m))
    if rules.get("max_file_bytes"):
        predicted = estimate_bytes(output, min(triangles, target_tris or triangles), vertices if not target_tris or triangles <= target_tris
                                   else int(vertices * target_tris / max(triangles, 1)), _value(m, "file.resource_bytes") or 0)
        if predicted > rules["max_file_bytes"] or "max_file_bytes" in failed:
            per_tri = 50 if output == "stl" else 18
            by_bytes = max(4, int((rules["max_file_bytes"] - 2048 - (_value(m, "file.resource_bytes") or 0)) / per_tri))
            if by_bytes < (target_tris or triangles):
                target_tris = by_bytes
    needs_simplify = bool(target_tris and triangles > target_tris)

    def recommend(defect_rule, operation, automatic, text, loses=None):
        recommendations.append({"id": f"R{len(recommendations) + 1}", "for_defect": by_rule.get(defect_rule), "operation": operation,
                                "automatic": automatic, "text": text, "loses": loses or []})

    losses: list[dict] = []
    if needs_simplify:
        losses = _losses_of_rebuild(m)
        ratio = target_tris / triangles
        operations.append({"op": "simplify", "target_triangles": int(target_tris), "ratio": round(ratio, 6), "mode": "attribute",
                           "keep_borders": True, "why": "max_triangles" if target_tris == rules.get("max_triangles") else _("why.bytes_estimate")})
        recommend("max_triangles" if "max_triangles" in failed else "max_file_bytes", "simplify", True,
                  _("rec.simplify", a=triangles, b=int(target_tris)),
                  [x["label"] for x in losses])
    if "max_texture_px" in failed:
        if needs_simplify:
            recommend("max_texture_px", "resize_textures", False, _("rec.textures_dropped"))
        else:
            operations.append({"op": "resize_textures", "max_px": rules["max_texture_px"], "why": "max_texture_px"})
            recommend("max_texture_px", "resize_textures", True, _("rec.resize", px=rules["max_texture_px"]),
                      [_("l.texture_resolution")])
            losses.append({"measurement": "attributes.texture_max_px", "label": _("l.texture_resolution"), "before": _value(m, "attributes.texture_max_px")})
    unit_from = _value(m, "units.effective") or "mm"
    unit_to = "m" if output == "glb" else rules.get("units", "mm")
    if unit_from != unit_to:
        operations.append({"op": "scale", "factor": UNIT_TO_MM[unit_from] / UNIT_TO_MM[unit_to], "from": unit_from, "to": unit_to,
                           "why": _("why.gltf_metres") if output == "glb" else _("why.profile_units")})
    if needs_simplify or source_format != output or operations:
        operations.append({"op": "export", "format": output, "writer": "trimesh", "preserve": _("pres.geometry") if needs_simplify else _("pres.visuals")})
    if "accepted_formats" in failed and output not in rules["accepted_formats"]:
        blockers.append({"defect": by_rule["accepted_formats"], "reason": _("blk.format", fmt=output.upper())})
    elif "accepted_formats" in failed:
        recommend("accepted_formats", "convert", True, _("rec.convert", fmt=output.upper()))
    if "max_textures" in failed:
        if needs_simplify:
            recommend("max_textures", "drop_textures", True, _("rec.drop_textures"))
        else:
            blockers.append({"defect": by_rule["max_textures"], "reason": _("blk.atlas")})
            recommend("max_textures", "atlas", False, _("rec.atlas"))
    if "require_uvs_if_textured" in failed:
        if needs_simplify:
            recommend("require_uvs_if_textured", "drop_textures", True, _("rec.uv_dropped"))
        else:
            blockers.append({"defect": by_rule["require_uvs_if_textured"], "reason": _("blk.uv")})
            recommend("require_uvs_if_textured", "unwrap", False, _("rec.unwrap"))
    if "max_bodies" in failed:
        blockers.append({"defect": by_rule["max_bodies"], "reason": _("blk.bodies")})
        recommend("max_bodies", "union_or_split", False, _("rec.bodies"))
    if "require_watertight" in failed:
        blockers.append({"defect": by_rule["require_watertight"], "reason": _("blk.watertight")})
        recommend("require_watertight", "repair", False, _("rec.repair"))
    if "max_extent_mm" in failed:
        blockers.append({"defect": by_rule["max_extent_mm"], "reason": _("blk.extent")})
        recommend("max_extent_mm", "scale_or_split", False, _("rec.extent"))
    if "min_wall_mm" in failed:
        blockers.append({"defect": by_rule["min_wall_mm"], "reason": _("blk.wall")})
        recommend("min_wall_mm", "thicken", False, _("rec.wall"))
    if "units" in failed:
        recommend("units", "scale", True, _("rec.rescale", a=unit_from, b=unit_to))
    if _value(m, "units.assumed"):
        recommend(None, "verify_units", False, _("rec.verify_units", unit=unit_from, hint=_value(m, "units.size_hint")))
    if textured and needs_simplify:
        recommend(None, "preserve_attributes", False, _("rec.preserve"))
    for c in checks:
        if c["status"] == "not_evaluated" and not (c["rule"] == "units" and _value(m, "units.assumed")):
            recommend(None, "check_manually", False, _("rec.manual", rule=c["rule"], reason=c["reason"]))
    if not failed:  # nothing to fix: a preparation would only rewrite the same content
        operations, losses, needs_simplify = [], [], False
    return {"defects": defects, "recommendations": recommendations, "operations": operations, "declared_losses": losses,
            "blockers": blockers, "target_triangles": int(target_tris) if needs_simplify else None}


def _defect_message(c: dict) -> str:
    rule, observed_value, limit = c["rule"], c["observed"], c["limit"]
    if rule == "accepted_formats":
        observed_value, limit = str(observed_value).upper(), [str(x).upper() for x in limit]
    key = "d." + rule
    if key not in {"d.max_triangles", "d.max_file_bytes", "d.max_textures", "d.max_texture_px", "d.require_uvs_if_textured", "d.max_bodies",
                   "d.require_watertight", "d.max_extent_mm", "d.units", "d.accepted_formats", "d.min_wall_mm"}:
        return _("d.other", rule=rule, obs=show(observed_value), lim=show(limit))
    return _(key, obs=show(observed_value), lim=show(limit))


def summarize(checks: list[dict]) -> dict:
    statuses = [c["status"] for c in checks]
    return {"pass": statuses.count("pass"), "fail": statuses.count("fail"), "not_evaluated": statuses.count("not_evaluated"),
            "compliant": "fail" not in statuses, "fully_verified": "fail" not in statuses and "not_evaluated" not in statuses}


def report(profile: dict, inspection: dict, lang: str | None = None) -> dict:
    with language(lang):
        return _report(profile, inspection)


def _report(profile: dict, inspection: dict) -> dict:
    checks = evaluate(profile, inspection["measurements"])
    plan = plan_for(profile, inspection["measurements"], checks)
    summary = summarize(checks)
    verdict = (_("v.meets") + ("" if summary["fully_verified"] else _("v.not_eval", n=summary["not_evaluated"]))
               if summary["compliant"] else _("v.defects", n=summary["fail"]))
    return {"kind": "draft", "profile": {k: profile[k] for k in ("id", "name", "purpose", "output_format", "rules")}, "file": inspection["file"],
            "summary": {**summary, "verdict": verdict}, "checks": checks, **plan, "measurements": inspection["measurements"],
            "locations": inspection.get("locations"), "notes": inspection["notes"], "method": inspection["method"],
            "next": _("next")}


# ---------- library glue ----------
def resolve_source(services, model_id: int | None = None, path: str | None = None) -> tuple[Path, dict | None]:
    if model_id:
        model = services.models.get(int(model_id))
        if model is None:
            raise LookupError(_("e.model_missing", id=model_id))
        file = Path(model["path"])
        if not file.is_file():
            raise LookupError(_("e.model_file_missing", id=model_id, path=file))
        return file, model
    text = (path or "").strip().strip('"')
    if not text:
        raise ValueError(_("e.no_source"))
    file = Path(text).expanduser()
    if not file.is_absolute():
        raise ValueError(_("e.relative"))
    format_of(file)
    if not file.is_file():
        raise LookupError(_("e.file_missing", path=file))
    return file.resolve(), services.models.by_path(str(file.resolve()))


def inspect_asset(services, target: str, model_id: int | None = None, path: str | None = None, lang: str | None = None) -> dict:
    """Draft report of one library model or file against a profile. Writes nothing. `lang` = es | en for the texts."""
    with language(lang):
        return _inspect_asset(services, target, model_id, path)


def _wants_walls(profile: dict) -> bool:
    return profile["rules"].get("min_wall_mm") is not None


def _inspect_asset(services, target, model_id, path) -> dict:
    file, model = resolve_source(services, model_id, path)
    profile = asset_targets.get_profile(services.config.data_dir, target)
    out = report(profile, inspect_file(file, services.config.max_file_mb, with_walls=_wants_walls(profile)))
    out["source"] = {"model_id": model["id"] if model else None, "name": model["name"] if model else file.name, "path": str(file)}
    out["previous_preparations"] = [_brief(r) for r in list_preparations(services, sha256=out["file"]["sha256"], limit=5)]
    return out


# ---------- preparation (operation) ----------
def _prepared_dir(services) -> Path:
    return Path(services.config.data_dir) / "prepared"


def _receipt_path(services, prep_id: str) -> Path:
    if not re.fullmatch(r"p[0-9a-f]{12}", prep_id or ""):
        raise LookupError(_("e.prep_unknown"))
    return _prepared_dir(services) / "receipts" / f"{prep_id}.json"


def _resize_textures(scene, max_px: int) -> list[dict]:
    """Downscale every PIL image held by the materials of a trimesh scene to max_px per side. Returns what changed."""
    from PIL import Image

    changed, seen = [], set()
    for geometry in scene.geometry.values():
        material = getattr(getattr(geometry, "visual", None), "material", None)
        if material is None:
            continue
        for attr in ("baseColorTexture", "normalTexture", "metallicRoughnessTexture", "occlusionTexture", "emissiveTexture", "image"):
            image = getattr(material, attr, None)
            if image is None or not hasattr(image, "size") or id(image) in seen:
                continue
            seen.add(id(image))
            if max(image.size) <= max_px:
                continue
            factor = max_px / max(image.size)
            new = image.resize((max(1, int(image.size[0] * factor)), max(1, int(image.size[1] * factor))), Image.LANCZOS)
            setattr(material, attr, new)
            changed.append({"slot": attr, "before_px": list(image.size), "after_px": list(new.size)})
    return changed


def _simplify(welded, target: int) -> tuple[object, dict]:
    """Run the library LOD engine (simplify.simplify_mesh) and tighten the ratio until the count is within the target."""
    from .simplify import _trimesh, engines, simplify_mesh

    total = len(welded.faces)
    ratio, attempts = min(1.0, target / total), []
    for _attempt in range(4):
        out = simplify_mesh(welded, ratio=ratio, mode="attribute", keep_borders=True)
        after = len(out["faces"])
        attempts.append({"ratio": round(ratio, 6), "triangles": int(after), "engine": out["engine"]})
        if after <= target or after == 0:
            break
        ratio = ratio * target / after * 0.98
    return _trimesh(out["vertices"], out["faces"]), {"engine": out["engine"], "engine_default": engines()["default"], "attempts": attempts}


def _effective_changes(before: dict, after: dict) -> list[dict]:
    keys = ("file.format", "file.total_bytes", "geometry.triangles", "geometry.vertices_welded", "geometry.bodies", "geometry.open_edges",
            "geometry.watertight", "geometry.bbox_mm", "units.effective", "attributes.uvs", "attributes.normals", "attributes.vertex_colors",
            "attributes.materials", "attributes.textures", "attributes.texture_max_px", "attributes.animations")
    changes = []
    for key in keys:
        a, b = before.get(key), after.get(key)
        if a is None or b is None:
            continue
        if a["value"] != b["value"] or a["status"] != b["status"]:
            changes.append({"measurement": key, "before": a["value"], "after": b["value"], "status_before": a["status"], "status_after": b["status"]})
    return changes


def _observed_losses(before: dict, after: dict) -> list[dict]:
    losses = []
    for key in ATTRIBUTE_KEYS:
        a, b = before.get(key), after.get(key)
        if not a or a["status"] != "observed" or not a["value"]:
            continue
        if key == "attributes.scene_nodes" and (not isinstance(a["value"], int) or a["value"] <= 1 or (b and b.get("value") == a["value"])):
            continue
        if key == "attributes.normals" and a["value"] != "vertex":  # facet normals are implied by the triangles, not authored data
            continue
        if not b or b["status"] != "observed" or not b["value"] or (key == "attributes.normals" and b["value"] != a["value"]):
            losses.append({"measurement": key, "label": label(key), "before": a["value"], "after": b["value"] if b else None,
                           "status_after": b["status"] if b else "missing"})
    return losses


def _brief(receipt: dict) -> dict:
    return {k: receipt.get(k) for k in ("id", "status", "created_at", "profile_id", "source_model_id", "source_name", "source_sha256",
                                         "output_sha256", "output_path", "variant_model_id", "message")}


def prepare(services, target: str, model_id: int | None = None, path: str | None = None, accept_losses: bool = False,
            max_deviation_mm: float | None = None, lang: str | None = None) -> dict:
    """See _prepare; `lang` = es | en for every text of the receipt (stored in that language)."""
    with language(lang):
        return _prepare(services, target, model_id, path, accept_losses, max_deviation_mm)


def _prepare(services, target: str, model_id: int | None, path: str | None, accept_losses: bool,
             max_deviation_mm: float | None) -> dict:
    """Run the plan of the report as a NEW file and verify it. Returns the stored receipt.

    status: unchanged (already compliant, nothing written) | prepared (verified variant) | rejected (losses not accepted) |
    failed (blocked, or the re-imported variant does not meet the profile; the variant file is discarded)."""
    started = time.perf_counter()
    file, model = resolve_source(services, model_id, path)
    profile = asset_targets.get_profile(services.config.data_dir, target)
    sha_before = sha256_of(file)
    inspection = inspect_file(file, services.config.max_file_mb, with_walls=_wants_walls(profile))
    draft = report(profile, inspection)
    tolerance = max_deviation_mm if max_deviation_mm is not None else profile["rules"].get("max_deviation_mm")
    prep_id = "p" + uuid.uuid4().hex[:12]
    receipt = {"id": prep_id, "kind": "operation", "created_at": time.time(), "profile_id": profile["id"], "profile": draft["profile"],
               "source_model_id": model["id"] if model else None, "source_name": model["name"] if model else file.name,
               "source_path": str(file), "source_sha256": sha_before, "accept_losses": bool(accept_losses),
               "recipe": {"operations": draft["operations"], "max_deviation_mm": tolerance},
               "declared_losses": draft["declared_losses"], "defects_before": draft["defects"], "blockers": draft["blockers"],
               "summary_before": draft["summary"], "measurements_before": inspection["measurements"],
               "output_path": None, "output_sha256": None, "variant_model_id": None}

    def finish(status: str, message: str, **extra) -> dict:
        after_sha = sha256_of(file)
        if after_sha != sha_before:  # pragma: no cover - would be a bug in a writer
            raise RuntimeError(_("p.original_changed"))
        receipt.update(status=status, message=message, original_unchanged=True, source_sha256_after=after_sha,
                       seconds=round(time.perf_counter() - started, 2), **extra)
        path_out = _receipt_path(services, prep_id)
        path_out.parent.mkdir(parents=True, exist_ok=True)
        atomic.write_json_atomic(path_out, receipt)
        return receipt

    if draft["summary"]["compliant"] and not draft["operations"]:
        return finish("unchanged", _("p.unchanged") + ("" if draft["summary"]["fully_verified"] else _("p.unchanged_ne", n=draft["summary"]["not_evaluated"])))
    if draft["blockers"]:
        return finish("failed", _("p.blocked", reasons=" ".join(b["reason"] for b in draft["blockers"])))
    if draft["declared_losses"] and not accept_losses:
        labels = ", ".join(x["label"] for x in draft["declared_losses"])
        return finish("rejected", _("p.rejected", labels=labels))

    import trimesh

    ops = {op["op"]: op for op in draft["operations"]}
    output_format = profile["output_format"]
    engine_info = None
    if "simplify" in ops:
        _meshes, welded, _unit, _loaded = load_geometry(file)
        mesh, engine_info = _simplify(welded, ops["simplify"]["target_triangles"])
        ops["simplify"]["engine"] = engine_info
        export_obj = mesh
    else:
        _meshes, welded, _unit, loaded = load_geometry(file)
        keeps_visuals = output_format == "glb" and bool(_losses_of_rebuild(inspection["measurements"]))
        if keeps_visuals:  # the loaded scene keeps UVs, materials and textures the GLB writer supports
            export_obj = loaded if isinstance(loaded, trimesh.Scene) else trimesh.Scene(loaded)
        else:  # geometry only in the source: the welded mesh (an STL is a triangle soup until welded)
            export_obj = welded.copy()
        if "resize_textures" in ops and isinstance(export_obj, trimesh.Scene):
            ops["resize_textures"]["changed"] = _resize_textures(export_obj, ops["resize_textures"]["max_px"])
    if "scale" in ops:
        export_obj.apply_scale(ops["scale"]["factor"])
    keep_normals = output_format == "glb" and "simplify" not in ops and _value(inspection["measurements"], "attributes.normals") == "vertex"
    data = export_obj.export(file_type=output_format, include_normals=True) if keep_normals else export_obj.export(file_type=output_format)
    stem = re.sub(r"[^A-Za-z0-9]+", "-", file.stem).strip("-").lower()[:40] or "model"
    out = _prepared_dir(services) / f"{stem}-{profile['id']}-{sha_before[:8]}-{prep_id[1:7]}.{output_format}"
    out.parent.mkdir(parents=True, exist_ok=True)
    atomic.write_bytes_atomic(out, data)
    receipt["recipe"]["operations"] = list(ops.values())

    # Independent verification: fresh load from disk, every rule re-checked, deviation sampled on welded geometry in mm.
    after = inspect_file(out, with_walls=_wants_walls(profile))
    checks_after = evaluate(profile, after["measurements"])
    summary_after = summarize(checks_after)
    from .simplify import measure_error

    deviation = measure_error(welded_mm(file), welded_mm(out), DEVIATION_SAMPLES)
    deviation.update(note=_("dev.note"), tolerance_mm=tolerance,
                     within_tolerance=None if tolerance is None or deviation["hausdorff_mm"] is None else deviation["hausdorff_mm"] <= tolerance)
    declared_after = after["measurements"].get("geometry.triangles_declared")
    verification = {"loader": _("p.loader") + (_("p.loader_gltf") if declared_after else ""),
                    "triangles": after["measurements"]["geometry.triangles"]["value"],
                    "triangles_declared": declared_after["value"] if declared_after else None,
                    "counts_agree": None if not declared_after else declared_after["value"] == after["measurements"]["geometry.triangles"]["value"],
                    "checks": checks_after, "summary": summary_after}
    receipt.update(measurements_after=after["measurements"], verification=verification, deviation=deviation,
                   effective_changes=_effective_changes(inspection["measurements"], after["measurements"]),
                   observed_losses=_observed_losses(inspection["measurements"], after["measurements"]))
    problems = [f"{c['rule']}: {_defect_message(c)}" for c in checks_after if c["status"] == "fail"]
    if deviation["within_tolerance"] is False:
        problems.append(_("p.over_tolerance", dev=deviation["hausdorff_mm"], tol=tolerance))
    if verification["counts_agree"] is False:
        problems.append(_("p.counts_disagree"))
    if problems:
        out.unlink(missing_ok=True)
        return finish("failed", _("p.discarded", problems="; ".join(problems)),
                      discarded_output=str(out))
    receipt.update(output_path=str(out), output_sha256=sha256_of(out), output_bytes=out.stat().st_size,
                   download_url=f"/api/asset-preparations/{prep_id}/file")
    if model is not None and output_format in ("stl", "obj", "3mf"):
        from .family_tools import import_file, model_ref

        imported = import_file(services, str(out), model_ref(model["id"]))
        services.models.patch(imported["id"], {"name": f"{model['name']} · {profile['name']}",
                                                "notes": _("p.variant_note", profile=profile["name"], id=model["id"], receipt=prep_id)})
        services.models.tag(imported["id"], ["preparado", profile["id"]], [])
        receipt["variant_model_id"] = imported["id"]
    lost = ", ".join(x["label"] for x in receipt["observed_losses"])
    return finish("prepared", _("p.prepared", n=summary_after["pass"])
                  + (_("p.prepared_ne", n=summary_after["not_evaluated"]) if summary_after["not_evaluated"] else "")
                  + _("p.prepared_dev", dev=deviation["hausdorff_mm"]) + (_("p.prepared_lost", lost=lost) if lost else "") + ".")


def list_preparations(services, model_id: int | None = None, sha256: str | None = None, limit: int = 50) -> list[dict]:
    folder = _prepared_dir(services) / "receipts"
    if not folder.is_dir():
        return []
    rows = []
    for file in folder.glob("p*.json"):
        receipt = atomic.read_json(file, default=None)
        if not isinstance(receipt, dict):
            continue
        if model_id is not None and receipt.get("source_model_id") != model_id:
            continue
        if sha256 and receipt.get("source_sha256") != sha256:
            continue
        rows.append(receipt)
    rows.sort(key=lambda r: r.get("created_at") or 0, reverse=True)
    return rows[:limit]


def get_preparation(services, prep_id: str) -> dict:
    path = _receipt_path(services, prep_id)
    receipt = atomic.read_json(path, default=None)
    if not isinstance(receipt, dict):
        raise LookupError(_("e.prep_missing", id=prep_id))
    return receipt
