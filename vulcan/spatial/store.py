"""Saved scenarios and runs under <data_dir>/spatial (plain JSON, written atomically)."""

from __future__ import annotations

import hashlib
import json
import re
import time
from pathlib import Path

from ..hoard_link.atomic import write_json_atomic
from .errors import SpatialError

SLUG = re.compile(r"^[a-z0-9][a-z0-9_-]{0,47}$")
KEEP_RUNS = 40


def _root(data_dir) -> Path:
    return Path(data_dir) / "spatial"


def _check_slug(value: str, what: str) -> str:
    if not isinstance(value, str) or not SLUG.match(value):
        raise SpatialError("invalid", f"{what} must be 1-48 characters: lowercase letters, digits, - and _.")
    return value


def save_scenario(data_dir, scenario_id: str, scenario: dict, *, replace: bool = False) -> dict:
    _check_slug(scenario_id, "scenario id")
    path = _root(data_dir) / "scenarios" / f"{scenario_id}.json"
    if path.exists() and not replace:
        raise SpatialError("conflict", f"Scenario '{scenario_id}' already exists.", hint="Pass replace=true to overwrite it.")
    if not isinstance(scenario, dict) or not isinstance(scenario.get("layers"), dict):
        raise SpatialError("invalid", "A scenario needs a 'layers' object.")
    path.parent.mkdir(parents=True, exist_ok=True)
    write_json_atomic(path, scenario)
    return {"id": scenario_id, "path": str(path), "bytes": path.stat().st_size}


def load_scenario(data_dir, scenario_id: str) -> dict:
    _check_slug(scenario_id, "scenario id")
    path = _root(data_dir) / "scenarios" / f"{scenario_id}.json"
    if not path.is_file():
        raise SpatialError("not_found", f"No saved scenario '{scenario_id}'.", hint="spatial_scenarios lists them.")
    return json.loads(path.read_text(encoding="utf-8"))


def delete_scenario(data_dir, scenario_id: str) -> None:
    _check_slug(scenario_id, "scenario id")
    path = _root(data_dir) / "scenarios" / f"{scenario_id}.json"
    if not path.is_file():
        raise SpatialError("not_found", f"No saved scenario '{scenario_id}'.")
    path.unlink()


def list_scenarios(data_dir) -> list[dict]:
    out = []
    for path in sorted((_root(data_dir) / "scenarios").glob("*.json")):
        try:
            sc = json.loads(path.read_text(encoding="utf-8"))
        except ValueError:
            continue
        out.append({"id": path.stem, "name": sc.get("name"), "description": sc.get("description"), "layers": sorted((sc.get("layers") or {})), "bytes": path.stat().st_size})
    return out


def save_run(data_dir, result: dict) -> str:
    folder = _root(data_dir) / "runs"
    folder.mkdir(parents=True, exist_ok=True)
    body = json.dumps(result, ensure_ascii=False, sort_keys=True)
    run_id = f"{result['kind']}-{time.strftime('%Y%m%d-%H%M%S')}-{hashlib.sha256(body.encode()).hexdigest()[:6]}"
    write_json_atomic(folder / f"{run_id}.json", {**result, "run_id": run_id, "saved_at": time.strftime("%Y-%m-%dT%H:%M:%S%z")})
    files = sorted(folder.glob("*.json"), key=lambda p: p.stat().st_mtime)
    for old in files[:-KEEP_RUNS]:
        old.unlink(missing_ok=True)
    return run_id


def load_run(data_dir, run_id: str) -> dict:
    if not re.fullmatch(r"[a-z]+-\d{8}-\d{6}-[0-9a-f]{6}", run_id or ""):
        raise SpatialError("invalid", "Bad run id.")
    path = _root(data_dir) / "runs" / f"{run_id}.json"
    if not path.is_file():
        raise SpatialError("not_found", f"No saved run '{run_id}'.")
    return json.loads(path.read_text(encoding="utf-8"))


def list_runs(data_dir) -> list[dict]:
    out = []
    for path in sorted((_root(data_dir) / "runs").glob("*.json"), key=lambda p: p.stat().st_mtime, reverse=True):
        try:
            r = json.loads(path.read_text(encoding="utf-8"))
        except ValueError:
            continue
        out.append({"run_id": path.stem, "kind": r.get("kind"), "scenario": r.get("scenario"), "saved_at": r.get("saved_at")})
    return out


def export_dir(data_dir) -> Path:
    folder = _root(data_dir) / "exports"
    folder.mkdir(parents=True, exist_ok=True)
    return folder
