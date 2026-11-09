"""Official Khronos glTF validation for every GLB/glTF that Vulcan indexes, exports or generates.

The validator is the Khronos reference implementation (npm ``gltf-validator``), run with Node through
``scripts/gltf_validate.mjs`` (one JSON report on stdout). Without Node or the package nothing breaks: the result is
``status = "not_installed"`` and callers carry on.

Reports are stored in ``<data dir>/gltf_validation/<sha256 of the file>.json`` so a library model, its web export and a
generated file are each validated once per content. ``summarize`` is the compact form shown as a badge:
``valid`` (no errors, no warnings), ``warnings`` (no errors) or ``errors``. Warnings never fail an export; errors only mark it.
"""

from __future__ import annotations

import hashlib
import json
import shutil
import subprocess
import time
from pathlib import Path

from .hoard_link import atomic


def _app_root() -> Path:
    import os
    import sys

    candidates = [os.environ.get("VULCAN_APP_DIR"), Path(__file__).resolve().parents[1], Path(sys.prefix).resolve().parent, Path.cwd()]
    for candidate in candidates:
        if candidate and (Path(candidate) / "scripts" / "gltf_validate.mjs").is_file():
            return Path(candidate)
    return Path(__file__).resolve().parents[1]


REPO = _app_root()
SCRIPT = REPO / "scripts" / "gltf_validate.mjs"
PACKAGE = REPO / "node_modules" / "gltf-validator" / "package.json"
TIMEOUT_S = 120
MAX_ISSUES = 200
SUFFIXES = (".glb", ".gltf")


def _node() -> str | None:
    return shutil.which("node")


def available() -> bool:
    return bool(_node()) and SCRIPT.is_file() and PACKAGE.is_file()


def status() -> dict:
    version = None
    if PACKAGE.is_file():
        try:
            version = json.loads(PACKAGE.read_text(encoding="utf-8")).get("version")
        except (OSError, ValueError):
            version = None
    return {"available": available(), "node": bool(_node()), "package": PACKAGE.is_file(), "validator_version": version,
            "note": None if available() else "gltf-validator needs Node.js and `npm install` in the app folder."}


def sha256_of(path: Path) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for block in iter(lambda: handle.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def is_gltf(path) -> bool:
    return Path(path).suffix.lower() in SUFFIXES


def validate_path(path, timeout: int = TIMEOUT_S, max_issues: int = MAX_ISSUES) -> dict:
    """Run the validator on one file. Never raises: the status says what happened
    (``done`` with counts, ``not_installed``, ``timeout``, ``error``, ``missing``)."""
    file = Path(path)
    if not file.is_file():
        return {"status": "missing", "message": f"File not found: {file}"}
    if not is_gltf(file):
        return {"status": "error", "message": "Only .glb and .gltf files can be validated."}
    if not available():
        return {"status": "not_installed", "message": status()["note"]}
    try:
        done = subprocess.run([_node(), str(SCRIPT), str(file), "--max-issues", str(max_issues)], cwd=str(REPO), capture_output=True,
                              text=True, encoding="utf-8", errors="replace", timeout=timeout,
                              creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    except subprocess.TimeoutExpired:
        return {"status": "timeout", "message": f"The validator took more than {timeout} s."}
    except OSError as error:
        return {"status": "not_installed", "message": f"Node could not be started: {error}"}
    try:
        report = json.loads(done.stdout)
    except ValueError:
        return {"status": "error", "message": (done.stderr or done.stdout or "The validator printed nothing.").strip()[-400:]}
    if not isinstance(report, dict) or "status" not in report:
        return {"status": "error", "message": "Unexpected validator output."}
    return report


def summarize(report: dict | None) -> dict:
    """Compact badge data: state (valid | warnings | errors | not_installed | error | none), counts and a short label."""
    if not report:
        return {"state": "none", "errors": 0, "warnings": 0, "infos": 0, "hints": 0, "label": "Not validated"}
    if report.get("status") != "done":
        state = "not_installed" if report.get("status") == "not_installed" else "error"
        return {"state": state, "errors": 0, "warnings": 0, "infos": 0, "hints": 0, "message": report.get("message"),
                "label": "Validator not installed" if state == "not_installed" else "Validation failed"}
    errors, warnings = int(report.get("errors", 0)), int(report.get("warnings", 0))
    state = "errors" if errors else ("warnings" if warnings else "valid")
    plural = lambda n, one, many: f"{n} {one if n == 1 else many}"  # noqa: E731
    label = "Valid glTF" if state == "valid" else (plural(errors, "error", "errors") if state == "errors" else plural(warnings, "warning", "warnings"))
    if errors and warnings:
        label += ", " + plural(warnings, "warning", "warnings")
    return {"state": state, "errors": errors, "warnings": warnings, "infos": int(report.get("infos", 0)), "hints": int(report.get("hints", 0)),
            "label": label}


def reports_dir(services) -> Path:
    return Path(services.config.data_dir) / "gltf_validation"


def report_path(services, sha256: str) -> Path:
    return reports_dir(services) / f"{sha256}.json"


def stored_report(services, path=None, sha256: str | None = None) -> dict | None:
    """The saved report for a file (by path, hashing it) or for a known SHA-256; None when it was never validated."""
    if sha256 is None:
        if path is None or not Path(path).is_file():
            return None
        sha256 = sha256_of(Path(path))
    saved = atomic.read_json(report_path(services, sha256), default=None)
    return {**saved, "cached": True} if isinstance(saved, dict) else None


def validate_and_store(services, path, force: bool = False, label: str | None = None) -> dict:
    """Validate (or reuse the saved report of the same content), store it and return it with a ``summary``.
    A ``not_installed`` result is returned but never stored, so installing the validator later works at once."""
    file = Path(path)
    if not file.is_file():
        report = {"status": "missing", "message": f"File not found: {file}"}
        return {**report, "path": str(file), "summary": summarize(report)}
    sha = sha256_of(file)
    if not force:
        saved = atomic.read_json(report_path(services, sha), default=None)
        if isinstance(saved, dict) and saved.get("status") == "done":
            return {**saved, "cached": True}
    report = validate_path(file)
    report.update(path=str(file), name=file.name, bytes=file.stat().st_size, sha256=sha, validated_at=time.time(), cached=False)
    if label:
        report["label"] = label
    report["summary"] = summarize(report)
    if report.get("status") == "done":
        atomic.write_json_atomic(report_path(services, sha), report)
    return report


def validate_export(services, path, label: str | None = None, force: bool = False) -> dict | None:
    """Hook for code that writes a GLB: validates it, never raises and never fails the export (warnings or errors are only
    recorded). Returns the compact summary to embed in the export's own metadata, or None when something unexpected happened."""
    try:
        report = validate_and_store(services, path, force=force, label=label)
        return {**report["summary"], "validator_version": report.get("validator_version"), "sha256": report.get("sha256"),
                "validated_at": report.get("validated_at")}
    except Exception as error:  # noqa: BLE001 - an export must never fail because of its check
        return {"state": "error", "errors": 0, "warnings": 0, "infos": 0, "hints": 0, "label": "Validation failed", "message": str(error)[:200]}


def validate_beside(path, name: str = "gltf-validation.json") -> dict | None:
    """For writers without a Vulcan services object (Gulliver): validate the GLB, save the full report as ``name`` in the same folder
    and return the compact summary. Never raises."""
    try:
        file = Path(path)
        report = validate_path(file)
        report.update(path=str(file), name=file.name, bytes=file.stat().st_size if file.is_file() else None, validated_at=time.time())
        report["summary"] = summarize(report)
        if report.get("status") == "done":
            atomic.write_json_atomic(file.with_name(name), report)
        return {**report["summary"], "validator_version": report.get("validator_version")}
    except Exception as error:  # noqa: BLE001
        return {"state": "error", "errors": 0, "warnings": 0, "infos": 0, "hints": 0, "label": "Validation failed", "message": str(error)[:200]}


def validate_model(services, model_id: int, force: bool = False) -> dict:
    """Validate a library model or variant by id. GLB/glTF models are validated as they are; any other format (STL, OBJ, 3MF)
    is validated through its web GLB export (the glTF Vulcan would hand out), which is built first when missing."""
    model = services.models.get(model_id)
    if model is None:
        raise LookupError(f"Model {model_id} does not exist.")
    path = Path(model["path"])
    if not path.is_file():
        raise LookupError(f"The file of model {model_id} is no longer on disk: {path}")
    if is_gltf(path):
        return {"model_id": model_id, "source": "model_file", **validate_and_store(services, path, force)}
    from .simplify import export_web_glb

    export = export_web_glb(services, model_id)
    return {"model_id": model_id, "source": "web_glb", "exported_from": model["format"], **validate_and_store(services, export["path"], force)}


def model_report(services, model_id: int) -> dict:
    """Saved report of a model (its own GLB/glTF, or its web GLB) without running the validator: {"validated": False} if none yet."""
    model = services.models.get(model_id)
    if model is None:
        raise LookupError(f"Model {model_id} does not exist.")
    path = Path(model["path"])
    source = "model_file"
    if not is_gltf(path):
        from .simplify import web_glb_path

        path, source = web_glb_path(services, model), "web_glb"
    saved = stored_report(services, path) if path.is_file() else None
    if not saved:
        return {"model_id": model_id, "source": source, "validated": False, "summary": summarize(None), "status": status()}
    return {"model_id": model_id, "source": source, "validated": True, "summary": summarize(saved), **saved}
