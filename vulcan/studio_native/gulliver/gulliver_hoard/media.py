"""Listing photos and turntable videos of a model: the browser renders them (the 3D viewer is the only renderer) and
uploads the result, this module validates and stores them next to the files of the model's current build."""

from __future__ import annotations

from typing import Any

from .errors import GulliverError
from .hoard_link.atomic import write_bytes_atomic

MAX_BYTES = 60 * 1024 * 1024
PNG_MAGIC = b"\x89PNG\r\n\x1a\n"
EBML_MAGIC = b"\x1a\x45\xdf\xa3"          # first bytes of every WebM / Matroska file

# kind -> (file name in the build folder, accepted content types, media type served)
KINDS: dict[str, tuple[str, tuple[str, ...], str]] = {
    "photo": ("photo.png", ("image/png",), "image/png"),
    "video": ("turntable.webm", ("video/webm",), "video/webm"),
}
NAMES = {spec[0]: kind for kind, spec in KINDS.items()}


def media_type(name: str) -> str:
    return KINDS[NAMES[name]][2]


def _base_type(content_type: str) -> str:
    return (content_type or "").split(";", 1)[0].strip().lower()


def save_media(services: Any, model_id: str, kind: str, data: bytes, content_type: str) -> dict[str, Any]:
    """Store ``data`` as the model's ``photo.png`` / ``turntable.webm`` (replacing any previous one).

    Returns ``{name, url, bytes}``. Raises :class:`GulliverError` for an unknown kind, a model without a build, a wrong
    content type or magic bytes, an empty body, or more than 60 MB."""
    if kind not in KINDS:
        raise GulliverError("invalid", f"Unknown media kind {kind!r}.", "Use 'photo' or 'video'.")
    name, accepted, _ = KINDS[kind]
    services.projects.get(model_id)                                    # 404 for an unknown model
    folder = services.projects.current_build_dir(model_id)
    if folder is None or not folder.is_dir():
        raise GulliverError("not_found", "This model has not been built yet.", "Build it first, then take the photo or video.")
    if _base_type(content_type) not in accepted:
        raise GulliverError("invalid", f"A {kind} must be sent as {accepted[0]}.", f"Send the raw body with Content-Type: {accepted[0]}.")
    if not data:
        raise GulliverError("invalid", "The body is empty.")
    if len(data) > MAX_BYTES:
        raise GulliverError("too_large", f"The {kind} is larger than {MAX_BYTES // (1024 * 1024)} MB.", "Use a smaller size or a shorter clip.")
    magic = PNG_MAGIC if kind == "photo" else EBML_MAGIC
    if not data.startswith(magic):
        raise GulliverError("invalid", f"The data is not a valid {'PNG image' if kind == 'photo' else 'WebM video'}.")
    write_bytes_atomic(folder / name, data)
    return {"name": name, "url": f"/api/models/{model_id}/media/{name}", "bytes": len(data)}


def list_media(services: Any, model_id: str) -> dict[str, Any]:
    """The photo / video stored for the current build: ``{model_id, media: [{kind, name, url, bytes}]}``."""
    services.projects.get(model_id)
    folder = services.projects.current_build_dir(model_id)
    items = []
    if folder is not None:
        for kind, (name, _, _) in KINDS.items():
            path = folder / name
            if path.is_file():
                items.append({"kind": kind, "name": name, "url": f"/api/models/{model_id}/media/{name}",
                              "bytes": path.stat().st_size})
    return {"model_id": model_id, "media": items}
