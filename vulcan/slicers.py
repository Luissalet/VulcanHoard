"""Open library models in a desktop slicer (PrusaSlicer, Bambu Studio, OrcaSlicer, UltiMaker Cura).

Executables are autodetected in the usual install folders on Windows (and on PATH elsewhere); a path saved in
`<data dir>/slicers.json` wins over detection. Opening launches the slicer detached on this PC with the file paths as
arguments, the way "Open with" does; nothing is copied or converted."""

from __future__ import annotations

import glob
import os
import shutil
import subprocess
import sys
from pathlib import Path

from .hoard_link import atomic

SLICERS = {
    "prusaslicer": {"name": "PrusaSlicer", "exe": ["prusa-slicer.exe", "prusa-slicer"],
                    "windows": [r"{pf}\Prusa3D\PrusaSlicer\prusa-slicer.exe", r"{pf}\Prusa3D\PrusaSlicer*\prusa-slicer.exe",
                                r"{pf86}\Prusa3D\PrusaSlicer\prusa-slicer.exe"]},
    "bambustudio": {"name": "Bambu Studio", "exe": ["bambu-studio.exe", "bambu-studio"],
                    "windows": [r"{pf}\Bambu Studio\bambu-studio.exe", r"{local}\Programs\Bambu Studio\bambu-studio.exe"]},
    "orcaslicer": {"name": "OrcaSlicer", "exe": ["orca-slicer.exe", "orca-slicer", "OrcaSlicer"],
                   "windows": [r"{pf}\OrcaSlicer\orca-slicer.exe", r"{local}\Programs\OrcaSlicer\orca-slicer.exe"]},
    "cura": {"name": "UltiMaker Cura", "exe": ["UltiMaker-Cura.exe", "Ultimaker-Cura.exe", "cura"],
             "windows": [r"{pf}\UltiMaker Cura*\UltiMaker-Cura.exe", r"{pf}\Ultimaker Cura*\Ultimaker-Cura.exe",
                         r"{pf}\Ultimaker Cura*\Cura.exe"]},
}
OPENABLE = (".stl", ".3mf", ".obj")
MAX_FILES = 50


def _settings_path(data_dir: Path) -> Path:
    return Path(data_dir) / "slicers.json"


def load_settings(data_dir: Path) -> dict:
    data = atomic.read_json(_settings_path(data_dir), default=None)
    data = data if isinstance(data, dict) else {}
    return {"paths": {k: v for k, v in (data.get("paths") or {}).items() if k in SLICERS and isinstance(v, str)},
            "default": data.get("default") if data.get("default") in SLICERS else None}


def save_settings(data_dir: Path, paths: dict | None = None, default: str | None = None) -> dict:
    current = load_settings(data_dir)
    for key, value in (paths or {}).items():
        if key not in SLICERS:
            raise ValueError(f"Unknown slicer '{key}'. Use one of: {', '.join(SLICERS)}.")
        value = (value or "").strip().strip('"')
        if not value:
            current["paths"].pop(key, None)
            continue
        if not Path(value).is_file():
            raise ValueError(f"{SLICERS[key]['name']}: the file does not exist: {value}")
        current["paths"][key] = value
    if default is not None:
        if default and default not in SLICERS:
            raise ValueError(f"Unknown slicer '{default}'.")
        current["default"] = default or None
    atomic.write_json_atomic(_settings_path(data_dir), current)
    return current


def _detect(key: str) -> str | None:
    spec = SLICERS[key]
    if os.name == "nt":
        roots = {"pf": os.environ.get("ProgramFiles", r"C:\Program Files"), "pf86": os.environ.get("ProgramFiles(x86)", r"C:\Program Files (x86)"),
                 "local": os.environ.get("LOCALAPPDATA", "")}
        for pattern in spec["windows"]:
            for hit in sorted(glob.glob(pattern.format(**roots)), reverse=True):  # newest version folder first
                if Path(hit).is_file():
                    return hit
    for name in spec["exe"]:
        found = shutil.which(name)
        if found:
            return found
    if sys.platform == "darwin":
        app = Path("/Applications") / f"{spec['name'].replace(' ', '')}.app"
        if app.exists():
            return str(app)
    return None


def list_slicers(data_dir: Path) -> dict:
    settings = load_settings(data_dir)
    rows = []
    for key, spec in SLICERS.items():
        configured = settings["paths"].get(key)
        detected = _detect(key)
        path = configured if configured and Path(configured).exists() else detected
        rows.append({"id": key, "name": spec["name"], "path": path, "configured_path": configured, "detected_path": detected,
                     "source": "configured" if path and path == configured else "detected" if path else "missing", "available": bool(path)})
    available = [r["id"] for r in rows if r["available"]]
    default = settings["default"] if settings["default"] in available else (available[0] if available else None)
    return {"slicers": rows, "default": default, "saved_default": settings["default"], "settings_path": str(_settings_path(data_dir))}


def command_for(executable: str, files: list[str]) -> list[str]:
    if sys.platform == "darwin" and executable.endswith(".app"):
        return ["open", "-a", executable, *files]
    return [executable, *files]


def launch(command: list[str]) -> int:
    """Start the slicer detached from this server (it keeps running when Vulcan stops). Returns the process id."""
    flags = 0
    if os.name == "nt":
        flags = getattr(subprocess, "DETACHED_PROCESS", 0) | getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
    process = subprocess.Popen(command, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                               close_fds=True, creationflags=flags, cwd=str(Path(command[-1]).parent) if len(command) > 1 else None)
    return process.pid


def _folder_models(services, root_id: int | None, path: str) -> tuple[list[dict], str]:
    from .agent_tools import _resolve_folder

    root_id, rel = _resolve_folder(services, root_id, path)
    prefix = rel + "/" if rel else ""
    with services.db.lock:
        rows = services.db.conn.execute("SELECT id, path, rel_path, name FROM models WHERE root_id = ? AND status = 'ok' ORDER BY rel_path",
                                        (root_id,)).fetchall()
    picked = [dict(r) for r in rows if r["rel_path"].startswith(prefix)]
    return picked, rel


def open_in_slicer(services, model_ids: list[int] | None = None, root_id: int | None = None, path: str | None = None,
                   slicer: str | None = None, dry_run: bool = False) -> dict:
    """Launch a slicer with the files of some models, or of every model in a folder (and its subfolders)."""
    folder = None
    if model_ids:
        models = []
        for model_id in model_ids:
            model = services.models.get(model_id)
            if model is None:
                raise LookupError(f"Model {model_id} does not exist.")
            models.append(model)
    elif path is not None or root_id is not None:
        models, folder = _folder_models(services, root_id, path or "")
    else:
        raise ValueError("Give model_ids, or a folder (root_id + path, or an absolute folder path).")
    files = [m["path"] for m in models if Path(m["path"]).suffix.lower() in OPENABLE and Path(m["path"]).is_file()]
    if not files:
        raise ValueError("None of those models has an STL, 3MF or OBJ file on disk.")
    skipped = len(files) - MAX_FILES if len(files) > MAX_FILES else 0
    files = files[:MAX_FILES]
    available = list_slicers(services.config.data_dir)
    key = slicer or available["default"]
    row = next((r for r in available["slicers"] if r["id"] == key), None)
    if row is None or not row["available"]:
        found = ", ".join(r["name"] for r in available["slicers"] if r["available"]) or "none"
        raise ValueError(f"Slicer '{key or '-'}' is not installed or configured (found: {found}). Set its path in Ajustes or slicers settings.")
    command = command_for(row["path"], files)
    result = {"ok": True, "slicer": row["id"], "slicer_name": row["name"], "executable": row["path"], "files": files, "count": len(files),
              "folder": folder, "skipped_over_limit": skipped, "dry_run": dry_run}
    if dry_run:
        result["command"] = command
        result["note"] = "dry_run: nothing was launched."
        return result
    result["pid"] = launch(command)
    result["note"] = f"{row['name']} is opening {len(files)} file(s) on this PC."
    return result
