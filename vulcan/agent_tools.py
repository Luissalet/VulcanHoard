"""Tools exposed to the assistant. One list drives /api/agent/* and mcp_server.py."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from .hoard_link.agentkit import Empty, Tool, ann, call_tool as run_tool, tool_catalog as catalog_of
from .organize import plan_view
from .search import SORT_NAMES, Filters
from .services import Services

AGENT_INSTRUCTIONS = """Vulcan's Hoard is the user's own library of 3D-printable models (STL, 3MF, OBJ files in folders on their PC), scanned locally: for every file it knows the size in mm, triangles, volume, whether the mesh is watertight, how many bodies it has, a thumbnail, tags, notes and an optional marketplace listing (title, description, tags, category).
Describe a model only from what the geometry data and the user say: dimensions, number of parts, watertightness, file format, folder and the user's own notes and tags. Never invent features, materials, history or use cases that are not in the data or in the conversation; ask the user when the listing needs them.
Start with models_search or models_recent to find the model, then model_info for everything about it (including its listing and duplicates). Report the model id back so later calls are unambiguous.
For "models with a similar shape" use models_similar with that id. It compares closed geometry even after remeshing or rotation; its scores are suggestions, never proof of duplicates.
When asked for a listing ("genera la ficha"), write it in the user's voice and language (Spanish unless told otherwise), then save it with model_listing_set (source=assistant). Tags are lower-case, short, without duplicates. Saving the same listing twice is harmless.
model_tag, model_note, model_listing_set, models_add_root and models_rescan change data: call them only when the user asks. model_import_file indexes one file right now (other apps call it when they finish an export); listings_export_catalog reads every listing in the shape the shop catalogue of Mercator imports. Scanning runs in the background: models_stats shows progress (files done / total, files per second, ETA). A folder with thousands of files takes minutes; answer with the partial numbers and say the scan is still running, or wait briefly and call models_stats again. Never read Vulcan's data folder, database or thumbnails directly with the shell or file tools: everything the app knows is available through these tools, and its database has a single writer.

The user also sells these models on a marketplace in a different convention: each PRODUCT is a FOLDER of models (not one model), and its listing (title in English, an English description of 200+ characters, exactly 20 unique lower-case tags) lives in that folder as cults3d.json. "ficha/listing para la carpeta X" or "ficha de X" means folder_listing_get/folder_listing_set on that folder, never model_listing_set. "fichas que faltan" or "qué carpetas no tienen ficha" means folder_listings(status=none). Draft one or several with folder_listing_draft (it runs in the background with a local model and returns progress; if no model backend is available it returns the assembled material instead so you can write the listing yourself and save it with folder_listing_set). Always run folder_listing_check after writing or importing a listing by hand before calling it approved. folder_listings_export produces a CSV/Markdown/JSON file in data/exports for uploading to the marketplace.
To organise a folder of loose files and folders into one folder per group from a list the user pasted (\"una carpeta por línea evolutiva\", \"agrupa estos por serie\"), never move files one by one with the shell: call collection_plan with the folder and the pasted list, show the user the summary (groups, to_move, unmatched, ambiguous, conflicts), fix what they point out (aliases, a different target_template, the list itself) and plan again, and only after they agree call collection_apply(plan_id). It never deletes or overwrites; collection_undo reverts the last apply. After an apply the root is rescanned in the background: wait for models_stats to show it idle. Then \"fichas de todas\" / \"crea las fichas de todas las carpetas\" is one call: sheets_batch (default mode skeleton needs no model; use dry_run first on a big root); refine the drafts afterwards with folder_listing_set and check them with folder_listing_check. Use sheets_batch mode=model only when the user asks for the local model to write them."""


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
    distinct: bool | None = Field(None, description="Fold exact duplicates (same file content) into one hit with a 'copies' count. Default: on when sorting by size, extent, volume, triangles or date (a 'top N' question), off for relevance and name.")


class InfoArgs(BaseModel):
    id: int | None = Field(None, ge=1, description="Model id from models_search.")
    path: str | None = Field(None, max_length=2000, description="Absolute path or path relative to its root, when the id is unknown.")


class IdArgs(BaseModel):
    id: int = Field(..., ge=1, description="Model id.")


class SimilarArgs(IdArgs):
    limit: int = Field(10, ge=1, le=50)
    minimum_score: float = Field(0.75, ge=0.5, le=1.0)


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


class FolderListingsArgs(BaseModel):
    root_id: int | None = Field(None, ge=1, description="Restrict to one root; omit to list across every root.")
    status: str | None = Field(None, pattern="^(none|draft|checked|approved)$", description="Only listings in this status.")
    limit: int = Field(100, ge=1, le=1000)


class FolderPathArgs(BaseModel):
    root_id: int | None = Field(None, ge=1, description="Root id; omit when path is an absolute folder path.")
    path: str = Field("", max_length=2000, description="Folder path relative to the root (empty string for the root itself), or an absolute path.")


class FolderListingSetArgs(BaseModel):
    root_id: int | None = Field(None, ge=1, description="Root id; omit when path is an absolute folder path.")
    path: str = Field("", max_length=2000, description="Folder path relative to the root (empty string for the root itself), or an absolute path.")
    title: str | None = Field(None, max_length=120, description="English, non-empty, at most 120 characters.")
    description: str | None = Field(None, max_length=20000, description="English, at least 200 characters, no markdown.")
    tags: list[str] | None = Field(None, max_length=40, description="Exactly 20 unique lower-case tags when finalising the listing.")
    status: str = Field("draft", pattern="^(draft|checked|approved)$", description="Workflow status to record; run folder_listing_check before approving.")


class FolderListingCheckArgs(BaseModel):
    root_id: int | None = Field(None, ge=1, description="Root id; omit to check across every root (with path omitted too).")
    path: str | None = Field(None, max_length=2000, description="One folder; omit to check every folder listing (in root_id, or every root).")


class FolderListingDraftArgs(BaseModel):
    root_id: int | None = Field(None, ge=1, description="Restrict to one root; omit to match across every root.")
    path: str = Field("", max_length=2000, description="Exact folder path or a glob pattern (e.g. 'Contornos pokemon/*'); empty matches folders still missing a listing.")
    limit: int = Field(20, ge=1, le=200, description="Maximum number of folders to draft in this batch.")
    overwrite: bool = Field(False, description="Redraft folders that already have a draft/checked/approved listing.")


class FolderListingsExportArgs(BaseModel):
    root_id: int = Field(..., ge=1)
    format: str = Field("csv", pattern="^(csv|md|json)$")
    status: str | None = Field(None, pattern="^(none|draft|checked|approved)$", description="Only export listings in this status.")


class MatchArgs(BaseModel):
    model_config = ConfigDict(extra="forbid")

    case_insensitive: bool = Field(True, description="Ignore upper/lower case.")
    accent_insensitive: bool = Field(True, description="Ignore accents (Pokémon = pokemon).")
    ignore_separators: bool = Field(True, description="Ignore punctuation, underscores, hyphens, dots and spaces (Mr. Mime = mr_mime = MrMime).")
    ignore_number_prefix: bool = Field(True, description="Ignore a leading number in item names ('0001_bulbasaur' matches 'Bulbasaur').")
    allow_contains: bool = Field(True, description="Also match whole-word occurrences inside a longer name ('Bulbasaur outline v2'); the longest member wins, equal ones are reported as ambiguous.")
    include_files: bool = Field(True, description="Sort loose files.")
    include_folders: bool = Field(True, description="Sort sub-folders.")
    aliases: dict[str, str] = Field(default_factory=dict, description="{alias as it appears in a name: member name in the list}, for spellings the rules cannot bridge.")


class CollectionPlanArgs(BaseModel):
    root: str = Field(..., min_length=1, max_length=2000, description="Absolute path of the folder whose immediate children (files and folders) are sorted.")
    reference: str = Field(..., min_length=1, max_length=400000, description="The list pasted by the user, one group per line, members separated by >, ->, → , or ' - ', optionally with numbers: '001 Bulbasaur > Ivysaur > Venusaur'. 'Name: a > b' names the group explicitly (default: the first member). Or CSV with header group,member,number. Or the path of such a .txt/.csv file.")
    reference_format: str = Field("auto", pattern="^(auto|lines|csv)$", description="auto detects a CSV header (group,member); force lines or csv when needed (a headerless CSV needs csv).")
    target_template: str = Field("{number} {group}", max_length=200, description="Name of each group folder. Placeholders: {number} (zero-padded to the width used in the list), {group}, {first}, {last}, {count}, {index}.")
    match: MatchArgs = Field(default_factory=MatchArgs, description="How names are compared.")
    number_width: int | None = Field(None, ge=1, le=12, description="Force the zero-padding width of {number}; default = the widest number written in the list.")


class CollectionApplyArgs(BaseModel):
    plan_id: str = Field(..., min_length=4, max_length=64, description="Id returned by collection_plan.")


class CollectionUndoArgs(BaseModel):
    apply_id: str | None = Field(None, max_length=64, description="Id returned by collection_apply; omit to undo the most recent apply that is not undone yet.")
    remove_created_folders: bool = Field(True, description="After moving the items back, remove the group folders that the apply created if they are empty again (never a folder with anything inside).")


class SheetsBatchArgs(BaseModel):
    root_id: int | None = Field(None, ge=1, description="Root id; omit when path is an absolute folder under a known root.")
    path: str = Field("", max_length=2000, description="Only folders (or models) under this relative path or glob (e.g. 'Contornos pokemon/*'); absolute folder path allowed when root_id is omitted; empty = the whole root.")
    scope: str = Field("folders", pattern="^(folders|models)$", description="folders = one marketplace sheet per folder (cults3d.json); models = one listing per model file.")
    mode: str = Field("skeleton", pattern="^(skeleton|model)$", description="skeleton (default) = deterministic draft from the scanned data, no language model; model = queue the background local-model drafting (folders only).")
    refresh: bool = Field(False, description="Also rewrite sheets that are still drafts (skeleton listings written by the assistant for models).")
    overwrite: bool = Field(False, description="Also replace checked/approved folder sheets and manual model listings. Off by default.")
    dry_run: bool = Field(False, description="Report what would be created, refreshed or skipped without writing anything.")
    limit: int = Field(1000, ge=1, le=5000, description="Maximum sheets written in this call.")
    language: str = Field("en", pattern="^(en|es)$", description="Language of skeleton model listings (folder sheets are always English, the marketplace convention).")

class ImportFileArgs(BaseModel):
    path: str = Field(..., min_length=1, max_length=2000, description="Absolute path of an STL, 3MF or OBJ file that exists on this PC.")
    source_ref: str | None = Field(None, max_length=500, description="hoard:// reference of the record it comes from (e.g. hoard://plato/export/12); kept on the model and linked in the hub.")


class CatalogExportArgs(BaseModel):
    folder: str | None = Field(None, max_length=2000, description="Only listings in this folder and below: absolute path, or path relative to a root. Omit for every listing.")


HIT_KEYS = ("id", "name", "format", "bbox", "triangles", "volume_cm3", "watertight", "bodies", "tags", "has_listing", "collection", "rel_path", "units_guess", "dupe_of", "status")


def _hit(model: dict) -> dict:
    hit = {k: model[k] for k in HIT_KEYS}
    hit["thumb_url"] = f"/api/models/{model['id']}/thumb" if model["has_thumb"] else None
    return hit


RANKING_SORTS = ("size", "extent", "volume", "triangles", "date")
FOLD_SCAN_MAX = 2000  # rows read to fill one folded page


def run_search(services: Services, args: SearchArgs) -> dict:
    sort = args.sort if args.sort in SORT_NAMES else "relevance"
    distinct = args.distinct if args.distinct is not None else sort.lstrip("-") in RANKING_SORTS
    common = dict(q=args.q, root_id=args.root_id, format=args.format, tag=args.tag, collection=args.collection, watertight=args.watertight,
                  has_listing=args.has_listing, dupes_only=args.dupes_only, bbox_min=args.bbox_min, bbox_max=args.bbox_max, sort=sort)
    if not distinct:
        result = services.search.query(Filters(**common, limit=args.limit, offset=args.offset))
        hits = [_hit(m) for m in result["models"]]
        note = None if hits else "No model matches. Try fewer words, another tag, or models_stats to see which folders are scanned."
        return {"hits": hits, "count": len(hits), "total": result["total"], "offset": args.offset, "note": note}

    # «The three biggest» should be three different models, not one file copied three times:
    # read in sort order, keep the first copy of each content hash, count the rest.
    wanted = args.offset + args.limit
    groups: dict[str, dict] = {}
    order: list[str] = []
    total = 0
    page, read = 200, 0
    while len(order) < wanted and read < FOLD_SCAN_MAX:
        result = services.search.query(Filters(**common, limit=page, offset=read))
        total = result["total"]
        rows = result["models"]
        if not rows:
            break
        for m in rows:
            key = m.get("sha256") or f"id:{m['id']}"
            if key in groups:
                groups[key]["copies"] += 1
                continue
            groups[key] = {**_hit(m), "copies": 1}
            order.append(key)
        read += len(rows)
        if read >= total:
            break
    hits = [groups[k] for k in order[args.offset:wanted]]
    note = None if hits else "No model matches. Try fewer words, another tag, or models_stats to see which folders are scanned."
    return {"hits": hits, "count": len(hits), "total": total, "offset": args.offset, "distinct": True,
            "note": note or "Exact duplicates folded: 'copies' counts the identical files behind each hit (distinct=false lists every file)."}


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


def run_similar(services: Services, args: SimilarArgs) -> dict:
    model = services.models.get(args.id)
    if model is None:
        raise LookupError(f"Model {args.id} does not exist.")
    matches = services.dupes.similar_shapes(model, args.limit, args.minimum_score)
    return {"model_id": args.id, "matches": matches, "count": len(matches),
            "note": "Filled-shape comparison supports closed meshes only. Scores suggest resemblance, not duplicate status."}


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


def _resolve_folder(services: Services, root_id: int | None, path: str) -> tuple[int, str]:
    """(root_id, rel_path) from an explicit root_id + relative path, or from an absolute folder path alone."""
    from pathlib import Path

    path = (path or "").strip().replace("\\", "/")
    if root_id is not None:
        if services.roots.get(root_id) is None:
            raise LookupError(f"Root {root_id} does not exist.")
        return root_id, path.strip("/")
    if not path:
        raise ValueError("Give root_id + path (relative to the root), or an absolute folder path under a known root.")
    resolved = Path(path).expanduser().resolve()
    for root in services.roots.list():
        try:
            rel = resolved.relative_to(Path(root.path)).as_posix()
        except ValueError:
            continue
        return root.id, ("" if rel == "." else rel)
    raise ValueError("Give root_id + path (relative to the root), or an absolute folder path under a known root.")


def run_folder_listings(services: Services, args: FolderListingsArgs) -> dict:
    rows = services.folder_listings.list(root_id=args.root_id, status=args.status, missing_first=True)[: args.limit]
    return {"listings": rows, "count": len(rows)}


def run_folder_listing_get(services: Services, args: FolderPathArgs) -> dict:
    root_id, rel = _resolve_folder(services, args.root_id, args.path)
    listing = services.folder_listings.get(root_id, rel)
    if listing is None:
        raise LookupError(f"No folder listing at '{rel or '(root)'}'. Call folder_listing_set or folder_listing_draft to create one.")
    return {"listing": listing}


def run_folder_listing_set(services: Services, args: FolderListingSetArgs) -> dict:
    root_id, rel = _resolve_folder(services, args.root_id, args.path)
    listing = services.folder_listings.set(root_id, rel, {"title": args.title, "description": args.description, "tags": args.tags}, status=args.status)
    return {"ok": True, "listing": listing}


def run_folder_listing_check(services: Services, args: FolderListingCheckArgs) -> dict:
    if args.path is not None:
        root_id, rel = _resolve_folder(services, args.root_id, args.path)
        listing = services.folder_listings.check(root_id, rel)
        return {"listing": listing, "ok": not listing["issues"]}
    results = services.folder_listings.check_all(root_id=args.root_id)
    bad = [r for r in results if r["issues"]]
    return {"checked": len(results), "with_issues": len(bad), "listings": results}


def run_folder_listing_draft(services: Services, args: FolderListingDraftArgs) -> dict:
    targets = services.folder_listings.match_targets(args.root_id, args.path, args.limit, args.overwrite)
    if not targets:
        return {"ok": True, "queued": 0, "note": "No matching folder needs a draft (everything already has a listing; pass overwrite=true to redraft)."}
    try:
        progress = services.draft_worker.start(targets, args.overwrite)
    except RuntimeError as error:
        raise ValueError(str(error)) from error
    return {"ok": True, "queued": len(targets), "progress": progress, "note": "Drafting in the background; poll models_stats-style with folder_listings(status='draft') or the app's UI."}


def run_folder_listings_export(services: Services, args: FolderListingsExportArgs) -> dict:
    result = services.folder_listings.export(args.root_id, args.format, args.status)
    return {"ok": True, **result}


def run_collection_plan(services: Services, args: CollectionPlanArgs) -> dict:
    plan = services.organizer.plan(args.root, args.reference, args.target_template, args.match.model_dump(), args.reference_format, args.number_width)
    view = plan_view(plan, 300)
    view["note"] = ("Nothing was moved. Review unmatched, ambiguous and conflicts (fix the list or add aliases and plan again), "
                    "then collection_apply(plan_id) when the user agrees; collection_undo reverts it.")
    return view


def _rescan_note(queued: list[int]) -> str:
    return (f"Rescan queued for root(s) {queued}; wait until models_stats shows it idle before sheets_batch or folder_listing_*." if queued
            else "The folder is not inside a scanned root, so nothing was rescanned.")


def run_collection_apply(services: Services, args: CollectionApplyArgs) -> dict:
    journal = services.organizer.apply(args.plan_id)
    queued = services.rescan_covering(journal["root"]) if journal["moves"] else []
    return {"ok": True, "apply_id": journal["id"], "plan_id": args.plan_id, "root": journal["root"], "counts": journal["counts"],
            "created_folders": len(journal["created_dirs"]), "skipped": journal["skipped"][:200], "errors": journal["errors"][:50],
            "rescan_queued": queued, "note": f"{_rescan_note(queued)} Nothing was deleted or overwritten; collection_undo(apply_id) reverts it."}


def run_collection_undo(services: Services, args: CollectionUndoArgs) -> dict:
    result = services.organizer.undo(args.apply_id, args.remove_created_folders)
    queued = services.rescan_covering(result["root"]) if result["restored"] else []
    return {"ok": True, **result, "rescan_queued": queued, "note": _rescan_note(queued)}


def run_sheets_batch(services: Services, args: SheetsBatchArgs) -> dict:
    from .sheets_batch import run_sheets_batch as batch

    root_id, rel = _resolve_folder(services, args.root_id, args.path)
    return batch(services, root_id, rel, args.scope, args.mode, args.refresh, args.overwrite,
                 args.dry_run, args.limit, args.language)

def run_import_file(services: Services, args: ImportFileArgs) -> dict:
    from .family_tools import import_file

    return import_file(services, args.path, args.source_ref or "")


def run_catalog_export(services: Services, args: CatalogExportArgs) -> dict:
    from .family_tools import export_catalog

    return export_catalog(services, args.folder or "")


TOOLS: list[Tool] = [
    Tool("models_search", "Search the 3D model library by words and filters. Keywords: buscar modelo, encuentra mi STL, figura de.\nSearch the user's 3D model library by words (name, tags, notes, folder, listing text) with filters: format, tag, collection, watertight, has_listing, duplicates, size in mm. Returns id, name, format, bbox (mm), triangles, tags, has_listing and thumb_url per hit, paginated. Ranking sorts (-extent, -volume, -size, -triangles, -date) fold identical files into one hit with a 'copies' count, so 'the three biggest' are three different models. First step for 'find my model of X'.\nSinónimos: buscar modelo, modelo 3D, STL, 3MF, OBJ, impresión 3D, archivo, figura, pieza, buscar en mis modelos, cuál era, carpeta de modelos, etiquetas, tamaño en mm, miniatura.", SearchArgs, ann(True), run_search),
    Tool("model_info", "Everything about one model: size in mm, triangles, tags, listing, duplicates. Keywords: ficha, dimensiones.\nEverything about one model (by id or path): dimensions in mm, triangles, vertices, volume, surface, watertight, bodies (parts), units guess, folder, tags, notes, listing, exact and near duplicates, albums, thumb_url.\nSinónimos: modelo 3D, ficha, detalles, cuántos triángulos, tamaño en mm, medidas, volumen, es estanco, cuántas piezas, duplicados, STL, ruta del archivo, miniatura.", InfoArgs, ann(True), run_info),
    Tool("model_listing_get", "The marketplace listing of a model, or none. Keywords: ver ficha de tienda, descripción, tags.\nThe marketplace listing of a model (title, description, tags, category, price hint, language, source, updated_at), or none.\nSinónimos: ficha, descripción para la tienda, texto de la tienda, título, etiquetas, categoría, precio, ficha del modelo.", IdArgs, ann(True), run_listing_get),
    Tool("model_listing_set", "Write a model's marketplace listing: title, description, tags (write). Keywords: escribe la ficha, publicar.\nWrite the marketplace listing of a model (write): title, description (markdown), tags, category, price_hint, language. Fields left out keep their value; source is recorded as assistant. Idempotent: saving the same text twice changes nothing but the timestamp. Describe only what the geometry and the user say.\nSinónimos: genera la ficha, escribe la descripción para la tienda, ficha del modelo, título y descripción, etiquetas para la tienda, categoría, precio, redactar.", ListingSetArgs, ann(False, False, True), run_listing_set),
    Tool("model_tag", "Add or remove tags on a model (write). Keywords: etiquetar, quitar etiqueta, clasificar modelo.\nAdd and/or remove tags on a model (write). Tags are lower-cased and de-duplicated; adding an existing tag is a no-op.\nSinónimos: etiquetar, añadir etiqueta, quitar etiqueta, etiquetas, tags, marcar, clasificar modelo.", TagArgs, ann(False, False, True), run_tag),
    Tool("model_note", "Replace or append the user's notes on a model (write).\nSinónimos: nota, apuntar, anotar, notas del modelo, recordar sobre este modelo, comentario.", NoteArgs, ann(False, False, False), run_note),
    Tool("models_stats", "Library statistics: counts, sizes, duplicates, scan queue and ETA. Keywords: cuántos modelos, estadísticas.\nLibrary statistics: models, bytes and triangles by format and by root folder, collections, listings, duplicates, errors, skipped files, scan queue with files/second and ETA per root, and folder watching. Cheap to call repeatedly (cached 5 s during a scan).\nSinónimos: estadísticas, cuántos modelos, cuántos STL, tamaño de la biblioteca, carpetas de modelos, está escaneando, progreso, resumen.", Empty, ann(True), run_stats),
    Tool("models_dupes", "Duplicate groups, exact or near. Keywords: duplicados, repetidos, modelos iguales.\nDuplicate groups: exact (identical files, same sha256) or near (same triangle count, volume and bounding box within 1 %; suggestions to review). Each group lists id, name, path, size and dimensions.\nSinónimos: duplicados, repetidos, archivos iguales, copias, modelos parecidos, limpiar duplicados, mismo STL.", DupesArgs, ann(True), run_dupes),
    Tool("models_similar", "Find models with a similar 3D shape to one model id. Keywords: misma forma, remallado, geometría parecida.\nCompares filled closed meshes on a normalized voxel grid across right-angle rotations. Finds related models despite different triangle counts or uniform scale. Returns ranked scores and model ids; never labels them as duplicates.\nSinónimos: piezas parecidas, modelos con forma similar, geometría similar, mismo objeto remallado, versiones de esta pieza, busca modelos parecidos.", SimilarArgs, ann(True), run_similar),
    Tool("models_add_root", "Add a folder of models to the library and scan it (only when asked). Keywords: añadir carpeta, escanear.\nAdd a folder of models to the library (write). The path must exist on the user's PC; adding the same folder twice returns the existing root without rescanning. Options: thumbnails=all|top-level|none and skip_small_bytes to ignore tiny auto-generated meshes; exclude globs can be edited in the app (Carpetas). Scanning (metrics + thumbnails) starts in the background, in parallel worker processes. Only when the user asks.\nSinónimos: añadir carpeta, carpeta de modelos, escanear carpeta, indexar mis STL, nueva carpeta, agregar modelos.", AddRootArgs, ann(False, False, True), run_add_root),
    Tool("models_rescan", "Rescan a root or every root for new or changed files (only when asked). Keywords: reescanear, actualizar.\nQueue a non-destructive rescan of one root or of every enabled root: only new or changed files are parsed again; deleted files are purged. Only when the user asks.\nSinónimos: reescanear, volver a escanear, actualizar biblioteca, refrescar carpeta, escanear de nuevo, reindexar.", RescanArgs, ann(False, False, True), run_rescan),
    Tool("models_recent", "The newest models by file date. Keywords: modelos recientes, últimos añadidos, nuevos.\nThe n newest models by file modification date, with id, name, format, bbox, triangles, tags, has_listing and thumb_url.\nSinónimos: recientes, últimos modelos, lo último que he modelado, novedades, qué he añadido, modelos nuevos.", RecentArgs, ann(True), run_recent),
    Tool("folder_listings", "List folder listings (one per product folder) filtered by status; folders missing one come first.\nSinónimos: fichas, listados de carpeta, qué fichas faltan, estado de las fichas, carpetas sin ficha, cults3d.", FolderListingsArgs, ann(True), run_folder_listings),
    Tool("folder_listing_get", "The marketplace listing of one folder (title, description, 20 tags, status, issues), by root+path.\nSinónimos: ficha de la carpeta, título y descripción de la carpeta, etiquetas, cults3d.json, estado de la ficha.", FolderPathArgs, ann(True), run_folder_listing_get),
    Tool("folder_listing_set", "Write a folder's listing (write): title, description (English), 20 tags; saves the DB and cults3d.json.\nSinónimos: genera la ficha de la carpeta, guarda el título y la descripción, escribe las 20 etiquetas, cults3d.json.", FolderListingSetArgs, ann(False, False, True), run_folder_listing_set),
    Tool("folder_listing_check", "Validate one or every folder listing (format, lengths, 20 unique tags, template rules, duplicate titles).\nSinónimos: comprobar ficha, validar ficha, revisar carpeta, está bien la ficha, errores de la ficha.", FolderListingCheckArgs, ann(True), run_folder_listing_check),
    Tool("folder_listing_draft", "Draft folder listings with the local model in the background (write): title, description, 20 tags.\nSinónimos: redactar fichas, generar ficha con IA, borrador de ficha, rellenar fichas que faltan, redacción automática.", FolderListingDraftArgs, ann(False, False, False), run_folder_listing_draft),
    Tool("folder_listings_export", "Export folder listings of a root to CSV, Markdown or JSON in data/exports (write, returns the path).\nSinónimos: exportar fichas, exportar catálogo, csv de fichas, listado para subir a la tienda, exportar cults3d.", FolderListingsExportArgs, ann(False, False, False), run_folder_listings_export),
    Tool("collection_plan", "Plan sorting a folder's items into group folders from a pasted list (read-only). Keywords: organizar.\nRead-only plan, no model: scans the immediate children of a folder and matches each to a member of the user's list (one group per line, members separated by >, ->, → or ','; or CSV group,member,number), ignoring case, accents, separators and number prefixes like 0001_. Returns the target folder per group with the items moving into it, items already in place, unmatched items, ambiguous items (several members) and conflicts (target exists as a file, name already taken), plus a plan_id for collection_apply. Nothing is moved.\nSinónimos: organizar colección, agrupar carpetas, una carpeta por línea evolutiva, ordenar por lista, clasificar archivos, meter en carpetas, plan de organización, carpeta por grupo, números con ceros.", CollectionPlanArgs, ann(True), run_collection_plan),
    Tool("collection_apply", "Apply a stored collection plan: create group folders and move the items in (write). Keywords: aplicar plan.\nCarries out a plan from collection_plan by id: creates the group folders and moves the matched items into them. Never deletes and never overwrites (a collision is skipped and reported), re-checks the disk at apply time, writes an undo journal and queues a rescan of the affected root. Only when the user agrees to the plan.\nSinónimos: aplicar plan, mover a carpetas, ejecutar la organización, crear carpetas por grupo, ordenar de verdad.", CollectionApplyArgs, ann(False, False, False), run_collection_apply),
    Tool("collection_undo", "Undo the last collection_apply (or a given one) by moving every item back (write). Keywords: deshacer.\nReverses an apply from its journal: moves each item back to where it was, never overwriting anything (an occupied original location is skipped and reported), then removes the group folders the apply created if they are empty again. Defaults to the most recent apply that is not undone. Queues a rescan.\nSinónimos: deshacer organización, revertir, volver atrás, devolver los archivos a su sitio, deshacer el último cambio.", CollectionUndoArgs, ann(False, False, False), run_collection_undo),
    Tool("sheets_batch", "Create or refresh sheet drafts for every folder or model of a root in one call (write). Keywords: fichas.\nOne call for all the sheets: for every folder of models (cults3d.json sheets, English) or every model (listings) under a root creates a deterministic skeleton draft from the scanned data and the root's template, without any language model. Drafts stay in status draft; existing drafts are rewritten only with refresh=true, checked/approved sheets only with overwrite=true. dry_run reports counts without writing. mode=model queues the background local-model drafting instead (folders only). Returns created, refreshed and skipped counts with reasons.\nSinónimos: fichas de todas las carpetas, crear todas las fichas, borradores de fichas en lote, rellenar fichas que faltan, ficha esqueleto, generar fichas de golpe, fichas para toda la colección.", SheetsBatchArgs, ann(False, False, False), run_sheets_batch),    Tool("model_import_file", "Index one STL/3MF/OBJ file now: thumbnail, size, duplicate check (write). Keywords: importar modelo.\nIndex one model file right away instead of waiting for a scan (write): measures it, renders the thumbnail, hashes it for the duplicate check and adds it to the library. The folder is added as a small root that lists only the files imported from it, unless a scanned root already covers the file. Importing the same file twice changes nothing. Emits vulcan.model.added for a new model; source_ref (a hoard:// reference, e.g. an export of another app) is stored and linked.\nSinónimos: importar archivo, añadir un STL, indexar modelo, registrar exportación, añadir al catálogo ahora, escanear un archivo.", ImportFileArgs, ann(False, False, True), run_import_file),
    Tool("listings_export_catalog", "Export the folder and model listings as catalogue entries for a shop catalogue. Keywords: exportar catálogo.\nEvery folder listing (not 'none') and every model listing as one entry: ref (hoard://vulcan/...), kind folder or model, title, description, tags, price (the listing's price hint, if any), folder, model_ids, status, updated_at. folder limits it to one folder and below. Read-only: it writes nothing; Mercator imports this shape.\nSinónimos: catálogo de la tienda, fichas para Mercator, exportar fichas como catálogo, listado de productos, cults3d.", CatalogExportArgs, ann(True), run_catalog_export),
]

TOOLS_BY_NAME = {tool.name: tool for tool in TOOLS}


def tool_catalog() -> list[dict]:
    return catalog_of(TOOLS)


def call_tool(services: Services, name: str, arguments: dict | None, *, cap: bool = True) -> Any:
    """Run one tool through the shared agent kit (argument validation, result cap)."""
    return run_tool(TOOLS_BY_NAME, services, name, arguments, cap=cap)
