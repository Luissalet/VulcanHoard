"""App-specific wrapper around the vendored `hoard_link` package.

Vulcan's Hoard uses exactly one model capability -- `llm`, for drafting
folder listings (title/description/tags) -- so this module stays small: it
turns `data/backend.json` plus the environment into a `LinkConfig` for this
app and builds the strict-JSON drafting prompt. `services.py` and
`folder_listings.py` stay about the app's own logic; this is where the
Hoard Link specifics live so the vendored package (`vulcan/hoard_link/`)
never has to be edited.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Mapping, Optional

from .hoard_link import LinkConfig

APP_ID = "vulcan"
BACKEND_FILE = "backend.json"

# The draft is short (title + one paragraph + 20 tags); a reasoning model's
# hidden thinking is stripped by Hoard Link but still counts against this
# budget, so it is generous rather than tight.
DRAFT_MAX_TOKENS = 1536

DRAFT_SYSTEM = (
    "You write marketplace listings for 3D-printable model packs (silhouette/outline "
    "packs sold as STL/3MF/OBJ files). You are given: the folder name, the file names "
    "and sizes of the models inside it, a style guide (naming grammar, known groups, "
    "base tags) and up to three example listings already approved for other folders in "
    "the same catalogue. Reply with strict JSON only, no markdown fences, no commentary: "
    '{"title": str, "description": str (English, at least 200 characters, no markdown), '
    '"tags": [array of exactly 20 unique, short, lowercase tags, no duplicates]}. '
    "Never invent a species, franchise, license or use case that is not implied by the "
    "folder name or the style guide; describe only what the file names, sizes and style "
    "guide say."
)


def backend_json_path(data_dir: Path) -> Path:
    return Path(data_dir) / BACKEND_FILE


def read_backend_json(data_dir: Path) -> dict[str, Any]:
    """The raw `backend.json` contents, or `{}` if absent or unreadable."""
    path = backend_json_path(data_dir)
    if not path.is_file():
        return {}
    try:
        raw = json.loads(path.read_text(encoding="utf-8-sig"))
    except ValueError:
        return {}
    return raw if isinstance(raw, dict) else {}


def load_link_config(data_dir: Path, env: Optional[Mapping[str, str]] = None) -> LinkConfig:
    import os

    return LinkConfig.load(backend_json_path(data_dir), env=env if env is not None else os.environ, app=APP_ID)


def build_draft_messages(material: dict[str, Any]) -> list[dict[str, str]]:
    """Chat messages for `folder_listing_draft`.

    `material` is the assembled context: folder name, files (name, size,
    extent_mm), count, template (style guide) and up to 3 examples (title,
    description, tags of already-approved listings in the same root) --
    see `folder_listings.assemble_material`.
    """
    return [
        {"role": "system", "content": DRAFT_SYSTEM},
        {"role": "user", "content": json.dumps(material, ensure_ascii=False)},
    ]


def parse_draft_json(text: str) -> dict[str, Any]:
    """Best-effort strict-JSON parse of a model's draft reply.

    Strips a leading/trailing markdown fence some models add despite being
    asked not to, then requires a JSON object with the three expected keys
    (whatever shape they are in -- validation of their content is
    `folder_listings.validate_listing`'s job, not this parser's).
    """
    cleaned = text.strip()
    if cleaned.startswith("```"):
        cleaned = cleaned.split("\n", 1)[1] if "\n" in cleaned else ""
        if cleaned.rstrip().endswith("```"):
            cleaned = cleaned.rstrip()[:-3]
    cleaned = cleaned.strip()
    start, end = cleaned.find("{"), cleaned.rfind("}")
    if start == -1 or end == -1 or end < start:
        raise ValueError("The model's reply is not JSON.")
    data = json.loads(cleaned[start : end + 1])
    if not isinstance(data, dict):
        raise ValueError("The model's reply is not a JSON object.")
    return data
