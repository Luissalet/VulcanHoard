"""Build and retain CPU-only primitive blocking packages."""

from __future__ import annotations

import json
from shutil import copyfile
import subprocess
import tempfile
from pathlib import Path
from typing import Annotated, Literal
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field, model_validator

from .asset_inspection import read_gltf, sha256_of
from .blender_render import find_blender
from .hoard_link.agentkit import Tool, ann

Coordinate = Annotated[float, Field(ge=-5, le=5)]
Angle = Annotated[float, Field(ge=-360, le=360)]
Position = tuple[Coordinate, Coordinate, Coordinate]
Rotation = tuple[Angle, Angle, Angle]
Grip = Literal["rest", "sheet_left", "sheet_right", "tube"]
Accessory = Literal["sheet", "tube", "stairs"]
PassDirection = Literal["left_to_right", "right_to_left"]
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
    gesture: Literal["unroll", "flip", "pass", "custom", "reverse"] = Field(
        "flip", description="Unroll, turn upright (flip), pass, custom or turn over (reverse). "
        "Desenrollar, enderezar (flip), pasar, toma propia o girar al dorso (reverse)."
    )
    pass_direction: PassDirection | None = Field(
        None, description="For pass only, from the character's perspective. Defaults to left_to_right. "
        "Solo para pass, desde el punto de vista del personaje. Por defecto, de izquierda a derecha."
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
        if self.pass_direction is not None and self.gesture != "pass":
            raise ValueError("Use pass_direction with gesture=pass. / Usa pass_direction con gesture=pass.")
        if (self.poses is not None) != (self.gesture == "custom"):
            raise ValueError("Use poses with gesture=custom. / Usa poses con gesture=custom.")
        if self.poses is not None:
            frames = [key.frame for key in self.poses]
            if frames[0] != 0 or any(a >= b for a, b in zip(frames, frames[1:])):
                raise ValueError("Pose frames must increase from 0. / Los fotogramas deben aumentar desde 0.")
        for key in self.resolved_poses():
            grips = (key.left_hand, key.right_hand)
            if "sheet" not in self.accessories and (key.sheet_roll or "sheet_left" in grips or "sheet_right" in grips):
                raise ValueError("Sheet grip or roll requires the sheet. / El agarre o enrollado requiere la hoja.")
            if "tube" not in self.accessories and "tube" in grips:
                raise ValueError("Tube grip requires the tube. / El agarre requiere el tubo.")
        return self

    def resolved_poses(self) -> list[Key]:
        return self.poses or preset(self.gesture, self.pass_direction or "left_to_right")


def preset(gesture: str, pass_direction: PassDirection = "left_to_right") -> list[Key]:
    frames = (0, 24, 48)
    if gesture == "unroll":
        return [
            Key(frame=frame, sheet=(0.29 - width / 2, -0.40, 1.27), sheet_width=width,
                tube=(0.34, -0.4, 1.26), tube_rotation=(0, 0, 0),
                left_hand="tube", right_hand="sheet_right", sheet_roll=True)
            for frame, width in zip(frames, (0.10, 0.28, 0.46))
        ]
    if gesture == "flip":
        # The right hand holds the same edge as the front turns in its own plane.
        return [
            Key(frame=0, sheet=(-0.17, -0.40, 1.23), sheet_rotation=(0, 180, 0), right_hand="sheet_left"),
            Key(frame=24, sheet=(-0.25, -0.45, 1.43), sheet_rotation=(0, 90, 0), right_hand="sheet_left"),
            Key(frame=48, sheet=(-0.31, -0.40, 1.23), right_hand="sheet_left"),
        ]
    if gesture == "reverse":
        return [
            Key(frame=0, sheet=(-0.10, -0.40, 1.23), right_hand="sheet_right"),
            Key(frame=24, sheet=(-0.33, -0.66, 1.40), sheet_rotation=(0, 0, -90), right_hand="sheet_right"),
            Key(frame=48, sheet=(-0.56, -0.40, 1.23), sheet_rotation=(0, 0, -180), right_hand="sheet_right"),
        ]
    if pass_direction == "left_to_right":
        return [
            Key(frame=0, sheet=(0.23, -0.39, 1.25), left_hand="sheet_left"),
            Key(frame=24, sheet=(0, -0.43, 1.36), right_hand="sheet_right", left_hand="sheet_left"),
            Key(frame=48, sheet=(-0.23, -0.39, 1.25), right_hand="sheet_right"),
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


class Freeze(Input):
    id: str | None = Field(None, pattern=r"^[a-f0-9]{32}$")
    source: str | None = Field(None, max_length=1000)
    source_sha256: str | None = Field(None, pattern=r"^[a-fA-F0-9]{64}$")
    frame: int = Field(ge=0, le=100_000)
    fps: int = Field(FPS, ge=1, le=240)
    print_size_mm: float | None = Field(None, ge=10, le=2000, description="Longest side in the print. / Lado mayor de la figura impresa.")
    sheet_wall_mm: float | None = Field(None, ge=0.1, le=5, description="Minimum wall for the blocking's paper, in print millimetres. / Grosor mínimo del papel del bloqueo, en milímetros impresos.")

    @model_validator(mode="after")
    def check_source(self):
        if bool(self.id) == bool(self.source) or bool(self.source) != bool(self.source_sha256):
            raise ValueError("Supply a blocking ID or a GLB path with its SHA-256. / Indica un ID de bloqueo o un GLB con su SHA-256.")
        if (self.print_size_mm is None) != (self.sheet_wall_mm is None):
            raise ValueError("Supply print_size_mm and sheet_wall_mm together. / Indica print_size_mm y sheet_wall_mm juntos.")
        return self


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
        if "geometry" in key:
            key["geometry"] = str(package / key["geometry"])
            key["layers"] = {
                part: {label: str(package / file) for label, file in paths.items()}
                for part, paths in key["layers"].items()
            }
    first = result["keyframes"][0]["maps"]
    result["anchors"] = {"blocking": result["files"]["mesh"], "parts": str(metadata),
                         "blocking_color": first["color"],
                         **{label: first[label] for label in ("depth", "normals") if label in first}}
    result["undo"] = {"tool": "blocking_undo", "arguments": {"id": result["id"]}}
    return result


def build(services, args: Build) -> dict:
    blender = find_blender()
    if not blender:
        raise RuntimeError("Install Blender or set VULCAN_BLENDER. / Instala Blender o configura VULCAN_BLENDER.")
    root = _root(services)
    root.mkdir(parents=True, exist_ok=True)
    package_id = uuid4().hex
    spec = args.model_dump(mode="json", exclude={"poses", "accessories", "pass_direction"})
    if args.gesture == "pass":
        spec["pass_direction"] = args.pass_direction or "left_to_right"
    spec.update(id=package_id, accessories=sorted(args.accessories), fps=FPS,
                poses=[key.model_dump(mode="json") for key in args.resolved_poses()])
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
        for key in receipt["keyframes"]:
            files.extend(key["maps"].values())
            files.append(key["geometry"])
            files.extend(path for layer in key["layers"].values() for path in layer.values())
        if any(not Path(path).is_file() or not Path(path).stat().st_size for path in files):
            raise RuntimeError("Blocking output incomplete. / El bloqueo está incompleto.")
        output.rename(root / package_id)
    return _receipt(root / package_id)


def get(services, args: Lookup) -> dict:
    return _receipt(_root(services) / args.id)


def _pose_root(services) -> Path:
    return _root(services) / "poses"


def freeze(services, args: Freeze) -> dict:
    package = get(services, Lookup(id=args.id)) if args.id else None
    source = Path(package["files"]["scene"] if package else args.source).resolve(strict=True)
    digest = sha256_of(source)
    fps = package["fps"] if package else args.fps
    contacts = None
    if package:
        if args.frame > package["poses"][-1]["frame"]:
            raise ValueError("Frame is outside the take. / El fotograma queda fuera de la toma.")
        key = next((key for key in package["keyframes"] if key["frame"] == args.frame), {})
        if key.get("geometry"):
            contacts = json.loads(Path(key["geometry"]).read_text(encoding="utf-8"))["contacts"]
    else:
        if source.suffix.lower() != ".glb" or digest != args.source_sha256.lower():
            raise ValueError("The pinned GLB does not match. / El GLB fijado no coincide.")
        doc, _ = read_gltf(source)
        if len(doc.get("animations", [])) != 1:
            raise ValueError("A single animated take is required. / Se necesita una toma con una sola animación.")
        samplers = doc["animations"][0]["samplers"]
        end = max((doc["accessors"][sampler["input"]]["max"][0] for sampler in samplers), default=0)
        if args.frame / fps > end + 1e-5:
            raise ValueError("Frame is outside the take. / El fotograma queda fuera de la toma.")
    blender = find_blender()
    if not blender:
        raise RuntimeError("Install Blender or set VULCAN_BLENDER. / Instala Blender o configura VULCAN_BLENDER.")
    root = _pose_root(services)
    root.mkdir(parents=True, exist_ok=True)
    pose_id = uuid4().hex
    worker = Path(__file__).with_name("blender_blocking_worker.py")
    with tempfile.TemporaryDirectory(prefix=".pose-", dir=root) as scratch:
        scratch = Path(scratch)
        output = scratch / "output"
        output.mkdir()
        pinned = scratch / source.name
        copyfile(source, pinned)
        if sha256_of(pinned) != digest:
            raise ValueError("The animation changed. / Ha cambiado la animación.")
        result = subprocess.run(
            [blender, "--background", "--factory-startup", "--disable-autoexec", "--threads", "2",
             "--python-exit-code", "1", "--python", str(worker), "--", "freeze", str(pinned), str(output),
             str(args.frame), str(fps), str(args.print_size_mm or 0), str(args.sheet_wall_mm or 0)],
            capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=85,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        if result.returncode or not (output / "pose.glb").is_file():
            raise RuntimeError("Freezing failed. / Ha fallado la congelación.\n" + (result.stdout + result.stderr)[-2400:])
        receipt = json.loads((output / "pose.json").read_text(encoding="utf-8"))
        receipt.update(id=pose_id, blocking_id=args.id, frame=args.frame, fps=fps, seconds=args.frame / fps,
                       source_sha256=digest, sha256=sha256_of(output / "pose.glb"))
        if contacts is not None:
            receipt["key_contacts"] = contacts
        (output / "pose.json").write_text(json.dumps(receipt), encoding="utf-8")
        output.rename(root / pose_id)
    return pose_get(services, Lookup(id=pose_id))


def pose_get(services, args: Lookup) -> dict:
    folder = _pose_root(services) / args.id
    receipt = folder / "pose.json"
    if not receipt.is_file():
        raise ValueError("Frozen pose not found. / No se ha encontrado la pose congelada.")
    return {**json.loads(receipt.read_text(encoding="utf-8")), "path": str(folder / "pose.glb"),
            "receipt": str(receipt), "undo": {"tool": "blocking_pose_undo", "arguments": {"id": args.id}}}


def pose_undo(services, args: Undo) -> dict:
    return _withdraw(_pose_root(services), args, "pose.json", "blocking_pose_undo")


def undo(services, args: Undo) -> dict:
    return _withdraw(_root(services), args, "blocking.json", "blocking_undo")


def _withdraw(root: Path, args: Undo, manifest: str, tool: str) -> dict:
    active, withdrawn = root / args.id, root / (".withdrawn-" + args.id)
    source, target = (withdrawn, active) if args.restore else (active, withdrawn)
    try:
        source.rename(target)
    except FileNotFoundError:
        if not (target / manifest).is_file():
            raise ValueError("Blocking package not found. / No se ha encontrado el bloqueo.") from None
    return {"ok": True, "id": args.id, "withdrawn": not args.restore,
            "undo": {"tool": tool, "arguments": {"id": args.id, "restore": not args.restore}}}


TOOLS = [
    Tool("blocking_build", "Build a primitive take on CPU. Crea una toma con articulación en CPU.\n"
         "Returns an editable scene, animated GLB, per-key maps and transparent part layers with grip contacts. "
         "Devuelve la escena editable, el GLB animado, mapas por clave, capas transparentes y contactos de agarre. "
         "Undo / Deshacer: blocking_undo.\n"
         "Sinónimos: preparar un bloqueo de animación, montar una toma con primitivas, ensayar un gesto.",
         Build, ann(draft_safe=True), build, timeout_s=105, capped=False),
    Tool("blocking_get", "Read a blocking package. Consulta un bloqueo y su articulación.\n"
         "Returns named parts, maps, layers and contact files by keyframe. "
         "Devuelve las partes, mapas, capas y archivos de contacto por fotograma.\n"
         "Sinónimos: consultar un bloqueo, ver las claves de la toma, recuperar capas y contactos.",
         Lookup, ann(True), get, capped=False),
    Tool("blocking_freeze", "Bake a take frame to static GLB. Congela un fotograma en GLB estático.\n"
         "Returns mesh, source hash and measured grips. Devuelve malla, huella de origen y agarres medidos. "
         "Optional print size and wall thicken the blocking's paper. El tamaño y el grosor opcionales preparan el papel para impresión. "
         "Undo / Deshacer: blocking_pose_undo.\n"
         "Sinónimos: congelar una pose, sacar una malla de un fotograma, exportar un GLB estático.",
         Freeze, ann(draft_safe=True), freeze, timeout_s=95, capped=False),
    Tool("blocking_pose_get", "Read a frozen pose by ID. Consulta una pose congelada por ID.\n"
         "Sinónimos: consultar una pose fija, recuperar la malla del fotograma, ver los agarres de la pose.",
         Lookup, ann(True), pose_get, capped=False),
    Tool("blocking_pose_undo", "Withdraw or restore a frozen pose. Retira o restaura una pose congelada.\n"
         "Sinónimos: deshacer la pose congelada, retirar la malla estática, restaurar la pose fija.",
         Undo, ann(idempotent=True), pose_undo),
    Tool("blocking_undo", "Withdraw or restore a blocking package. Retira o restaura un bloqueo.\n"
         "Returns the inverse call. Devuelve la operación inversa.\n"
         "Sinónimos: deshacer el bloqueo, retirar la toma, restaurar el paquete de animación.",
         Undo, ann(idempotent=True), undo),
]
