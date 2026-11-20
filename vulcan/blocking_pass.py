"""Author a handoff on a pinned, detailed articulation scene."""

from __future__ import annotations

import json
import tempfile
from pathlib import Path
from shutil import copyfile
from typing import Annotated
from uuid import uuid4

from pydantic import Field, FiniteFloat, model_validator

from . import blocking
from .articulation.service import run_worker
from .asset_inspection import sha256_of
from .hoard_link.agentkit import AppError, Tool, ann

Vec = tuple[FiniteFloat, FiniteFloat, FiniteFloat]
Name = Annotated[str, Field(min_length=1, max_length=100)]
Radius = Annotated[float, Field(gt=0, le=1)]


class Arm(blocking.Input):
    shoulder: Vec
    elbow: Vec
    wrist: Vec
    bind_contact: Vec = Field(description="Point on the palm in the source pose. Punto de contacto de la mano en la pose de origen.")
    pole: Vec = Field(description="World direction of the bend. Dirección del codo en el espacio de la escena.")
    skin: dict[Name, tuple[Radius, Radius, Radius]] = Field(
        default_factory=dict, description="Mesh names with upper arm, forearm and hand binding radii in metres. "
        "Nombres de mallas con radios de influencia en metros para brazo, antebrazo y mano.")
    forearm_parts: list[Name] = Field(default_factory=list, max_length=16)
    hand_parts: list[Name] = Field(default_factory=list, max_length=16)
    grip_rotation: blocking.Rotation = (0, 0, 0)


class Hold(blocking.Input):
    contact: Vec = Field(description="World position of the active grip. Posición del agarre activo en la escena.")
    rotation: blocking.Rotation = (0, 0, 0)


class SheetArt(blocking.Input):
    path: str = Field(max_length=1000)
    sha256: str = Field(pattern=r"^[a-fA-F0-9]{64}$")
    up: Vec = Field(description="Up direction of the artwork in the source. Dirección hacia arriba del dibujo en el origen.")


class Handoff(blocking.Input):
    source: str = Field(max_length=1000, description="Editable articulation scene (.blend). Escena editable de articulación (.blend).")
    source_sha256: str = Field(pattern=r"^[a-fA-F0-9]{64}$")
    source_frame: int = Field(1, ge=0, le=100_000)
    sheet: Name
    left_edge: Vec
    right_edge: Vec
    sheet_art: SheetArt | None = None
    left: Arm
    right: Arm
    direction: blocking.PassDirection = "left_to_right"
    start: Hold
    overlap: Hold
    finish: Hold
    approach_contact: Vec | None = Field(
        None, description="Optional receiving-hand contact target at frame 12. "
        "Objetivo opcional de la mano que recibe en el fotograma 12.")
    camera: blocking.Camera
    review_camera: blocking.Camera | None = None
    size: int = Field(384, ge=128, le=768)

    @model_validator(mode="after")
    def validate_bindings(self):
        rigid = [self.sheet, *self.left.forearm_parts, *self.right.forearm_parts,
                 *self.left.hand_parts, *self.right.hand_parts]
        if len(rigid) != len(set(rigid)) or set(rigid) & (self.left.skin.keys() | self.right.skin.keys()):
            raise ValueError("Rigid parts must have a single binding. / Cada pieza rígida debe tener una sola asignación.")
        for arm in (self.left, self.right):
            if not arm.skin and not arm.hand_parts:
                raise ValueError("Bind geometry for both hands. / Asigna geometría a las dos manos.")
        if self.left_edge == self.right_edge:
            raise ValueError("Choose two separate sheet contacts. / Marca dos contactos distintos en la hoja.")
        return self


def build(services, args: Handoff) -> dict:
    source = Path(args.source)
    if not source.is_absolute() or source.suffix.lower() != ".blend" or not source.is_file():
        raise ValueError("Use an editable articulation scene. / Usa una escena editable de articulación.")
    root = blocking._root(services)
    root.mkdir(parents=True, exist_ok=True)
    package_id = uuid4().hex
    worker = Path(__file__).with_name("blender_pass_worker.py")
    with tempfile.TemporaryDirectory(prefix=".pass-", dir=root) as scratch:
        scratch = Path(scratch)
        pinned = scratch / "source.blend"
        digest = _pin(source, pinned, args.source_sha256)
        controls = args.model_dump(mode="json")
        controls["source_sha256"] = digest
        spec = {**controls, "id": package_id, "fps": blocking.FPS}
        if args.sheet_art:
            art = Path(args.sheet_art.path)
            if not art.is_absolute() or art.suffix.lower() not in {".png", ".jpg", ".jpeg"} or not art.is_file():
                raise ValueError("Use an absolute PNG or JPEG path. / Usa una ruta absoluta a un PNG o JPEG.")
            art_copy = scratch / ("sheet" + art.suffix.lower())
            art_digest = _pin(art, art_copy, args.sheet_art.sha256)
            controls["sheet_art"]["sha256"] = art_digest
            spec["sheet_art"] = {**controls["sheet_art"], "path": str(art_copy)}
        recipe = scratch / "recipe.json"
        recipe.write_text(json.dumps(spec), encoding="utf-8")
        output = scratch / "output"
        output.mkdir()
        run_worker(worker, [pinned, recipe, output], output / "blocking.json", timeout=155)
        (output / "controls.json").write_text(json.dumps(controls, indent=2, ensure_ascii=False), encoding="utf-8")
        staged = blocking._receipt(output)
        paths = list(staged["files"].values())
        for key in staged["keyframes"]:
            paths.extend([key["geometry"], *key["maps"].values()])
        if any(not Path(path).is_file() or not Path(path).stat().st_size for path in paths):
            raise RuntimeError("Pass output incomplete. / La toma del pase está incompleta.")
        output.rename(root / package_id)
    return blocking.get(services, blocking.Lookup(id=package_id))


def _pin(source: Path, target: Path, expected: str) -> str:
    copyfile(source, target)
    digest = sha256_of(target)
    if digest != expected.lower():
        raise AppError("conflict", "The source revision changed. / Ha cambiado la revisión de origen.")
    return digest


TOOLS = [
    Tool("blocking_pass", "Pass a sheet between the hands of a detailed rig. Pasa una hoja entre las manos de un modelo detallado.\n"
         "Returns an editable take, key views and an animated GLB. Undo: blocking_undo. "
         "Devuelve toma editable, vistas por clave y GLB animado. Deshacer: blocking_undo.",
         Handoff, ann(draft_safe=True), build, timeout_s=175, capped=False),
]
