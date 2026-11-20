"""Resolve a pinned dossier object into a source-space clipped panel."""

import json
import math
import re
from hashlib import sha256
from pathlib import Path

import numpy as np

from ..hoard_link.agentkit import AppError

SIZE = re.compile(r"(?<![\w.,-])(\d+(?:[.,]\d+)?)\s*[x×]\s*(\d+(?:[.,]\d+)?)\s*(mm|cm|m)\b", re.IGNORECASE)
METRES = {"mm": 0.001, "cm": 0.01, "m": 1.0}
CORNERS = {
    "inferior izquierda": "bottom-left", "inferior derecha": "bottom-right",
    "superior izquierda": "top-left", "superior derecha": "top-right",
    "lower left": "bottom-left", "lower right": "bottom-right",
    "upper left": "top-left", "upper right": "top-right",
    "bottom left": "bottom-left", "bottom right": "bottom-right",
    "top left": "top-left", "top right": "top-right",
}
SPANISH_CORNER = re.compile(r"le falta la esquina ((?:inferior|superior) (?:izquierda|derecha)),? (?:rasgada|cortada) en diagonal")
ENGLISH_CORNER = re.compile(r"(?:the )?((?:lower|upper|bottom|top) (?:left|right)) corner (?:diagonally (?:cut|torn)|(?:cut|torn) diagonally)")
CLIP_FRACTION = 0.15


def read_fields(spec):
    path = Path(spec["path"])
    if not path.is_absolute() or path.suffix.lower() != ".json":
        raise AppError("invalid", "Use an absolute dossier JSON path. / Usa la ruta absoluta al JSON del expediente.")
    try:
        data = path.read_bytes()
    except OSError as exc:
        raise AppError("invalid", "Cannot read the dossier file. / No se puede leer el fichero del expediente.") from exc
    revision = sha256(data).hexdigest()
    if revision != spec["revision"].lower():
        raise AppError("conflict", "Dossier revision changed. / Ha cambiado la revisión del expediente.")
    try:
        dossier = json.loads(data)
    except ValueError as exc:
        raise AppError("invalid", "Invalid dossier JSON. / JSON del expediente no válido.") from exc
    objects = dossier.get("objects") if isinstance(dossier, dict) else None
    if not isinstance(objects, list):
        raise AppError("invalid", "The dossier needs a list of objects. / El expediente necesita una lista de objetos.")
    matches = [item for item in objects if isinstance(item, dict) and item.get("ref") == spec["ref"]]
    if len(matches) != 1:
        raise AppError("invalid", "The object reference must identify one dossier object. / La referencia debe identificar un solo objeto del expediente.")
    fields = matches[0].get("fields")
    if not isinstance(fields, dict):
        raise AppError("invalid", "The dossier object needs fields. / El objeto del expediente necesita campos.")
    return revision, fields


def dimensions(fields):
    value = fields.get("Tamaño", fields.get("Size"))
    matches = SIZE.findall(value) if isinstance(value, str) else []
    if len(matches) == 1:
        width, height, unit = matches[0]
        result = tuple(float(side.replace(",", ".")) * METRES[unit.lower()] for side in (width, height))
        if all(math.isfinite(side) and side > 0 for side in result):
            return result
    raise AppError("invalid", "Size must contain one positive width × height in mm, cm or m. / Tamaño debe contener una anchura × altura positiva en mm, cm o m.")


def clipped_outline(width, height, corner, clip):
    corners = {
        "bottom-left": (0, 0), "top-left": (0, height),
        "top-right": (width, height), "bottom-right": (width, 0),
    }
    points = list(corners.values())
    index = points.index(corners[corner])
    x, y = points[index]
    before, after = points[index - 1], points[(index + 1) % len(points)]

    def towards(point):
        target_x, target_y = point
        return x + clip * np.sign(target_x - x), y + clip * np.sign(target_y - y)

    points[index:index + 1] = [towards(before), towards(after)]
    return np.asarray(points)


def resolve_panel(panel):
    spec = panel["dossier_object"]
    revision, fields = read_fields(spec)
    width_m, height_m = dimensions(fields)
    defect = fields.get("Defecto", fields.get("Defect"))
    text = " ".join(defect.casefold().split()).rstrip(".") if isinstance(defect, str) else ""
    match = SPANISH_CORNER.fullmatch(text) or ENGLISH_CORNER.fullmatch(text)
    if not match:
        raise AppError("invalid", "Defect must specify one diagonally clipped corner. / Defecto debe indicar una esquina cortada en diagonal.")
    corner = CORNERS[match[1]]
    finish = fields.get("Material")
    if not isinstance(finish, str) or not finish.strip():
        raise AppError("invalid", "The dossier object needs a Material field. / El objeto del expediente necesita el campo Material.")

    scale = spec["scale"]
    width, height = width_m * scale, height_m * scale
    if not all(math.isfinite(side) and side > 0 for side in (width, height)):
        raise AppError("invalid", "Size at this scale must be finite and positive. / El tamaño a esta escala debe ser finito y positivo.")
    right, up = np.asarray([spec["right"], spec["up"]])
    lengths = [math.hypot(*axis) for axis in (right, up)]
    if not all(math.isfinite(length) and length > 0 for length in lengths):
        raise AppError("invalid", "Panel axes must have a finite, nonzero length. / Los ejes del panel deben tener una longitud finita y no nula.")
    right, up = right / lengths[0], up / lengths[1]
    if abs(np.dot(right, up)) > 1e-6:
        raise AppError("invalid", "Panel axes must be perpendicular. / Los ejes del panel deben ser perpendiculares.")
    clip = CLIP_FRACTION * min(width, height)
    points = clipped_outline(width, height, corner, clip)
    offsets = points - np.asarray(spec["grip_uv"]) * [width, height]
    outline = np.asarray(panel["pivot"]) + offsets @ np.stack((right, up))
    if not np.isfinite(outline).all():
        raise AppError("invalid", "Panel outline must be finite. / El contorno del panel debe ser finito.")

    return {**panel, "outline": outline.tolist(), "source_object": {
        "ref": spec["ref"], "revision": revision, "finish": finish,
        "dimensions_m": [width_m, height_m], "scale": scale,
        "panel_dimensions_m": [width, height], "corner": corner, "clip_m": clip,
    }}
