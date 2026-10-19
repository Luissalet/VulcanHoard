"""A visual scene contract: observed evidence before UV roles or height choices."""

import base64
import json
import time
from pathlib import Path
from typing import Literal
import httpx
from pydantic import BaseModel, Field, model_validator
from PIL import Image, ImageDraw
from .processing import png_bytes
from .model_io import check_response
from .vision import OLLAMA


class Part(BaseModel):
    key: str = Field(min_length=1, max_length=40)
    name: str = Field(max_length=80)
    appearance: str = Field(max_length=220)
    location: str = Field(max_length=120)
    count: int | None = Field(default=None, ge=1, le=32)
    representation: Literal["modeled", "painted", "mixed", "uncertain"]
    controlEvidence: str = Field(max_length=220)
    printRequirement: str = Field(default="", max_length=220)


class PhysicalRelation(BaseModel):
    part: str
    reference: str
    relation: Literal["covers", "inside", "borders", "continues", "separate"]
    evidence: str = Field(max_length=200)


class SceneContract(BaseModel):
    parts: list[Part] = Field(min_length=1, max_length=18)
    relations: list[PhysicalRelation] = Field(max_length=24)
    uncertainties: list[str] = Field(max_length=10)

    @model_validator(mode="after")
    def references_exist(self):
        keys = [p.key for p in self.parts]
        if len(set(keys)) != len(keys):
            raise ValueError("Repeated scene-part key")
        for r in self.relations:
            if r.part not in keys or r.reference not in keys or r.part == r.reference:
                raise ValueError("Physical relation must link two observed parts")
        return self


def apply_print_intent(data):
    """Perception is model output; preserving details without paint is user intent."""
    result = SceneContract.model_validate(data).model_dump()
    policies = {
        "modeled": "Conservar el volumen existente; no duplicarlo a partir del color o las sombras.",
        "painted": "Conservar el contorno pintado mediante diferencias de altura legibles sin color, siguiendo el perfil del artista.",
        "mixed": "Conservar la geometría existente y distinguir mediante alturas los detalles pintados que desaparecen en el control.",
        "uncertain": "Revisar otra vista antes de decidir qué detalle necesita altura; no inventar un volumen.",
    }
    for part in result["parts"]:
        part["printRequirement"] = policies[part["representation"]]
    result["printRequirementSource"] = "artist-unpainted-figure"
    if "generation" in data:
        result["generation"] = data["generation"]
    return result


PROMPT = """Understand this specific 3D object before assigning any texture regions.
The output will be an UNPAINTED physical figure. The contact sheet has two rows:
FRONT then BACK. LEFT = original colored model, RIGHT = uniform-height CONTROL,
with the same pre-existing geometry. Read these labels carefully. The control is
not a candidate heightmap; missing painted details in it are expected.
Identify what is actually present and the physical meaning of each feature. Ground
each part in COLOR + SHAPE + LOCATION, then compare whether its boundary/volume
already exists in the corresponding control. Do not infer geometry from a painted
highlight. A flat eye, lash, freckle or printed emblem must remain recognizable
without color; a modeled nose or garment volume must not be displaced again merely
because its texture has shading. Different colors can describe the same surface.
Distinguish actual eyes from circular marks elsewhere. Do not call soles eyebrows.
Describe layers such as a belt covering cloth, a buckle on a belt, a pupil inside
an eye, or an ink stroke inside an emblem ONLY when visible. A relation describes
what covers, contains, borders or continues another part; it is not automatically
an absolute height order. Existing geometric layers already supply their volume.
Do not guess the character name or invent unseen parts. Record uncertainty when
control resolution or occlusion prevents a decision. Count distinctive small marks
when possible. Keep separate parts that will need independent editing. No numeric
heights and no UV IDs. Return a compact JSON scene contract, with short Spanish
visible-evidence summaries, not a narration of your reasoning. Report observations
only: the application applies the artist's printing requirements separately.
JSON shape: {"parts":[{"key":"eyes","name":"Ojos","appearance":"color and shape",
"location":"where on the figure","count":2,"representation":"painted",
"controlEvidence":"what survives or disappears in the control"}],
"relations":[{"part":"part-key","reference":"other-key","relation":"inside",
"evidence":"visible support"}],"uncertainties":[]}.
Allowed representation: modeled, painted, mixed, uncertain.
Allowed relation: covers, inside, borders, continues, separate.
"""


def contact_sheet(scene):
    scene = Path(scene)
    sheet = Image.new("RGB", (1536, 1584), (65, 69, 73))
    draw = ImageDraw.Draw(sheet)
    for row, view in enumerate(["front", "back"]):
        for column, prefix in enumerate(["original", "control"]):
            with Image.open(scene / f"{prefix}-{view}.png") as source:
                image = source.convert("RGB")
                image.thumbnail((768, 768))
                sheet.paste(image, (column * 768 + (768 - image.width) // 2, row * 792 + 24))
            draw.text(
                (column * 768 + 12, row * 792 + 6),
                f"{view.upper()} / {prefix.upper()}",
                fill="white",
            )
    return sheet


def understand_scene(model, scene, evidence, progress=None):
    evidence = Path(evidence)
    evidence.mkdir(parents=True, exist_ok=True)
    sheet = contact_sheet(scene)
    sheet.save(evidence / "input.png")
    started = time.perf_counter()
    with httpx.Client(timeout=httpx.Timeout(180, connect=10), trust_env=False) as client:
        capability = client.post(OLLAMA + "/api/show", json={"model": model})
        capability.raise_for_status()
        thinking = "thinking" in capability.json().get("capabilities", [])
        body = {
            "model": model,
            "stream": True,
            "format": "json",
            "options": {"temperature": 0, "num_ctx": 16384, "num_predict": 6000},
            "messages": [
                {
                    "role": "user",
                    "content": PROMPT,
                    "images": [base64.b64encode(png_bytes(sheet)).decode()],
                }
            ],
        }
        if thinking:
            body["think"] = True
        (evidence / "request.json").write_text(
            json.dumps(
                {k: v for k, v in body.items() if k != "messages"} | {"prompt": PROMPT},
                ensure_ascii=False,
                indent=2,
            ),
            "utf-8",
        )
        content = ""
        reasoning_chars = 0
        last = {}
        notified = started
        with client.stream("POST", OLLAMA + "/api/chat", json=body) as response:
            check_response(response, evidence)
            for line in response.iter_lines():
                if not line:
                    continue
                last = json.loads(line)
                if last.get("error"):
                    raise RuntimeError(last["error"])
                message = last.get("message", {})
                reasoning_chars += len(message.get("thinking", ""))
                content += message.get("content", "")
                (evidence / "partial.txt").write_text(content, "utf-8")
                now = time.perf_counter()
                if progress and now - notified >= 15:
                    progress(
                        message="La IA contrasta las piezas y sus relaciones con la geometría original."
                    )
                    notified = now
                if now - started > 600:
                    raise TimeoutError("La comprensión de la figura superó diez minutos.")
        raw = {
            "content": content,
            "thinkingEnabled": thinking,
            "reasoningCharacters": reasoning_chars,
            "doneReason": last.get("done_reason"),
            "tokens": last.get("eval_count"),
            "seconds": round(time.perf_counter() - started, 2),
        }
        (evidence / "raw.json").write_text(json.dumps(raw, ensure_ascii=False, indent=2), "utf-8")
        if last.get("done_reason") == "length":
            raise ValueError(
                "El razonamiento agotó el presupuesto antes de completar el contrato visual."
            )
        result = apply_print_intent(json.loads(content))
        result["generation"] = {k: v for k, v in raw.items() if k != "content"}
        (evidence / "contract.json").write_text(
            json.dumps(result, ensure_ascii=False, indent=2), "utf-8"
        )
        return result
