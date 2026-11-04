"""Listing photos of a library model rendered by Blender in the background (headless): the model on a floor with a soft
shadow, an orbit camera fitted to its size and studio light, from named angles, in Workbench (fast), EEVEE or Cycles (CPU).
Photos are cached in ``<data dir>/renders/`` by file content and parameters, with a contact sheet of all the angles.
Blender is found with VULCAN_BLENDER, then the newest ``Blender Foundation/Blender X.Y`` install, then PATH."""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import subprocess
import tempfile
import time
from pathlib import Path

from PIL import Image, ImageDraw

from .geometry import _load
from .hoard_link import atomic, proc
from .simplify import source_model
from .thumbnail import thumb_mesh

WORKER = Path(__file__).with_name("blender_photo_worker.py")
VIEWS = ("hero", "iso", "front", "side", "back", "left", "top")
DEFAULT_VIEWS = ("hero", "front", "side", "top")
ENGINES = ("workbench", "eevee", "cycles")
LABELS = {"hero": "3/4", "iso": "iso", "front": "frontal", "side": "lateral", "back": "trasera", "left": "izquierda", "top": "planta"}
MAX_FACES = 1_500_000  # bigger meshes are decimated for the photo only (the file is never touched)
HEX = re.compile(r"^#?([0-9a-fA-F]{6})$")


def _version_of(path: Path) -> tuple[int, ...]:
    match = re.search(r"Blender\s+(\d+)\.(\d+)", str(path))
    return tuple(int(x) for x in match.groups()) if match else (0, 0)


def blender_candidates() -> list[str]:
    found: list[str] = []
    explicit = os.environ.get("VULCAN_BLENDER", "").strip().strip('"')
    if explicit and Path(explicit).is_file():
        found.append(explicit)
    bases = [os.environ.get("ProgramFiles", r"C:\Program Files"), os.environ.get("ProgramFiles(x86)", "")]
    installs: list[Path] = []
    for base in filter(None, bases):
        installs += [p for p in Path(base, "Blender Foundation").glob("Blender*/blender.exe") if p.is_file()]
    found += [str(p) for p in sorted(installs, key=_version_of, reverse=True)]
    on_path = shutil.which("blender")
    if on_path:
        found.append(on_path)
    return list(dict.fromkeys(found))


def find_blender() -> str | None:
    candidates = blender_candidates()
    return candidates[0] if candidates else None


def blender_status() -> dict:
    path = find_blender()
    version = ".".join(map(str, _version_of(Path(path)))) if path and _version_of(Path(path)) != (0, 0) else None
    return {"available": bool(path), "path": path, "version": version, "candidates": blender_candidates(),
            "engines": list(ENGINES), "views": list(VIEWS), "default_views": list(DEFAULT_VIEWS),
            "note": None if path else "Blender not found: install it or set VULCAN_BLENDER to blender.exe."}


def parse_color(value, default: str) -> tuple[float, float, float]:
    match = HEX.match(str(value or default).strip())
    if not match:
        raise ValueError(f"Colour must be #RRGGBB, got {value!r}.")
    raw = match.group(1)
    return tuple(int(raw[i:i + 2], 16) / 255.0 for i in (0, 2, 4))


def parse_views(views) -> list[str]:
    if isinstance(views, str):
        views = [v.strip() for v in views.split(",")]
    wanted = list(dict.fromkeys(v.lower() for v in (views or DEFAULT_VIEWS) if v))
    bad = [v for v in wanted if v not in VIEWS]
    if bad:
        raise ValueError(f"Unknown view(s) {', '.join(bad)}. Use: {', '.join(VIEWS)}.")
    return wanted[:7] or list(DEFAULT_VIEWS)


def build_spec(*, views, engine: str = "workbench", color: str = "#d6cdc0", background: str = "#f4f3ee",
               transparent: bool = False, size: int = 1024, samples: int | None = None, smooth: bool = True) -> dict:
    if engine not in ENGINES:
        raise ValueError(f"Unknown engine {engine!r}. Use: {', '.join(ENGINES)}.")
    samples = int(samples or (32 if engine == "cycles" else 16))
    return {"views": parse_views(views), "engine": engine, "color": parse_color(color, "#d6cdc0"),
            "background": parse_color(background, "#f4f3ee"), "transparent": bool(transparent),
            "size": max(256, min(2048, int(size))), "samples": max(1, min(512, samples)), "smooth": bool(smooth)}


def render_key(model: dict, spec: dict) -> str:
    recipe = json.dumps({k: spec[k] for k in ("engine", "color", "background", "transparent", "size", "samples", "smooth")}, sort_keys=True)
    digest = hashlib.sha1(recipe.encode()).hexdigest()[:10]
    return f"m{model['id']}-{(model.get('sha256') or str(model['id']))[:12]}-{digest}"


def renders_dir(services) -> Path:
    return services.config.data_dir / "renders"


def _parse_result(stdout: str) -> dict | None:
    for line in reversed((stdout or "").splitlines()):
        if line.startswith("VULCAN_RESULT "):
            try:
                return json.loads(line[len("VULCAN_RESULT "):])
            except ValueError:
                return None
    return None


def _timeout_for(engine: str, views: int, size: int) -> float:
    per_view = {"workbench": 20, "eevee": 60, "cycles": 240}[engine] * max(1.0, (size / 1024) ** 2)
    return 90 + per_view * views


def run_blender(blender: str, spec: dict, mesh_path: Path, outs: dict[str, str], work: Path, timeout: float) -> dict:
    """One Blender process renders every missing view; returns the worker's VULCAN_RESULT."""
    spec_file = work / "spec.json"
    spec_file.write_text(json.dumps({**spec, "views": list(outs), "mesh": str(mesh_path), "out": outs}), encoding="utf-8")
    argv = [blender, "-b", "--factory-startup", "--python", str(WORKER), "--", str(spec_file)]
    try:
        cp = proc.run(argv, timeout=timeout, env=proc.build_env())
    except subprocess.TimeoutExpired as error:
        raise RuntimeError(f"Blender took longer than {int(timeout)} s.") from error
    except OSError as error:
        raise RuntimeError(f"Blender could not finish: {error}") from error
    result = _parse_result(cp.stdout)
    if not result or not result.get("ok"):
        detail = (result or {}).get("error") or proc.tail_lines((cp.stdout or "") + "\n" + (cp.stderr or ""))
        raise RuntimeError(f"Blender failed: {detail}")
    return result


def contact_sheet(folder: Path, views: list[str], tile: int = 512) -> Path:
    """All the angles of one render set on one PNG (cached next to them)."""
    path = folder / f"sheet-{'-'.join(views)}.png"
    if path.is_file() and all(path.stat().st_mtime >= (folder / f"{v}.png").stat().st_mtime for v in views):
        return path
    cols = 1 if len(views) == 1 else 2 if len(views) <= 4 else 3
    rows = -(-len(views) // cols)
    sheet = Image.new("RGB", (cols * tile, rows * tile), (244, 243, 238))
    draw = ImageDraw.Draw(sheet)
    for index, view in enumerate(views):
        with Image.open(folder / f"{view}.png") as im:
            im = im.convert("RGBA").resize((tile, tile), Image.LANCZOS)
            x, y = (index % cols) * tile, (index // cols) * tile
            sheet.paste(im, (x, y), im)
        draw.text((x + 10, y + 8), LABELS.get(view, view), fill=(90, 80, 70))
    tmp = path.with_name(path.stem + ".tmp.png")
    sheet.save(tmp, "PNG", optimize=True)
    atomic.replace_with_retry(tmp, path)
    return path


def _describe(model_id: int, folder: Path, views: list[str]) -> list[dict]:
    base = f"/api/models/{model_id}/renders/{folder.name}"
    out = []
    for view in views:
        file = folder / f"{view}.png"
        if file.is_file():
            out.append({"view": view, "label": LABELS.get(view, view), "url": f"{base}/{view}.png?v={int(file.stat().st_mtime)}",
                        "path": str(file), "bytes": file.stat().st_size})
    return out


def render_photos(services, model_id: int, views=None, engine: str = "workbench", color: str = "#d6cdc0",
                  background: str = "#f4f3ee", transparent: bool = False, size: int = 1024, samples: int | None = None,
                  smooth: bool = True, force: bool = False, blender: str | None = None) -> dict:
    """Render (or reuse) listing photos of one model; only the missing angles go to Blender, in one process."""
    spec = build_spec(views=views, engine=engine, color=color, background=background, transparent=transparent,
                      size=size, samples=samples, smooth=smooth)
    model = source_model(services, model_id)
    blender = blender or find_blender()
    folder = renders_dir(services) / render_key(model, spec)
    missing = [v for v in spec["views"] if force or not (folder / f"{v}.png").is_file()]
    started = time.monotonic()
    run = None
    if missing:
        if not blender:
            raise RuntimeError("Blender not found: install it or set VULCAN_BLENDER to blender.exe.")
        work_root = renders_dir(services) / ".work"
        work_root.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(prefix="photo-", dir=work_root) as tmp:
            import trimesh

            mesh = _load(Path(model["path"]))
            vertices, faces = thumb_mesh(mesh.vertices, mesh.faces, MAX_FACES)
            stl = Path(tmp) / "model.stl"
            trimesh.Trimesh(vertices=vertices, faces=faces, process=False).export(str(stl))
            outs = {v: str(Path(tmp) / f"{v}.png") for v in missing}
            run = run_blender(blender, spec, stl, outs, Path(tmp), _timeout_for(spec["engine"], len(missing), spec["size"]))
            folder.mkdir(parents=True, exist_ok=True)
            for view, out in outs.items():
                if not Path(out).is_file():
                    raise RuntimeError(f"Blender did not write the {view} photo.")
                atomic.replace_with_retry(out, folder / f"{view}.png")
        meta = {"model_id": model_id, "name": model["name"], "sha256": model.get("sha256"), "spec": spec,
                "blender": blender, "blender_version": run.get("blender"), "engine_id": run.get("engine"),
                "rendered_at": time.time(), "triangles_rendered": run.get("triangles")}
        old = atomic.read_json(folder / "meta.json", {}) or {}
        meta["views"] = sorted(set(old.get("views", [])) | set(missing), key=VIEWS.index)
        atomic.write_json_atomic(folder / "meta.json", meta)
    sheet = contact_sheet(folder, spec["views"])
    return {"id": model_id, "name": model["name"], "key": folder.name, "engine": spec["engine"], "views": spec["views"],
            "rendered": missing, "cached": [v for v in spec["views"] if v not in missing],
            "photos": _describe(model_id, folder, spec["views"]),
            "sheet_url": f"/api/models/{model_id}/renders/{folder.name}/{sheet.name}?v={int(sheet.stat().st_mtime)}",
            "sheet_path": str(sheet), "blender": {"path": blender, "version": (run or {}).get("blender")},
            "per_view_seconds": (run or {}).get("views", []), "seconds": round(time.monotonic() - started, 2),
            "folder": str(folder), "spec": {k: spec[k] for k in ("size", "samples", "transparent", "smooth")}}


def list_photos(services, model_id: int) -> dict:
    """Every render set already made for a model, newest first."""
    root = renders_dir(services)
    sets = []
    for folder in root.glob(f"m{int(model_id)}-*"):
        meta = atomic.read_json(folder / "meta.json", None)
        if not folder.is_dir() or not meta:
            continue
        views = [v for v in meta.get("views", []) if (folder / f"{v}.png").is_file()]
        sets.append({"key": folder.name, "engine": meta["spec"]["engine"], "views": views,
                     "rendered_at": meta.get("rendered_at"), "blender_version": meta.get("blender_version"),
                     "photos": _describe(model_id, folder, views)})
    sets.sort(key=lambda s: s.get("rendered_at") or 0, reverse=True)
    return {"id": model_id, "sets": sets}


def photo_file(services, model_id: int, key: str, name: str) -> Path:
    if not re.fullmatch(rf"m{int(model_id)}-[0-9a-zA-Z]+-[0-9a-f]{{10}}", key) or not re.fullmatch(r"[a-z0-9-]+\.png", name):
        raise LookupError("No such photo.")
    path = renders_dir(services) / key / name
    if not path.is_file():
        raise LookupError("No such photo.")
    return path
