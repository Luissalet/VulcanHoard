"""Tools exposed to the assistant. One list drives /api/agent/* and mcp_server.py."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable

from pydantic import BaseModel, Field

from .search import SORT_NAMES, Filters
from .services import Services

AGENT_INSTRUCTIONS = """Vulcan's Hoard is the user's own library of 3D-printable models (STL, 3MF, OBJ files in folders on their PC), scanned locally: for every file it knows the size in mm, triangles, volume, whether the mesh is watertight, how many bodies it has, a thumbnail, tags, notes and an optional marketplace listing (title, description, tags, category).
Describe a model only from what the geometry data and the user say: dimensions, number of parts, watertightness, file format, folder and the user's own notes and tags. Never invent features, materials, history or use cases that are not in the data or in the conversation; ask the user when the listing needs them.
Start with models_search or models_recent to find the model, then model_info for everything about it (including its listing and duplicates). Report the model id back so later calls are unambiguous.
When asked for a listing ("genera la ficha"), write it in the user's voice and language (Spanish unless told otherwise), then save it with model_listing_set (source=assistant). Tags are lower-case, short, without duplicates. Saving the same listing twice is harmless.
model_tag, model_note, model_listing_set, models_add_root and models_rescan change data: call them only when the user asks. Scanning runs in the background: models_stats shows progress (files done / total, files per second, ETA). A folder with thousands of files takes minutes; answer with the partial numbers and say the scan is still running, or wait briefly and call models_stats again. Never read Vulcan's data folder, database or thumbnails directly with the shell or file tools: everything the app knows is available through these tools, and its database has a single writer."""


class Empty(BaseModel):
    pass


class SearchArgs(BaseModel):
    q: str = Field("", max_length=300, description="Words to look for in name, tags, notes, folder and listing text (prefix match, accent-insensitive). Empty lists everything.")
    format: str | None = Field(None, max_length=40, description="stl, obj, 3mf or a comma-separated list.")
    tag: str | None = Field(None, max_length=300, description="Tag(s) that must be present (comma-separated).")
    collection: str | None = Field(None, max_length=300, description="Folder-derived collection name (see models_stats).")
    root_id: int | None = Field(None, ge=1)
    watertight: bool | None = Field(None, description="Only watertight (true) or only open meshes (false).")
    has_listing: bool | None = Field(None, description="Only models with (true) or without (false) a listing.")
    dupes_only: bool = Field(False, description="Only models that are part of an exact-duplicate group.")
    bbox_min: float | None = Field(None, ge=0, description="Largest extent at least this many mm.")
    bbox_max: float | None = Field(None, ge=0, description="Largest extent at most this many mm.")
    sort: str = Field("relevance", description=f"One of {', '.join(SORT_NAMES)} (prefix - for descending). 'size' is file bytes; the biggest model in millimetres is sort='-extent' (largest side), the bulkiest is '-volume'.")
    limit: int = Field(20, ge=1, le=200)
    offset: int = Field(0, ge=0)


class InfoArgs(BaseModel):
    id: int | None = Field(None, ge=1, description="Model id from models_search.")
    path: str | None = Field(None, max_length=2000, description="Absolute path or path relative to its root, when the id is unknown.")


class IdArgs(BaseModel):
    id: int = Field(..., ge=1, description="Model id.")


class ListingSetArgs(BaseModel):
    id: int = Field(..., ge=1, description="Model id.")
    title: str | None = Field(None, max_length=300)
    description: str | None = Field(None, max_length=20000, description="Markdown allowed.")
    tags: list[str] | None = Field(None, max_length=100, description="Lower-case tags without duplicates.")
    category: str | None = Field(None, max_length=200)
    price_hint: str | None = Field(None, max_length=100, description="Free text, e.g. '4-6' or 'gratis'.")
    language: str | None = Field(None, max_length=10, description="ISO code of the listing language (default es).")


class TagArgs(BaseModel):
    id: int = Field(..., ge=1)
    add: list[str] = Field(default_factory=list, max_length=100, description="Tags to add (lower-cased, de-duplicated).")
    remove: list[str] = Field(default_factory=list, max_length=100, description="Tags to remove.")


class NoteArgs(BaseModel):
    id: int = Field(..., ge=1)
    notes: str = Field(..., max_length=20000, description="Replaces the model's notes (empty string clears them).")
    append: bool = Field(False, description="Append to the existing notes instead of replacing them.")


class DupesArgs(BaseModel):
    kind: str = Field("exact", pattern="^(exact|near)$", description="exact = identical bytes; near = same triangle count, volume and bbox within 1 %.")
    limit: int = Field(50, ge=1, le=500)


class AddRootArgs(BaseModel):
    path: str = Field(..., min_length=1, max_length=2000, description="Absolute folder path on the user's PC; it must exist.")
    name: str = Field("", max_length=200, description="Display name (defaults to the folder name).")
    watch: bool = Field(False, description="Rescan automatically when files change.")
    thumbnails: str = Field("all", pattern="^(all|top-level|none)$", description="all (default), top-level (only the root folder and its immediate subfolders get thumbnails) or none.")
    skip_small_bytes: int | None = Field(None, ge=0, description="Do not list files smaller than this many bytes (e.g. auto-exported layer meshes); omit for the server default.")


class RescanArgs(BaseModel):
    root_id: int | None = Field(None, ge=1, description="Root to rescan; omit to rescan every enabled root.")


class RecentArgs(BaseModel):
    n: int = Field(10, ge=1, le=200, description="How many of the newest models (by file modification date).")


@dataclass(frozen=True)
class Tool:
    name: str
    description: str
    input_model: type[BaseModel]
    annotations: dict[str, bool]
    run: Callable[[Services, Any], Any]


HIT_KEYS = ("id", "name", "format", "bbox", "triangles", "volume_cm3", "watertight", "bodies", "tags", "has_listing", "collection", "rel_path", "units_guess", "dupe_of", "status")


def _hit(model: dict) -> dict:
    hit = {k: model[k] for k in HIT_KEYS}
    hit["thumb_url"] = f"/api/models/{model['id']}/thumb" if model["has_thumb"] else None
    return hit


def run_search(services: Services, args: SearchArgs) -> dict:
    sort = args.sort if args.sort in SORT_NAMES else "relevance"
    filters = Filters(q=args.q, root_id=args.root_id, format=args.format, tag=args.tag, collection=args.collection, watertight=args.watertight,
                      has_listing=args.has_listing, dupes_only=args.dupes_only, bbox_min=args.bbox_min, bbox_max=args.bbox_max,
                      sort=sort, limit=args.limit, offset=args.offset)
    result = services.search.query(filters)
    hits = [_hit(m) for m in result["models"]]
    note = None if hits else "No model matches. Try fewer words, another tag, or models_stats to see which folders are scanned."
    return {"hits": hits, "count": len(hits), "total": result["total"], "offset": args.offset, "note": note}


def _find(services: Services, args: InfoArgs) -> dict:
    if args.id is None and not args.path:
        raise ValueError("Give an id or a path.")
    model = services.models.get(args.id) if args.id is not None else services.models.by_path(args.path)
    if model is None:
        raise LookupError("Model not found.")
    return model


def run_info(services: Services, args: InfoArgs) -> dict:
    model = _find(services, args)
    root = services.roots.get(model["root_id"])
    return {
        **model,
        "thumb_url": f"/api/models/{model['id']}/thumb" if model["has_thumb"] else None,
        "root": root.to_dict() if root else None,
        "listing": services.listings.get(model["id"]),
        "dupes": services.dupes.for_model(model),
        "albums": services.albums.for_model(model["id"]),
    }


def run_listing_get(services: Services, args: IdArgs) -> dict:
    if services.models.get(args.id) is None:
        raise LookupError(f"Model {args.id} does not exist.")
    listing = services.listings.get(args.id)
    return {"id": args.id, "listing": listing, "note": None if listing else "This model has no listing yet."}


def run_listing_set(services: Services, args: ListingSetArgs) -> dict:
    data = args.model_dump(exclude={"id"})
    listing = services.listings.set(args.id, data, source="assistant")
    return {"ok": True, "id": args.id, "listing": listing}


def run_tag(services: Services, args: TagArgs) -> dict:
    model = services.models.tag(args.id, args.add, args.remove)
    if model is None:
        raise LookupError(f"Model {args.id} does not exist.")
    return {"ok": True, "id": args.id, "tags": model["tags"]}


def run_note(services: Services, args: NoteArgs) -> dict:
    current = services.models.get(args.id)
    if current is None:
        raise LookupError(f"Model {args.id} does not exist.")
    notes = (current["notes"].rstrip() + "\n" + args.notes).strip() if args.append and current["notes"] else args.notes
    model = services.models.patch(args.id, {"notes": notes})
    return {"ok": True, "id": args.id, "notes": model["notes"]}


def run_stats(services: Services, _: Empty) -> dict:
    status = services.status()
    counts = services.stats.counts()
    worker = status["worker"]
    return {"counts": status["counts"], "by_format": counts["by_format"], "roots": counts["by_root"], "collections": counts["by_collection"][:100],
            "albums": counts["albums"], "scanning": worker["busy"], "current_root": worker["current"], "queue": worker["queued"],
            "progress": {k: {f: p[f] for f in ("phase", "files_done", "files_total", "jobs_done", "jobs_total", "rate", "eta_s", "error_count", "workers")} for k, p in worker["progress"].items()},
            "watching": status["watching"], "thumbnails": status["thumbnails"], "scan_workers": status["scan_workers"],
            "note": "Counts are cached for 5 s while a scan runs." if worker["busy"] else None}


def run_dupes(services: Services, args: DupesArgs) -> dict:
    groups = services.dupes.exact(args.limit) if args.kind == "exact" else services.dupes.near(args.limit)
    return {"kind": args.kind, "groups": groups, "count": len(groups),
            "note": None if groups else ("No exact duplicates." if args.kind == "exact" else "No near-duplicates suggested.")}


def run_add_root(services: Services, args: AddRootArgs) -> dict:
    root = services.add_root(args.name, args.path, None, None, args.watch, args.thumbnails, args.skip_small_bytes)
    return {"ok": True, "root": root.to_dict(), "note": "Scanning has started in the background; models_stats shows progress."}


def run_rescan(services: Services, args: RescanArgs) -> dict:
    if args.root_id is not None:
        queued = services.rescan(args.root_id)
        return {"ok": True, "queued": [args.root_id] if queued else [], "note": "Queued." if queued else "Already scanning or queued."}
    queued = [r.id for r in services.roots.list() if r.enabled and services.worker.enqueue(r.id)]
    return {"ok": True, "queued": queued, "note": f"{len(queued)} root(s) queued."}


def run_recent(services: Services, args: RecentArgs) -> dict:
    result = services.search.query(Filters(sort="-date", limit=args.n))
    return {"hits": [{**_hit(m), "file_modified_at": m["file_modified_at"]} for m in result["models"]], "count": len(result["models"])}


def _ann(read_only: bool, destructive: bool = False, idempotent: bool | None = None) -> dict[str, bool]:
    return {"readOnlyHint": read_only, "destructiveHint": destructive, "idempotentHint": read_only if idempotent is None else idempotent, "openWorldHint": False}


TOOLS: list[Tool] = [
    Tool("models_search", "Search the user's 3D model library by words (name, tags, notes, folder, listing text) with filters: format, tag, collection, watertight, has_listing, duplicates, size in mm. Returns id, name, format, bbox (mm), triangles, tags, has_listing and thumb_url per hit, paginated. First step for 'find my model of X'.\nSinónimos: buscar modelo, modelo 3D, STL, 3MF, OBJ, impresión 3D, archivo, figura, pieza, buscar en mis modelos, cuál era, carpeta de modelos, etiquetas, tamaño en mm, miniatura.", SearchArgs, _ann(True), run_search),
    Tool("model_info", "Everything about one model (by id or path): dimensions in mm, triangles, vertices, volume, surface, watertight, bodies (parts), units guess, folder, tags, notes, listing, exact and near duplicates, albums, thumb_url.\nSinónimos: modelo 3D, ficha, detalles, cuántos triángulos, tamaño en mm, medidas, volumen, es estanco, cuántas piezas, duplicados, STL, ruta del archivo, miniatura.", InfoArgs, _ann(True), run_info),
    Tool("model_listing_get", "The marketplace listing of a model (title, description, tags, category, price hint, language, source, updated_at), or none.\nSinónimos: ficha, descripción para la tienda, texto de la tienda, título, etiquetas, categoría, precio, ficha del modelo.", IdArgs, _ann(True), run_listing_get),
    Tool("model_listing_set", "Write the marketplace listing of a model (write): title, description (markdown), tags, category, price_hint, language. Fields left out keep their value; source is recorded as assistant. Idempotent: saving the same text twice changes nothing but the timestamp. Describe only what the geometry and the user say.\nSinónimos: genera la ficha, escribe la descripción para la tienda, ficha del modelo, título y descripción, etiquetas para la tienda, categoría, precio, redactar.", ListingSetArgs, _ann(False, False, True), run_listing_set),
    Tool("model_tag", "Add and/or remove tags on a model (write). Tags are lower-cased and de-duplicated; adding an existing tag is a no-op.\nSinónimos: etiquetar, añadir etiqueta, quitar etiqueta, etiquetas, tags, marcar, clasificar modelo.", TagArgs, _ann(False, False, True), run_tag),
    Tool("model_note", "Replace or append the user's notes on a model (write).\nSinónimos: nota, apuntar, anotar, notas del modelo, recordar sobre este modelo, comentario.", NoteArgs, _ann(False, False, False), run_note),
    Tool("models_stats", "Library statistics: models, bytes and triangles by format and by root folder, collections, listings, duplicates, errors, skipped files, scan queue with files/second and ETA per root, and folder watching. Cheap to call repeatedly (cached 5 s during a scan).\nSinónimos: estadísticas, cuántos modelos, cuántos STL, tamaño de la biblioteca, carpetas de modelos, está escaneando, progreso, resumen.", Empty, _ann(True), run_stats),
    Tool("models_dupes", "Duplicate groups: exact (identical files, same sha256) or near (same triangle count, volume and bounding box within 1 %; suggestions to review). Each group lists id, name, path, size and dimensions.\nSinónimos: duplicados, repetidos, archivos iguales, copias, modelos parecidos, limpiar duplicados, mismo STL.", DupesArgs, _ann(True), run_dupes),
    Tool("models_add_root", "Add a folder of models to the library (write). The path must exist on the user's PC; adding the same folder twice returns the existing root without rescanning. Options: thumbnails=all|top-level|none and skip_small_bytes to ignore tiny auto-generated meshes; exclude globs can be edited in the app (Carpetas). Scanning (metrics + thumbnails) starts in the background, in parallel worker processes. Only when the user asks.\nSinónimos: añadir carpeta, carpeta de modelos, escanear carpeta, indexar mis STL, nueva carpeta, agregar modelos.", AddRootArgs, _ann(False, False, True), run_add_root),
    Tool("models_rescan", "Queue a non-destructive rescan of one root or of every enabled root: only new or changed files are parsed again; deleted files are purged. Only when the user asks.\nSinónimos: reescanear, volver a escanear, actualizar biblioteca, refrescar carpeta, escanear de nuevo, reindexar.", RescanArgs, _ann(False, False, True), run_rescan),
    Tool("models_recent", "The n newest models by file modification date, with id, name, format, bbox, triangles, tags, has_listing and thumb_url.\nSinónimos: recientes, últimos modelos, lo último que he modelado, novedades, qué he añadido, modelos nuevos.", RecentArgs, _ann(True), run_recent),
]

TOOLS_BY_NAME = {tool.name: tool for tool in TOOLS}


def tool_catalog() -> list[dict]:
    return [
        {"name": t.name, "description": t.description, "annotations": t.annotations, "inputSchema": t.input_model.model_json_schema(by_alias=True)}
        for t in TOOLS
    ]


def call_tool(services: Services, name: str, arguments: dict | None) -> Any:
    tool = TOOLS_BY_NAME.get(name)
    if tool is None:
        raise KeyError(f"Unknown tool: {name}")
    args = tool.input_model.model_validate(arguments or {})
    return tool.run(services, args)
