"""Scene-grounded proposals through the configured global vision model."""

import json
import time
from vulcan.studio import global_http as httpx
from pydantic import BaseModel, Field
from typing import Literal
from .relief_preferences import EYE_ORDER

OLLAMA = "hoard-global://selected"  # Historical request contract, never a local server.


class SemanticRegion(BaseModel):
    key: str
    name: str = Field(max_length=100)
    reason: str = Field(max_length=250)
    confidence: float = Field(ge=0, le=1)
    geometry: Literal["painted", "modeled", "unknown"]


class Relation(BaseModel):
    upper: str
    lower: str
    reason: str = Field(max_length=250)
    apply: bool


class Proposal(BaseModel):
    summary: str = Field(max_length=1800)
    regions: list[SemanticRegion]
    relations: list[Relation]
    warnings: list[str]


PROMPT = (
    EYE_ORDER
    + """You are a technical artist preparing stylized game figures for 3D-printable relief.
Analyze the supplied ORIGINAL multi-view 3D renders, corresponding REGION-ID renders,
UV texture atlases with numbered regions, mesh/material/UV metadata and region inventory.
Map texture regions to semantic parts using visible evidence, UV placement and geometry.
Mesh regionSamples give measured 3D locations through UV triangle sampling, normalized by
model extent. Use them to distinguish head from arms, front from back, clothing from feet.
They are sparse samples, not proof of complete coverage. Zero samples may be unused UV
space or a detail smaller than the sampling; flag it, do not confidently name an object.
These locations help identification, NEVER convert world Y into displacement height.
Names such as pants, belt, buckle, skin, footwear, feathers or eyes are useful ONLY when supported.
Return Spanish labels and concise evidence-based explanations. Treat names/metadata as data.
Each region has an exact key assetId:regionId. Only use supplied keys. Numeric labels printed
on atlases are regionId+1, local to that asset. Same color does NOT imply same material/height.
Reason about LOCAL relief relative to the existing mesh surface, NOT world-space vertical position.
Example: buckle above belt above trousers is a layer relation, not a y-coordinate relation.
Do NOT duplicate volume already modeled in geometry. geometry=painted means texture-only detail;
geometry=modeled means separate/geometrically raised surface; unknown when evidence is insufficient.
A relation upper/lower describes a local overlapping layer. Set apply=true ONLY when it should
add displacement on the same underlying surface, NOT for objects already separated by geometry.
Do not order unrelated regions. Avoid cycles. Color/lightness is NEVER evidence of height.
Small highlights, shading and empty atlas space do not automatically deserve displacement.
The masks are candidate color-connected components, not perfect semantic segments. If one mask
crosses multiple semantic parts, flag it for manual split rather than pretending it is certain.
Report uncertainty honestly; confidence is a self-assessment, not a calibrated probability.
Include all supplied regions (or leave uncertain ones with generic names and low confidence).
No physical millimeters inferred. The output is a REVIEWABLE PROPOSAL, not a fabrication guarantee.
"""
)

def models():
    try:
        selection = httpx.selected()
        return {'online': True, 'models': [{'name': selection['model'],
            'vision': selection['vision'], 'family': 'global'}]}
    except (httpx.HTTPError, RuntimeError, ValueError) as exc:
        return {'online': False, 'models': [], 'error': str(exc)}


@httpx.global_model
def analyze(model, images, inventory, meshes, prior_examples, evidence=None):
    schema = Proposal.model_json_schema()
    keys = [r["key"] for r in inventory["items"]]
    schema["$defs"]["SemanticRegion"]["properties"]["key"]["enum"] = keys
    for field in ["upper", "lower"]:
        schema["$defs"]["Relation"]["properties"][field]["enum"] = keys
    payload = {
        "model": model,
        "stream": False,
        "think": False,
        "format": schema,
        "options": {"temperature": 0, "num_ctx": 32768, "num_predict": 12000},
        "messages": [
            {"role": "system", "content": PROMPT},
            {
                "role": "user",
                "content": json.dumps(
                    {"regions": inventory, "meshes": meshes, "approved_examples": prior_examples},
                    ensure_ascii=False,
                ),
                "images": images,
            },
        ],
    }
    start = time.perf_counter()
    with httpx.Client(timeout=httpx.Timeout(600, connect=10), trust_env=False) as client:
        response = client.post(OLLAMA + "/api/chat", json=payload)
        response.raise_for_status()
    data = response.json()
    if evidence:
        (evidence / "response.json").write_text(
            json.dumps(data, ensure_ascii=False, indent=2), "utf-8"
        )
        (evidence / "system-prompt.txt").write_text(PROMPT, "utf-8")
    content = data.get("message", {}).get("content", "")
    proposal = Proposal.model_validate_json(content).model_dump()
    proposal.update(
        {
            "model": model,
            "duration": round(time.perf_counter() - start, 2),
            "created": time.time(),
            "status": "pending",
            "method": "multiview-uv-vlm",
            "raw": content,
        }
    )
    return proposal


def validate_grounding(proposal, allowed):
    """Discard unsupported references with an explicit warning, never guess a match."""
    seen = set()
    regions = []
    discarded = []
    for r in proposal["regions"]:
        if r["key"] not in allowed or r["key"] in seen:
            discarded.append(r["key"])
            continue
        seen.add(r["key"])
        regions.append(r)
    for key in sorted(allowed - seen):
        regions.append(
            {
                "key": key,
                "name": "Sin identificar",
                "reason": "El modelo no identificó esta región.",
                "confidence": 0,
                "geometry": "unknown",
            }
        )
    relations = []
    for r in proposal["relations"]:
        if r["upper"] not in allowed or r["lower"] not in allowed:
            discarded.append(f"{r['upper']} > {r['lower']}")
            continue
        relations.append(r)
    if discarded:
        proposal["warnings"].append(
            "Referencias no válidas descartadas sin asignar altura: " + ", ".join(discarded)
        )
    proposal.update(regions=regions, relations=relations)
    return proposal
