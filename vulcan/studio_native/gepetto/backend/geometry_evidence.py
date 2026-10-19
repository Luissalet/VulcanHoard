"""Preserve small modeled parts when texture colors only shade existing geometry."""

import base64
import json
import time
from pathlib import Path
import httpx
import numpy as np
from PIL import Image
from pydantic import BaseModel, Field
from .model_io import check_response
from .processing import png_bytes
from .region_evidence import project_regions
from .vision import OLLAMA


class ShapeComparison(BaseModel):
    missingPaintedShapes: list[str] = Field(max_length=12)
    alreadyModeledShapes: list[str] = Field(max_length=12)
    reason: str = Field(min_length=10, max_length=750)


def compact_part(item, geometry):
    points = np.asarray(item.get("world_triangles", []))
    return bool(
        points.size and np.ptp(points.reshape(-1, 3), axis=0).max() <= geometry["size"] * 0.35
    )


PROMPT = """We are converting this colored figure into an UNPAINTED physical print.
Image 1 shows the original material. Image 2 shows the SAME model with uniform gray
texture. List the DISCRETE PAINTED SHAPES that vanish in image 2: those markings
need heightmap relief so they remain recognizable without color. Flat painted eyes,
pupils, eyelashes, freckles, clothing emblems or patterns MUST count as missing if
the gray surface loses their outline. By contrast, a volume, rim or hole already
visible as geometry in image 2 needs no added texture displacement. Ignore ordinary
smooth lighting gradients, but never discard an intentional painted shape merely
because it was originally flat. Output JSON with these arrays first:
{"missingPaintedShapes":["specific missing feature names"],
"alreadyModeledShapes":["shapes retained in the gray model"],
"reason":"short visual comparison"}.
Use empty missingPaintedShapes only when the original has no discrete painted
markings absent from the gray geometry."""
PROMPT = " ".join(PROMPT.split())


def preserve_modeled_part(
    model, item, inventory, labels, regions, geometry, original, control, evidence
):
    if not compact_part(item, geometry):
        return None
    projected = project_regions(inventory, item["material"], labels, geometry, size=original.size)
    visible = set(projected.ravel()) - {-1}
    coverage = float(np.isin(labels, list(visible)).sum() / max(1, (labels >= 0).sum()))
    # A front view is not evidence about a large unseen area of the atlas.
    if not visible or coverage < 0.95:
        return None
    evidence = Path(evidence)
    evidence.mkdir(parents=True, exist_ok=True)
    y, x = np.nonzero(projected >= 0)
    box = (
        max(0, int(x.min()) - 16),
        max(0, int(y.min()) - 16),
        min(original.width, int(x.max()) + 17),
        min(original.height, int(y.max()) + 17),
    )
    images = [original.crop(box), control.resize(original.size).crop(box)]
    for index, image in enumerate(images):
        image.save(evidence / f"input-{index}.png")
    start = time.perf_counter()
    with httpx.Client(timeout=180, trust_env=False) as client:
        response = client.post(
            OLLAMA + "/api/chat",
            json={
                "model": model,
                "stream": False,
                "think": False,
                "format": "json",
                "options": {"temperature": 0, "num_ctx": 8192, "num_predict": 450},
                "messages": [
                    {
                        "role": "user",
                        "content": PROMPT,
                        "images": [base64.b64encode(png_bytes(im)).decode() for im in images],
                    }
                ],
            },
        )
        check_response(response, evidence)
        raw = response.json()
    comparison = ShapeComparison.model_validate_json(raw["message"]["content"]).model_dump()
    record = comparison | {
        "visibleAtlasFraction": coverage,
        "model": model,
        "prompt": PROMPT,
        "seconds": round(time.perf_counter() - start, 2),
    }
    (evidence / "comparison.json").write_text(
        json.dumps(record, ensure_ascii=False, indent=2), "utf-8"
    )
    (evidence / "raw.json").write_text(json.dumps(raw, ensure_ascii=False, indent=2), "utf-8")
    if comparison["missingPaintedShapes"] or not comparison["alreadyModeledShapes"]:
        return None
    return {
        "description": comparison["reason"],
        "surfaces": [
            {
                "classes": [r["id"] for r in regions],
                "name": "Volumen ya modelado",
                "role": "modeled",
                "height": 128,
                "reason": comparison["reason"],
            }
        ],
        "warnings": [],
        "method": "compact-geometry-control",
        "model": model,
        "seconds": record["seconds"],
        "geometryEvidence": record,
    }
