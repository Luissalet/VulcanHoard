"""Render an articulated character against the camera and props of a retained blocking."""

from __future__ import annotations

import json
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Annotated
from uuid import uuid4

from pydantic import Field, FiniteFloat, model_validator

from . import blocking
from .articulation import service
from .asset_inspection import sha256_of
from .hoard_link.agentkit import Tool, ann

Digest = Annotated[str, Field(pattern=r"^[a-fA-F0-9]{64}$")]
Name = Annotated[str, Field(min_length=1, max_length=100)]
Point = tuple[FiniteFloat, FiniteFloat, FiniteFloat]
MANIFEST = "composition.json"


class Arm(blocking.Input):
    shoulder: Point = Field(description="Shoulder in source world metres. Hombro en metros en la escena de origen.")
    elbow: Point = Field(description="Elbow in source world metres. Codo en metros en la escena de origen.")
    contact: Point = Field(description="Point on the gripping mesh in the source. Punto de agarre en la malla de origen.")
    grip_part: Name = Field(description="Rigid distal arm mesh. Malla rígida del extremo del brazo.")
    radii: tuple[Annotated[float, Field(gt=0, le=1)], Annotated[float, Field(gt=0, le=1)]] = Field(
        description="Upper and lower arm influence radii on the body in source metres. "
        "Radios de influencia del brazo y antebrazo sobre el cuerpo, en metros de origen."
    )
    bend: Point = Field(description="Elbow direction in blocking space. Dirección del codo en el espacio del bloqueo.")


class Compose(blocking.Input):
    articulation_id: str = Field(pattern=r"^[a-f0-9]{32}$")
    source_sha256: Digest
    blocking_id: str = Field(pattern=r"^[a-f0-9]{32}$")
    blocking_scene_sha256: Digest
    blocking_recipe_sha256: Digest
    source_frame: int = Field(1, ge=0, le=100_000)
    frames: list[Annotated[int, Field(ge=0, le=96)]] = Field(min_length=1, max_length=8)
    size: int = Field(768, ge=128, le=768)
    body: Name
    sheet: Name = Field(description="Source map mesh with one packed image. Malla del mapa con una imagen empaquetada.")
    tube: Name
    yaw: blocking.Angle = Field(description="Rotate the rig into blocking space, about +Z. Giro del rig sobre +Z.")
    scale: float = Field(gt=0, le=20, description="Uniform rig scale. Escala uniforme del rig.")
    offset_xy: tuple[blocking.Coordinate, blocking.Coordinate] = (0, 0)
    tube_offset: blocking.Position = Field(
        (0, 0, 0), description="Fit the detailed tube to the character, in blocking metres. "
        "Ajusta el tubo detallado al personaje, en metros del bloqueo."
    )
    arm: Arm

    @model_validator(mode="after")
    def validate_selection(self):
        if self.frames != sorted(set(self.frames)):
            raise ValueError("Use distinct, increasing frames. / Usa fotogramas distintos y crecientes.")
        if len({self.body, self.sheet, self.tube, self.arm.grip_part}) != 4:
            raise ValueError("Choose distinct meshes. / Elige mallas distintas.")
        return self


def _root(services):
    return blocking._root(services) / "compositions"


def _receipt(folder):
    manifest = folder / MANIFEST
    if not manifest.is_file():
        raise ValueError("Composition not found. / No se ha encontrado la composición.")
    result = json.loads(manifest.read_text(encoding="utf-8"))
    for row in result["frames"]:
        row["maps"] = {label: str(folder / file) for label, file in row["maps"].items()}
    return {**result, "scene": str(folder / result["scene"]), "receipt": str(manifest),
            "undo": {"tool": "blocking_compose_undo", "arguments": {"id": result["id"]}}}


def build(services, args: Compose):
    data = services.config.data_dir.resolve()
    try:
        source = service.get(data, args.articulation_id)
    except LookupError as exc:
        raise ValueError(str(exc)) from exc
    take = blocking.get(services, blocking.Lookup(id=args.blocking_id))
    root = _root(services)
    root.mkdir(parents=True, exist_ok=True)
    package_id = uuid4().hex
    with TemporaryDirectory(prefix=".compose-", dir=root) as scratch:
        scratch = Path(scratch)
        rig, scene, recipe = (scratch / name for name in ("rig.blend", "take.blend", "take.json"))
        inputs = {
            "editable": service.pin_file(Path(source["files"]["scene"]), rig, args.source_sha256),
            "blocking_scene": service.pin_file(Path(take["files"]["scene"]), scene, args.blocking_scene_sha256),
            "blocking_recipe": service.pin_file(Path(take["files"]["recipe"]), recipe, args.blocking_recipe_sha256),
        }
        if rigged := source["files"].get("rigged"):
            inputs["rigged_glb"] = sha256_of(Path(rigged))

        pinned_take = json.loads(recipe.read_text(encoding="utf-8"))
        keys = {key["frame"]: key for key in pinned_take["poses"]}
        if not set(args.frames) <= keys.keys():
            raise ValueError("Choose retained keyframes. / Elige fotogramas clave conservados.")
        holders = {
            tuple(side for side in ("left", "right") if key[side + "_hand"] in ("sheet_left", "sheet_right"))
            for key in keys.values()
        }
        if (len(holders) != 1 or len(next(iter(holders))) != 1
                or not {"sheet", "tube", "step_1"} <= set(pinned_take["parts"])):
            raise ValueError("Use a blocking with one sheet holder, tube and stairs. / "
                             "Usa un bloqueo con una mano en la hoja, tubo y escalera.")
        holder, = next(iter(holders))
        settings = scratch / "settings.json"
        settings.write_text(json.dumps({
            "controls": args.model_dump(mode="json"),
            "take": {key: pinned_take[key] for key in ("camera", "fps", "gesture", "poses", "parts")},
            "holder": holder,
        }), encoding="utf-8")
        output = scratch / "output"
        output.mkdir()
        manifest = output / MANIFEST
        worker = Path(__file__).with_name("blender_blocking_compose.py")
        service.run_worker(worker, (rig, scene, settings, output), manifest, timeout=220)
        receipt = json.loads(manifest.read_text(encoding="utf-8"))
        files = [receipt["scene"], *(file for row in receipt["frames"] for file in row["maps"].values())]
        if [row["frame"] for row in receipt["frames"]] != args.frames or any(
                not (output / file).is_file() or not (output / file).stat().st_size for file in files):
            raise RuntimeError("Composition output incomplete. / La composición está incompleta.")
        receipt.update(id=package_id, articulation_id=args.articulation_id, blocking_id=args.blocking_id,
                       inputs=inputs, controls=args.model_dump(mode="json"),
                       artifacts={file: sha256_of(output / file) for file in files})
        manifest.write_text(json.dumps(receipt), encoding="utf-8")
        output.rename(root / package_id)
    return read(services, blocking.Lookup(id=package_id))


def read(services, args: blocking.Lookup):
    return _receipt(_root(services) / args.id)


def undo(services, args: blocking.Undo):
    return blocking._withdraw(_root(services), args, MANIFEST, "blocking_compose_undo")


TOOLS = [
    Tool("blocking_compose",
         "Render a detailed rig on pinned blocking keys. Compone un rig detallado sobre claves de un bloqueo fijado.\n"
         "Uses the retained camera, stairs and single-hand map gesture. Returns colour and depth. "
         "Usa la cámara, escalera y gesto de una mano conservados. Devuelve color y profundidad. "
         "Undo / Deshacer: blocking_compose_undo.\n"
         "Sinónimos: componer una toma detallada, renderizar un rig sobre un bloqueo.",
         Compose, ann(draft_safe=True), build, timeout_s=240, capped=False),
    Tool("blocking_compose_get",
         "Read a detailed composition by ID. Consulta una composición detallada por ID.\n"
         "Sinónimos: recuperar color y profundidad, consultar una toma compuesta.",
         blocking.Lookup, ann(True), read, capped=False),
    Tool("blocking_compose_undo",
         "Withdraw or restore a detailed composition. Retira o restaura una composición detallada.\n"
         "Sinónimos: deshacer la composición, restaurar un render por claves.",
         blocking.Undo, ann(idempotent=True), undo),
]
