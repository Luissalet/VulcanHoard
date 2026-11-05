"""Mesh inspection with trimesh: triangles, vertices, bounding box, volume, area, watertightness, bodies.

Everything is reported in millimetres (the de-facto unit of STL/OBJ; 3MF carries its unit and
trimesh converts it). `units_guess` flags files whose size makes millimetres unlikely.
"""

from __future__ import annotations

import functools
import hashlib
import logging
import os
import re
import tempfile
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np

log = logging.getLogger("vulcan.geometry")

FORMATS = {".stl": "stl", ".3mf": "3mf", ".obj": "obj", ".glb": "glb", ".gltf": "gltf"}
LEGACY_INCLUDE = ["**/*.stl", "**/*.3mf", "**/*.obj"]  # the default before GLB/glTF/FBX were indexed: roots that kept it get the new one
DEFAULT_INCLUDE = LEGACY_INCLUDE + ["**/*.glb", "**/*.gltf", "**/*.fbx"]
SCENE_FORMATS = ("glb", "gltf", "fbx")  # glTF conventions: metres and Y up (FBX is read through Blender into a GLB)
Y_UP_TO_Z_UP = np.array([[1.0, 0, 0, 0], [0, 0, -1.0, 0], [0, 1.0, 0, 0], [0, 0, 0, 1.0]])


@functools.lru_cache(maxsize=1)
def fbx_supported() -> bool:
    """FBX files are listed only where Blender can convert them."""
    from .blender_render import find_blender

    return bool(find_blender())


def fbx_cache_dir() -> Path:
    return Path(os.environ.get("VULCAN_FBX_CACHE") or Path(tempfile.gettempdir()) / "vulcan-fbx-cache")


def fbx_as_glb(path: Path) -> Path:
    """The GLB Blender makes from an FBX, cached by the FBX's content (shared by every process of the app)."""
    from .asset_animation_fbx import run_blender
    from .hoard_link import atomic

    digest = hashlib.sha256(Path(path).read_bytes()).hexdigest()[:20]
    folder = fbx_cache_dir()
    folder.mkdir(parents=True, exist_ok=True)
    target = folder / f"{digest}.glb"
    if not target.is_file():
        tmp = folder / f"{digest}.{os.getpid()}.tmp.glb"
        run_blender("fbx2glb", Path(path), tmp)
        atomic.replace_with_retry(tmp, target)
    return target


@dataclass
class MeshInfo:
    triangles: int
    vertices: int
    bbox: tuple[float, float, float]  # extents in mm (x, y, z)
    volume_cm3: float
    surface_cm2: float
    watertight: bool
    bodies: int
    units_guess: str  # mm | inches | meters | large
    geometry: object = field(default=None, repr=False)  # the trimesh mesh for the thumbnail (not stored)

    @classmethod
    def from_dict(cls, data: dict) -> "MeshInfo":
        return cls(triangles=data["triangles"], vertices=data["vertices"], bbox=(data["bbox_x"], data["bbox_y"], data["bbox_z"]),
                   volume_cm3=data["volume_cm3"], surface_cm2=data["surface_cm2"], watertight=bool(data["watertight"]),
                   bodies=data["bodies"], units_guess=data["units_guess"])

    def to_dict(self) -> dict:
        return {
            "triangles": self.triangles, "vertices": self.vertices,
            "bbox_x": self.bbox[0], "bbox_y": self.bbox[1], "bbox_z": self.bbox[2],
            "volume_cm3": self.volume_cm3, "surface_cm2": self.surface_cm2,
            "watertight": self.watertight, "bodies": self.bodies, "units_guess": self.units_guess,
        }


def format_for(path: Path) -> str | None:
    suffix = Path(path).suffix.lower()
    if suffix == ".fbx":
        return "fbx" if fbx_supported() else None
    return FORMATS.get(suffix)


def prettify_name(stem: str) -> str:
    """`dragon_bust-v2_supported` → `dragon bust v2 supported`; keeps case of acronyms, capitalises the first letter."""
    text = re.sub(r"[_\-]+", " ", stem)
    text = re.sub(r"(?<=[a-z])(?=[A-Z][a-z])", " ", text)  # camelCase → camel Case
    text = re.sub(r"\s+", " ", text).strip()
    return text[:1].upper() + text[1:] if text else stem


def guess_units(extents: np.ndarray) -> str:
    """Heuristic on the largest extent: real prints are ~5 mm – 500 mm."""
    largest = float(np.max(extents)) if extents.size else 0.0
    if largest <= 0:
        return "mm"
    if largest < 1.0:
        return "meters"  # a 0.12 mm "print" is a 120 mm model exported in metres
    if largest < 5.0:
        return "inches"  # 0.5–5 mm is typically 1–5 inches exported without conversion
    if largest > 1500.0:
        return "large"  # bigger than any hobby printer: probably microns or a scene
    return "mm"


def _load(path: Path):
    import trimesh

    kind = format_for(path)
    if kind in SCENE_FORMATS:  # glTF scenes (skinned meshes in their bind pose): metres -> mm and Y up -> Z up, like the library
        source = fbx_as_glb(path) if kind == "fbx" else path
        mesh = trimesh.load(str(source), file_type="gltf" if kind == "gltf" else "glb", force="mesh", process=True)
        if not hasattr(mesh, "faces") or len(mesh.faces) == 0:
            raise ValueError("no triangles found in the file")
        mesh.apply_scale(1000.0)
        mesh.apply_transform(Y_UP_TO_Z_UP)
        return mesh
    mesh = trimesh.load(str(path), file_type=kind, force="mesh", process=True)
    if not hasattr(mesh, "faces") or len(mesh.faces) == 0:
        raise ValueError("no triangles found in the file")
    units = getattr(mesh, "units", None)
    if units and units not in ("mm", "millimeter", "millimeters"):
        try:
            mesh = mesh.convert_units("mm", guess=False)
        except Exception as error:  # unknown unit strings: keep the numbers as they are
            log.debug("%s: cannot convert units %r: %s", path, units, error)
    return mesh


def inspect_mesh(mesh) -> MeshInfo:
    faces = int(len(mesh.faces))
    vertices = int(len(mesh.vertices))
    extents = np.asarray(mesh.extents, dtype=float) if faces else np.zeros(3)
    watertight = bool(mesh.is_watertight)
    volume = float(abs(mesh.volume))  # signed volume: an open mesh gives an estimate, reported as such by watertight=False
    area = float(mesh.area)
    try:
        bodies = int(mesh.body_count)
    except Exception:  # pragma: no cover - scipy missing or degenerate graph
        bodies = 1
    return MeshInfo(
        triangles=faces,
        vertices=vertices,
        bbox=(round(float(extents[0]), 3), round(float(extents[1]), 3), round(float(extents[2]), 3)),
        volume_cm3=round(volume / 1000.0, 4),
        surface_cm2=round(area / 100.0, 3),
        watertight=watertight,
        bodies=max(1, bodies),
        units_guess=guess_units(extents),
        geometry=mesh,
    )


def inspect_file(path: Path) -> MeshInfo:
    """Parse one STL/OBJ/3MF/GLB/glTF (or, with Blender, FBX) file. Raises ValueError/OSError on unreadable files."""
    if format_for(path) is None:
        raise ValueError(f"unsupported format: {path.suffix}")
    return inspect_mesh(_load(path))
