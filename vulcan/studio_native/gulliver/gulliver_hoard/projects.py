"""Projects on disk: ``<data>/projects/<id>/project.json`` plus ``builds/<build id>/`` (the files of each finished build).

A project is a named place with its parameters; building it makes a new build folder, and the project points at the newest
finished one. Older builds are pruned to ``keep_builds``."""

from __future__ import annotations

import re
import secrets
import shutil
import threading
import time
from pathlib import Path
from typing import Any, Optional

from .errors import GulliverError
from .hoard_link.atomic import read_json, write_json_atomic
from .params import ModelParams

ID_RE = re.compile(r"^[a-z0-9]{6,32}$")


def new_id() -> str:
    return time.strftime("%y%m%d") + secrets.token_hex(3)


class Projects:
    def __init__(self, folder: Path):
        self.folder = Path(folder)
        self.folder.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()

    # -- paths ----------------------------------------------------------------------------------------------------------------
    def dir(self, pid: str) -> Path:
        if not ID_RE.match(pid or ""):
            raise GulliverError("not_found", f"No model with id {pid!r}.", "List them with model_list.")
        return self.folder / pid

    def build_dir(self, pid: str, build_id: str) -> Path:
        if not ID_RE.match(build_id or ""):
            raise GulliverError("not_found", f"No build {build_id!r}.")
        return self.dir(pid) / "builds" / build_id

    # -- records --------------------------------------------------------------------------------------------------------------
    def _read(self, pid: str) -> dict[str, Any]:
        data = read_json(self.dir(pid) / "project.json", None)
        if not isinstance(data, dict):
            raise GulliverError("not_found", f"No model with id {pid!r}.", "List them with model_list.")
        return data

    def _write(self, rec: dict[str, Any]) -> None:
        rec["updated"] = time.time()
        write_json_atomic(self.dir(rec["id"]) / "project.json", rec)

    def get(self, pid: str) -> dict[str, Any]:
        with self._lock:
            return self._read(pid)

    def list(self) -> list[dict[str, Any]]:
        out = []
        with self._lock:
            for child in self.folder.iterdir() if self.folder.is_dir() else []:
                if child.is_dir() and ID_RE.match(child.name):
                    rec = read_json(child / "project.json", None)
                    if isinstance(rec, dict):
                        out.append(rec)
        out.sort(key=lambda r: r.get("updated", 0), reverse=True)
        return out

    def create(self, name: str, params: ModelParams, *, place: str = "", preset: str = "") -> dict[str, Any]:
        with self._lock:
            pid = new_id()
            while (self.folder / pid).exists():
                pid = new_id()
            (self.folder / pid).mkdir(parents=True)
            rec = {"id": pid, "name": name.strip() or "Maqueta", "place": place, "preset": preset, "created": time.time(),
                   "params": params.model_dump(), "status": "draft", "build": None, "error": "", "job": None, "builds": []}
            self._write(rec)
            return rec

    def update(self, pid: str, **changes: Any) -> dict[str, Any]:
        with self._lock:
            rec = self._read(pid)
            for key, value in changes.items():
                if key == "params" and isinstance(value, ModelParams):
                    value = value.model_dump()
                rec[key] = value
            self._write(rec)
            return rec

    def delete(self, pid: str) -> dict[str, Any]:
        with self._lock:
            rec = self._read(pid)
            shutil.rmtree(self.dir(pid), ignore_errors=True)
            return {"deleted": pid, "name": rec.get("name", "")}

    def duplicate(self, pid: str, name: str = "") -> dict[str, Any]:
        rec = self.get(pid)
        return self.create(name or f"{rec['name']} (copia)", ModelParams(**rec["params"]), place=rec.get("place", ""),
                           preset=rec.get("preset", ""))

    def finish_build(self, pid: str, build_id: str, report: dict[str, Any], keep: int,
                     params: Optional[dict[str, Any]] = None) -> dict[str, Any]:
        """Make ``build_id`` the current build, remember the parameters it was made with, and prune the oldest builds."""
        with self._lock:
            rec = self._read(pid)
            builds = [b for b in rec.get("builds", []) if b != build_id]
            builds.insert(0, build_id)
            for old in builds[keep:]:
                shutil.rmtree(self.build_dir(pid, old), ignore_errors=True)
            rec["builds"] = builds[:keep]
            entry = {"id": build_id, "finished": time.time(), "report": summary(report)}
            if params is not None:
                entry["params"] = params
                write_json_atomic(self.build_dir(pid, build_id) / "params.json", params)
            history = [h for h in rec.get("history", []) if h.get("id") in rec["builds"] and h.get("id") != build_id]
            rec["history"] = [entry] + history
            rec["build"] = {k: v for k, v in entry.items() if k != "params"}
            rec["status"] = "ready"
            rec["error"] = ""
            self._write(rec)
            return rec

    def builds(self, pid: str) -> list[dict[str, Any]]:
        """The kept builds, newest first: id, when, the parameters they were made with (None for builds made before these
        were remembered) and a summary of their report; ``current`` marks the one the model shows."""
        rec = self.get(pid)
        known = {h["id"]: h for h in rec.get("history", []) if h.get("id")}
        cur = (rec.get("build") or {}).get("id")
        out = []
        for bid in rec.get("builds", []):
            folder = self.build_dir(pid, bid)
            if not folder.is_dir():
                continue
            h = dict(known.get(bid) or {})
            if not h:
                rep = read_json(folder / "report.json", None) or {}
                h = {"id": bid, "finished": (folder / "report.json").stat().st_mtime if (folder / "report.json").exists() else None,
                     "report": summary(rep) if rep else None}
            if h.get("params") is None:
                h["params"] = read_json(folder / "params.json", None)
            h["current"] = bid == cur
            out.append(h)
        return out

    def use_build(self, pid: str, build_id: str) -> dict[str, Any]:
        """Show an earlier kept build again (and take back the parameters it was made with)."""
        with self._lock:
            rec = self._read(pid)
            if build_id not in rec.get("builds", []) or not self.build_dir(pid, build_id).is_dir():
                raise GulliverError("not_found", f"No kept build {build_id!r}.", "List them with model_builds.")
            entry = next((h for h in self.builds(pid) if h["id"] == build_id), None) or {"id": build_id}
            rec["build"] = {"id": build_id, "finished": entry.get("finished"), "report": entry.get("report")}
            if entry.get("params"):
                rec["params"] = entry["params"]
            rec["builds"] = [build_id] + [b for b in rec["builds"] if b != build_id]
            hist = rec.get("history", [])
            rec["history"] = [h for h in hist if h.get("id") == build_id] + [h for h in hist if h.get("id") != build_id]
            rec["status"] = "ready"
            self._write(rec)
            return rec

    def build_folder(self, pid: str, build_id: str = "") -> Optional[Path]:
        """The folder of a kept build (``build_id``) or of the current one."""
        if not build_id:
            return self.current_build_dir(pid)
        rec = self.get(pid)
        folder = self.build_dir(pid, build_id)
        if build_id not in rec.get("builds", []) or not folder.is_dir():
            raise GulliverError("not_found", f"No kept build {build_id!r}.", "List them with model_builds.")
        return folder

    def current_build_dir(self, pid: str) -> Optional[Path]:
        rec = self.get(pid)
        b = rec.get("build")
        return self.build_dir(pid, b["id"]) if b else None

    def report(self, pid: str, build_id: str = "") -> Optional[dict[str, Any]]:
        folder = self.build_folder(pid, build_id)
        return read_json(folder / "report.json", None) if folder else None

    def file(self, pid: str, name: str, build_id: str = "") -> Path:
        """A file of the current build (or of the kept build ``build_id``), by its name relative to the build folder (no
        path tricks)."""
        folder = self.build_folder(pid, build_id)
        if folder is None:
            raise GulliverError("not_found", "This model has not been built yet.", "Build it with model_build.")
        parts = Path(name.replace("\\", "/")).parts
        if not parts or any(p in ("..", "") or ":" in p for p in parts) or Path(name).is_absolute():
            raise GulliverError("invalid", "Bad file name.")
        path = folder.joinpath(*parts)
        if not path.is_file():
            raise GulliverError("not_found", f"No file {name!r} in the current build.")
        return path


def summary(report: dict[str, Any]) -> dict[str, Any]:
    keys = ("scale", "size_mm", "volume_cm3", "weight_g_pla", "triangles", "buildings", "fits_bed", "files", "check")
    out = {k: report.get(k) for k in keys}
    out["tiles"] = len(report.get("tiles") or [])
    out["parts"] = sorted((report.get("parts") or {}).keys())
    pr = report.get("printability") or {}
    out["score"] = pr.get("score")
    out["verdict"] = pr.get("verdict")
    out["fixes"] = len(pr.get("fixes") or [])
    return out
