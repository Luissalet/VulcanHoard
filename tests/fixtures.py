"""Generated mesh fixtures (trimesh): obviously fictional models, written in every supported format."""

from __future__ import annotations

import shutil
from pathlib import Path

import numpy as np
import trimesh


def cube(extents=(20.0, 30.0, 40.0)) -> trimesh.Trimesh:
    return trimesh.creation.box(extents=extents)


def sphere(radius=10.0, subdivisions=3) -> trimesh.Trimesh:
    return trimesh.creation.icosphere(subdivisions=subdivisions, radius=radius)


def open_box() -> trimesh.Trimesh:
    """A cube with its top face removed: not watertight."""
    mesh = cube((25.0, 25.0, 25.0))
    normals = mesh.face_normals
    keep = normals[:, 2] < 0.5  # drop the two triangles facing +Z
    mesh.update_faces(keep)
    return mesh


def multi_body() -> trimesh.Trimesh:
    """Two separate bodies in one file."""
    ball = sphere(6.0, 2).apply_translation((60.0, 0.0, 0.0))
    return trimesh.util.concatenate([cube((20.0, 20.0, 20.0)), ball])


def can_export_3mf() -> bool:
    try:
        cube().export(file_type="3mf")
        return True
    except Exception:
        return False


def make_library(folder: Path) -> dict[str, Path]:
    """Write the fixture models under `folder` and return name → path."""
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "dragones").mkdir(exist_ok=True)
    (folder / "soportes").mkdir(exist_ok=True)
    (folder / ".oculta").mkdir(exist_ok=True)
    files: dict[str, Path] = {}

    files["cube_bin"] = folder / "dragones" / "dragon_cubo_v2.stl"
    cube().export(files["cube_bin"], file_type="stl")
    files["cube_ascii"] = folder / "dragones" / "dragon_cubo_ascii.stl"
    cube().export(files["cube_ascii"], file_type="stl_ascii")
    files["cube_copy"] = folder / "soportes" / "copia_del_cubo.stl"
    shutil.copyfile(files["cube_bin"], files["cube_copy"])  # exact duplicate (same bytes)

    files["sphere_obj"] = folder / "esfera_lisa.obj"
    sphere().export(files["sphere_obj"], file_type="obj")
    files["sphere_near"] = folder / "soportes" / "esfera_casi_igual.stl"
    sphere(10.02).export(files["sphere_near"], file_type="stl")  # near duplicate: same triangles, +0.2 % size (+0.6 % volume)

    files["open_box"] = folder / "soportes" / "caja_abierta.stl"
    open_box().export(files["open_box"], file_type="stl")

    files["tiny_inch"] = folder / "pieza_en_pulgadas.stl"
    cube((1.0, 1.5, 2.0)).export(files["tiny_inch"], file_type="stl")

    files["broken"] = folder / "roto.stl"
    files["broken"].write_bytes(b"this is not a mesh at all, just some bytes that go nowhere\n" * 4)

    files["hidden"] = folder / ".oculta" / "ignorado.stl"
    cube().export(files["hidden"], file_type="stl")

    if can_export_3mf():
        files["multi_3mf"] = folder / "dragones" / "dos_cuerpos.3mf"
        multi_body().export(files["multi_3mf"], file_type="3mf")
    else:  # keep the reader path covered with a hand-made 3MF
        files["multi_3mf"] = folder / "dragones" / "dos_cuerpos.3mf"
        files["multi_3mf"].write_bytes(minimal_3mf(multi_body()))
    return files


def minimal_3mf(mesh: trimesh.Trimesh) -> bytes:
    """A bare-bones 3MF package (zip with the core model XML) for the reader test when trimesh cannot export."""
    import io
    import zipfile

    verts = "".join(f'<vertex x="{x}" y="{y}" z="{z}"/>' for x, y, z in np.asarray(mesh.vertices))
    tris = "".join(f'<triangle v1="{a}" v2="{b}" v3="{c}"/>' for a, b, c in np.asarray(mesh.faces))
    model = (
        '<?xml version="1.0" encoding="UTF-8"?><model unit="millimeter" xml:lang="en-US" xmlns="http://schemas.microsoft.com/3dmanufacturing/core/2015/02">'
        f'<resources><object id="1" type="model"><mesh><vertices>{verts}</vertices><triangles>{tris}</triangles></mesh></object></resources>'
        '<build><item objectid="1"/></build></model>'
    )
    rels = ('<?xml version="1.0" encoding="UTF-8"?><Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
            '<Relationship Target="/3D/3dmodel.model" Id="rel0" Type="http://schemas.microsoft.com/3dmanufacturing/2013/01/3dmodel"/></Relationships>')
    types = ('<?xml version="1.0" encoding="UTF-8"?><Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
             '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
             '<Default Extension="model" ContentType="application/vnd.ms-package.3dmanufacturing-3dmodel+xml"/></Types>')
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("[Content_Types].xml", types)
        archive.writestr("_rels/.rels", rels)
        archive.writestr("3D/3dmodel.model", model)
    return buffer.getvalue()


EXPECTED_MODELS = 9  # everything above except the hidden file
