"""sheets_batch: create or refresh sheet drafts for every folder (or every model) under a root in one call.

Reuses the per-folder sheet logic of `folder_listings.FolderListingStore` (the
same DB rows, the same `cults3d.json` write-back, the same validator) and the
per-model `ListingStore`. Two modes:

- `skeleton` (default): a deterministic draft built only from what the scan
  measured (folder name, formats, file names, sizes in mm) plus the root's
  template tags. No language model is involved or loaded; the draft stays in
  status `draft` and still has to be refined and approved.
- `model`: queues the existing background drafting job (`folder_listing_draft`),
  which needs a local model backend. Only when the caller asks for it.

Existing sheets are never replaced by accident: a `draft` is refreshed only
with `refresh=true`, a `checked`/`approved` sheet only with `overwrite=true`.
"""

from __future__ import annotations

import fnmatch
import re

from .folder_listings import TAG_COUNT, TITLE_MAX, folder_of, load_template
from .store import normalise_tags

GENERIC_TAGS = ("3d print", "3d printing", "3d model", "3d printable", "printable", "3d printer")
SAMPLE_FILES = 15
_LEADING_NUMBER = re.compile(r"^\s*#?\d+(?:\s*[.):_]\s*|\s+-\s+|\s+)(?=\S)")
_ONLY_NUMBERS = re.compile(r"^[\d\s.+&,#-]+$")
_TEXT = {
    "en": {
        "lead": "{name}: {count} printable file{plural} ({formats}) ready for 3D printing.",
        "largest": "The largest part measures {x} x {y} x {z} mm.",
        "triangles": "Together the meshes hold {triangles} triangles.",
        "closed": "{closed} of {measured} measured meshes are watertight.",
        "files": "Files included:",
        "more": "... and {n} more file{plural}.",
        "closing": "Dimensions come from the models themselves; check scale and orientation in your slicer before printing.",
        "title": "{name} - 3D Print {formats} Files",
    },
    "es": {
        "lead": "{name}: {count} archivo{plural} imprimible{plural} ({formats}) listo{plural} para impresi\u00f3n 3D.",
        "largest": "La pieza m\u00e1s grande mide {x} x {y} x {z} mm.",
        "triangles": "Entre todas las mallas suman {triangles} tri\u00e1ngulos.",
        "closed": "{closed} de {measured} mallas medidas son estancas.",
        "files": "Archivos incluidos:",
        "more": "... y {n} archivo{plural} m\u00e1s.",
        "closing": "Las medidas salen de los propios modelos; revisa la escala y la orientaci\u00f3n en tu laminador antes de imprimir.",
        "title": "{name} - Archivos {formats} para impresi\u00f3n 3D",
    },
}


def clean_name(raw: str) -> str:
    """A readable product name from a folder or file name: no number prefix, underscores as spaces."""
    if _ONLY_NUMBERS.match(raw or ""):
        return (raw or "").strip()  # a name made of numbers only (1-3, 108+463) is the name
    name = _LEADING_NUMBER.sub("", raw or "").replace("_", " ")
    name = " ".join(name.split()).strip(" -")
    if name and name == name.lower():
        name = name.title()
    return name or (raw or "").strip()


def _fmt_mm(row) -> str | None:
    if row["bbox_x"] is None:
        return None
    return " x ".join(f"{row[k]:.1f}".rstrip("0").rstrip(".") for k in ("bbox_x", "bbox_y", "bbox_z"))


def build_sheet(name: str, rows: list, template: dict | None = None, language: str = "en") -> dict:
    """{title, description, tags} from the measured files of one folder (or one model). Facts only, nothing invented."""
    text = _TEXT.get(language, _TEXT["en"])
    template = template or {}
    count = len(rows)
    plural = "" if count == 1 else "s"
    formats = sorted({str(r["format"]).upper() for r in rows}) or ["STL"]
    parts = [text["lead"].format(name=name, count=count, plural=plural, formats=", ".join(formats))]
    measured = [r for r in rows if r["bbox_x"] is not None]
    if measured:
        biggest = max(measured, key=lambda r: max(r["bbox_x"], r["bbox_y"], r["bbox_z"]))
        extent = " ".join(text["largest"].format(x=f"{biggest['bbox_x']:.1f}", y=f"{biggest['bbox_y']:.1f}", z=f"{biggest['bbox_z']:.1f}").split())
        tri = sum(r["triangles"] or 0 for r in measured)
        closed = sum(1 for r in measured if r["watertight"])
        line = extent + (" " + text["triangles"].format(triangles=f"{tri:,}") if tri else "")
        line += " " + text["closed"].format(closed=closed, measured=len(measured))
        parts.append(line)
    listing = []
    for r in rows[:SAMPLE_FILES]:
        size = _fmt_mm(r)
        listing.append(f"- {r['name']} ({str(r['format']).upper()}{', ' + size + ' mm' if size else ''})")
    if count > SAMPLE_FILES:
        listing.append(text["more"].format(n=count - SAMPLE_FILES, plural="" if count - SAMPLE_FILES == 1 else "s"))
    if count > 1 or listing:
        parts.append(text["files"] + "\n" + "\n".join(listing))
    parts.append(text["closing"])
    description = "\n\n".join(parts)

    title = text["title"].format(name=name, formats=" & ".join(formats))
    if len(title) > TITLE_MAX:
        title = title[:TITLE_MAX].rstrip()

    words = [w for w in re.split(r"[^\w']+", name.lower()) if len(w) >= 3 and not w.isdigit()][:5]
    candidates = list(words)
    if len(words) > 1:
        candidates.append(name.lower())
    candidates += [str(t) for t in (template.get("required_tags") or [])]
    candidates += [str(t) for t in (template.get("base_tags") or [])]
    pool = template.get("tags")
    if isinstance(pool, dict):
        candidates += [str(t) for t in (pool.get("pool") or [])]
    candidates += [f.lower() for f in formats] + [f"{f.lower()} file" for f in formats[:1]] + list(GENERIC_TAGS)
    forbidden = [w.strip().lower() for w in (template.get("forbidden_words") or []) if isinstance(w, str) and w.strip()]
    tags = [t for t in normalise_tags(candidates) if not any(w in t for w in forbidden)][:TAG_COUNT]
    return {"title": title, "description": description, "tags": tags}


def _wanted(rel: str, pattern: str) -> bool:
    pattern = (pattern or "").strip().strip("/")
    if not pattern:
        return True
    low = rel.lower()
    pat = pattern.lower()
    return low == pat or low.startswith(pat + "/") or fnmatch.fnmatch(low, pat)

def _model_rows(services, root_id: int) -> list:
    with services.db.lock:
        return services.db.conn.execute(
            "SELECT m.id, m.rel_path, m.name, m.format, m.size_bytes, m.bbox_x, m.bbox_y, m.bbox_z, m.triangles, m.watertight, "
            "(l.model_id IS NOT NULL) AS has_listing, l.listing_source "
            "FROM models m LEFT JOIN listings l ON l.model_id = m.id WHERE m.root_id = ? ORDER BY m.rel_path",
            (root_id,),
        ).fetchall()


def run_sheets_batch(services, root_id: int, path: str = "", scope: str = "folders", mode: str = "skeleton", refresh: bool = False,
                     overwrite: bool = False, dry_run: bool = False, limit: int = 1000, language: str = "en") -> dict:
    """Create or refresh sheet drafts for every folder (or model) of a root. See the module docstring for the rules."""
    if scope not in ("folders", "models"):
        raise ValueError("scope must be folders or models.")
    if mode not in ("skeleton", "model"):
        raise ValueError("mode must be skeleton or model.")
    if mode == "model" and scope != "folders":
        raise ValueError("mode=model only drafts folder sheets; use scope=folders (or mode=skeleton for models).")
    root = services.roots.get(root_id)
    if root is None:
        raise LookupError(f"Root {root_id} does not exist.")
    state = services.worker.status()
    if state["current"] == root_id or root_id in state["queued"]:
        raise ValueError("A scan of this root is still running; wait until models_stats shows it idle and call again.")
    rows = _model_rows(services, root_id)
    if not rows:
        raise ValueError("This root has no scanned models yet; add it and let the scan finish first.")
    template = load_template(root.path)
    result = {"ok": True, "root_id": root_id, "scope": scope, "mode": mode, "dry_run": dry_run, "created": [], "refreshed": [], "skipped": [],
              "queued": 0, "counts": {"created": 0, "refreshed": 0, "skipped": 0, "queued": 0}}
    actions: list[tuple[str, object, str]] = []  # (create|refresh, folder rel or model row, label)

    if scope == "folders":
        by_folder: dict[str, list] = {}
        for row in rows:
            by_folder.setdefault(folder_of(row["rel_path"]), []).append(row)
        for rel in sorted(by_folder):
            if not _wanted(rel, path):
                continue
            label = rel or "(root)"
            existing = services.folder_listings.get(root_id, rel)
            status = existing["status"] if existing else "none"
            if status == "none":
                actions.append(("create", rel, label))
            elif status == "draft" and (refresh or overwrite):
                actions.append(("refresh", rel, label))
            elif status == "draft":
                result["skipped"].append({"path": label, "reason": "draft_exists (pass refresh=true to rewrite it)"})
            elif overwrite:
                actions.append(("refresh", rel, label))
            else:
                result["skipped"].append({"path": label, "reason": f"{status}_protected (pass overwrite=true to replace it)"})
    else:
        for row in rows:
            if not _wanted(folder_of(row["rel_path"]), path):
                continue
            label = row["rel_path"]
            if not row["has_listing"]:
                actions.append(("create", row, label))
            elif row["listing_source"] == "assistant" and (refresh or overwrite):
                actions.append(("refresh", row, label))
            elif row["listing_source"] == "assistant":
                result["skipped"].append({"path": label, "reason": "assistant_listing_exists (pass refresh=true to rewrite it)"})
            elif overwrite:
                actions.append(("refresh", row, label))
            else:
                result["skipped"].append({"path": label, "reason": "manual_listing_protected (pass overwrite=true to replace it)"})

    if len(actions) > limit:
        result["skipped"] += [{"path": label, "reason": "limit_reached (raise limit or call again)"} for _, _, label in actions[limit:]]
        actions = actions[:limit]

    if mode == "model":
        targets = [(root_id, rel) for _, rel, _ in actions]
        result["queued"] = len(targets)
        if targets and not dry_run:
            try:
                result["progress"] = services.draft_worker.start(targets, bool(refresh or overwrite))
            except RuntimeError as error:
                raise ValueError(str(error)) from error
        result["note"] = "Drafting with the local model in the background; poll folder_listings(status='draft') or the Fichas page."
    else:
        folder_rows = by_folder if scope == "folders" else {}
        for kind, subject, label in actions:
            if scope == "folders":
                name = clean_name(subject.rsplit("/", 1)[-1] if subject else root.name)
                sheet = build_sheet(name, folder_rows[subject], template, "en")
                if not dry_run:
                    services.folder_listings.set(root_id, subject, sheet, status="draft")
            else:
                sheet = build_sheet(clean_name(subject["name"]), [subject], template, language)
                if not dry_run:
                    services.listings.set(subject["id"], {**sheet, "language": language}, source="assistant")
            result["created" if kind == "create" else "refreshed"].append(label)
        result["note"] = ("Skeleton drafts only use measured data and the template; refine them with folder_listing_set / model_listing_set, "
                          "then run folder_listing_check before calling them approved.")

    counts = result["counts"]
    counts["created"], counts["refreshed"], counts["skipped"], counts["queued"] = len(result["created"]), len(result["refreshed"]), len(result["skipped"]), result["queued"]
    for key in ("created", "refreshed", "skipped"):  # keep replies small; the counts above are exact
        if len(result[key]) > 200:
            result[f"{key}_truncated"] = len(result[key]) - 200
            result[key] = result[key][:200]
    return result