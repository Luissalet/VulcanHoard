"""Build and retain CPU-only primitive blocking packages."""

from __future__ import annotations

import json
import subprocess
import tempfile
from pathlib import Path
from typing import Annotated, Literal
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field, model_validator

from .blender_render import find_blender
from .hoard_link.agentkit import Tool, ann

Coordinate = Annotated[float, Field(ge=-5, le=5)]
Angle = Annotated[float, Field(ge=-360, le=360)]
Position = tuple[Coordinate, Coordinate, Coordinate]
Rotation = tuple[Angle, Angle, Angle]
Grip = Literal["rest", "sheet_left", "sheet_right", "tube"]
Accessory = Literal["sheet", "tube", "stairs"]
FPS = 24


class Input(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Camera(Input):
    center: Position = (0, -0.25, 0.72)
    azimuth: Angle = 22
    elevation: float = Field(12, ge=-80, le=80)
    ortho_scale: float = Field(2.8, ge=0.5, le=10)
    distance: float = Field(6, ge=3.1, le=30)


class Key(Input):
    frame: int = Field(ge=0, le=96)
    sheet: Position = (0, -0.4, 1.25)
    sheet_rotation: Rotation = (0, 0, 0)
    sheet_width: float = Field(0.46, ge=0.06, le=0.65)
    tube: Position = (0.33, 0.06, 1.02)
    tube_rotation: Rotation = (0, -13, 0)
    left_hand: Grip | Position = "rest"
    right_hand: Grip | Position = "rest"
    sheet_roll: bool = False


class Build(Input):
    gesture: Literal["unroll", "flip", "pass", "custom"] = Field(
        "flip", description="Unroll, flip, pass or custom. Desenrollar, voltear, pasar o toma propia."
    )
    accessories: set[Accessory] = Field(
        default_factory=lambda: {"sheet", "tube", "stairs"},
        description="Defaults to all three props. Incluye hoja, tubo y escalera por defecto.",
    )
    poses: list[Key] | None = Field(None, min_length=2, max_length=8)
    camera: Camera = Field(default_factory=Camera)
    size: int = Field(384, ge=128, le=768, description="Square PNG size in pixels. Tamaño de los PNG cuadrados en píxeles.")

    @model_validator(mode="after")
    def check_poses(self):
        if (self.poses is not None) != (self.gesture == "custom"):
            raise ValueError("Use poses with gesture=custom. / Usa poses con gesture=custom.")
        if self.poses is not None:
            frames = [key.frame for key in self.poses]
            if frames[0] != 0 or any(a >= b for a, b in zip(frames, frames[1:])):
                raise ValueError("Pose frames must increase from 0. / Los fotogramas deben aumentar desde 0.")
        for key in self.poses or preset(self.gesture):
            grips = (key.left_hand, key.right_hand)
            if "sheet" not in self.accessories and (key.sheet_roll or "sheet_left" in grips or "sheet_right" in grips):
                raise ValueError("Sheet grip or roll requires the sheet. / El agarre o enrollado requiere la hoja.")
            if "tube" not in self.accessories and "tube" in grips:
                raise ValueError("Tube grip requires the tube. / El agarre requiere el tubo.")
        return self


def preset(gesture: str) -> list[Key]:
    frames = (0, 24, 48)
    if gesture == "unroll":
        return [
            Key(frame=frame, sheet=(0.29 - width / 2, -0.40, 1.27), sheet_width=width,
                tube=(0.34, -0.4, 1.26), tube_rotation=(0, 0, 0),
                left_hand="tube", right_hand="sheet_right", sheet_roll=True)
            for frame, width in zip(frames, (0.10, 0.28, 0.46))
        ]
    if gesture == "flip":
        # Rotate around the right-hand grip at the edge, lifting it briefly.
        return [
            Key(frame=0, sheet=(-0.10, -0.40, 1.23), right_hand="sheet_right"),
            Key(frame=24, sheet=(-0.33, -0.66, 1.40), sheet_rotation=(0, 0, -90), right_hand="sheet_right"),
            Key(frame=48, sheet=(-0.56, -0.40, 1.23), sheet_rotation=(0, 0, -180), right_hand="sheet_right"),
        ]
    return [
        Key(frame=0, sheet=(-0.23, -0.39, 1.25), right_hand="sheet_right"),
        Key(frame=24, sheet=(0, -0.43, 1.36), right_hand="sheet_right", left_hand="sheet_left"),
        Key(frame=48, sheet=(0.23, -0.39, 1.25), left_hand="sheet_left"),
    ]


class Lookup(Input):
    id: str = Field(pattern=r"^[a-f0-9]{32}$")


class Undo(Lookup):
    restore: bool = False


def _root(services) -> Path:
    return services.config.data_dir.resolve() / "blockings"


def _receipt(package: Path) -> dict:
    metadata = package / "blocking.json"
    if not metadata.is_file():
        raise ValueError("Blocking package not found. / No se ha encontrado el bloqueo.")
    result = json.loads(metadata.read_text(encoding="utf-8"))
    result["path"] = str(package)
    result["files"] = {label: str(package / file) for label, file in result["files"].items()}
    for key in result["keyframes"]:
        key["maps"] = {label: str(package / file) for label, file in key["maps"].items()}
    first = result["keyframes"][0]["maps"]
    result["anchors"] = {"blocking": result["files"]["mesh"], "parts": str(metadata),
                         "blocking_color": first["color"], "depth": first["depth"], "normals": first["normals"]}
    result["undo"] = {"tool": "blocking_undo", "arguments": {"id": result["id"]}}
    return result


def build(services, args: Build) -> dict:
    blender = find_blender()
    if not blender:
        raise RuntimeError("Install Blender or set VULCAN_BLENDER. / Instala Blender o configura VULCAN_BLENDER.")
    root = _root(services)
    root.mkdir(parents=True, exist_ok=True)
    package_id = uuid4().hex
    spec = args.model_dump(mode="json", exclude={"poses", "accessories"})
    spec.update(id=package_id, accessories=sorted(args.accessories), fps=FPS,
                poses=[key.model_dump(mode="json") for key in args.poses or preset(args.gesture)])
    worker = Path(__file__).with_name("blender_blocking_worker.py")
    with tempfile.TemporaryDirectory(prefix=".build-", dir=root) as scratch:
        recipe = Path(scratch) / "recipe.json"
        recipe.write_text(json.dumps(spec), encoding="utf-8")
        output = Path(scratch) / "output"
        output.mkdir()
        result = subprocess.run(
            [blender, "--background", "--factory-startup", "--threads", "2", "--python-exit-code", "1",
             "--python", str(worker), "--", str(recipe), str(output)],
            capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=90,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        if result.returncode:
            raise RuntimeError("Blocking failed. / Falló el bloqueo.\n" + (result.stdout + result.stderr)[-2400:])
        receipt = _receipt(output)
        files = list(receipt["files"].values())
        files.extend(path for key in receipt["keyframes"] for path in key["maps"].values())
        if any(not Path(path).is_file() or not Path(path).stat().st_size for path in files):
            raise RuntimeError("Blocking output incomplete. / El bloqueo está incompleto.")
        output.rename(root / package_id)
    return _receipt(root / package_id)


def get(services, args: Lookup) -> dict:
    return _receipt(_root(services) / args.id)


def undo(services, args: Undo) -> dict:
    root = _root(services)
    active, withdrawn = root / args.id, root / (".withdrawn-" + args.id)
    source, target = (withdrawn, active) if args.restore else (active, withdrawn)
    try:
        source.rename(target)
    except FileNotFoundError:
        if not (target / "blocking.json").is_file():
            raise ValueError("Blocking package not found. / No se ha encontrado el bloqueo.") from None
    return {"ok": True, "id": args.id, "withdrawn": not args.restore,
            "undo": {"tool": "blocking_undo", "arguments": {"id": args.id, "restore": not args.restore}}}


TOOLS = [
    Tool("blocking_build", "Build a primitive take on CPU. Crea una toma con articulación en CPU.\n"
         "Returns editable scene, animated GLB and same-camera colour, depth and normal maps at each key. "
         "Crea un bloqueo de primitivas con dos manos, piezas articuladas, accesorios y una toma corta. "
         "Devuelve la escena editable, el GLB animado y mapas de color, profundidad y normales de cada clave. "
         "Undo / Deshacer: blocking_undo.", Build, ann(draft_safe=True), build, timeout_s=105, capped=False),
    Tool("blocking_get", "Read a blocking package. Consulta un bloqueo y su articulación.\n"
         "Returns named parts, files and maps by keyframe. Devuelve piezas, archivos y mapas por fotograma.",
         Lookup, ann(True), get, capped=False),
    Tool("blocking_undo", "Withdraw or restore a blocking package. Retira o restaura un bloqueo.\n"
         "Returns the inverse call. Devuelve la operación inversa.",
         Undo, ann(idempotent=True), undo),
]
