"""Model with code: a description (or a program) becomes a 3D part written as a short sand3d program.

sand3d is a Python library over Blender's ``bpy`` made for language models: objects are built by a few verbs, in millimetres,
and exported as GLB, STL, OBJ, USDA or URDF. ``bpy`` has no wheel for every Python, so the engine lives in its own virtual
environment (``.sand3d-venv`` next to the app, or ``<data dir>/engines/sand3d-venv``, or the Python in ``VULCAN_SAND3D_PYTHON``)
and every program runs there, in a separate process, in a temporary folder, with a time limit and a reduced set of builtins
and modules (see ``sand3d_runner.py``). The app's own virtual environment is never touched.

Flow of ``code_model``: the family's language model gets the sand3d reference (``sand3d_api.md``), the two or three tested
examples closest to the description (``sand3d_examples/``, picked by ``code_checks.pick_examples``), the description and the
measures parsed from it, and writes a program; the program runs; if it fails the error goes back to the model and it tries again
(3 attempts). When it runs, the mesh is measured (bounding box of the part and of its main body, sampled wall thickness, round
openings and outlines in sections) and checked against the requested measures; a miss goes back to the model with the measured
values for one more attempt, and the best result is kept. The STL (converted to millimetres) and the GLB are written, the GLB is
checked with the Khronos validator, a preview is rendered and the part is saved in the library with its source program beside it
so it can be edited and regenerated. ``code_model_run`` runs a program that is given (no language model, optional ``params``);
``code_model_edit`` changes a saved part from an instruction: an instruction that is only a measure changes the right named
parameter directly, anything else goes to the model with the program and its parameters.

Per part, in ``<data dir>/code_models/``: ``<slug>-<id>.stl`` (the mesh the library indexes, millimetres), ``.glb`` (metres, as
glTF wants), ``.s3d.py`` (the program), ``.preview.png`` and ``.code.json`` (description, history, measures, validation).
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import unicodedata
import uuid
from datetime import datetime, timezone
from pathlib import Path

import numpy as np

from . import cad
from . import code_checks as checks
from .family_tools import import_file, model_ref
from .gltf_validate import validate_export
from .hoard_link import atomic, proc
from .sand3d_runner import check_program
from .studio.global_http import job as global_job
from .thumbnail import render

RUNNER = Path(__file__).with_name("sand3d_runner.py")
API_DOC = Path(__file__).with_name("sand3d_api.md")
REPO = Path(__file__).resolve().parent.parent
MAX_ATTEMPTS = 3       # model calls to get a program that runs
MEASURE_RETRIES = 1    # one more call when it runs but misses the requested measures
EXAMPLES_IN_PROMPT = 3
RUN_TIMEOUT_S = float(os.environ.get("VULCAN_CODE_TIMEOUT", "300"))  # a busy machine can take a minute just to import bpy
SRC_LIMIT = 30_000
HISTORY_LIMIT = 20
VERSIONS_LIMIT = 5
TO_MM = 1000.0  # sand3d writes GLB/STL in metres
STAGES = ("queued", "writing", "running", "measuring", "exporting", "validating", "saving", "done")
_LOCK = threading.RLock()
_ENGINE_LOCK = threading.Lock()
_JOBS: dict[str, dict] = {}
_CANCEL: set[str] = set()
_INFO_CACHE: dict[str, tuple[float, dict]] = {}

SYSTEM_PROMPT = """You write 3D models as short Python programs with the sand3d library (Blender's bpy underneath). Reply with ONE ```python code block and nothing else.

Rules:
- Start with `import sand3d as s3d`. Only sand3d, math, itertools, functools, typing, dataclasses, collections, enum and random may be imported. No files, no bpy, no os, no exec/eval/open.
- Units are millimetres. Put every key dimension in a named top-level variable at the top, one per line or as `w, d, h = 60, 40, 30`, with a comment saying what it is (`t = 3  # mm, wall thickness`); derive the rest from them. Build at top level (or call your function with those variables) so changing a variable changes the part.
- The sizes in the description are the outside of the finished part (or of its main body when it has several, like a box and its lid): make the bounding box come out exactly at those numbers. A thickness asked for is the thickness of the walls or plates.
- Assign the final solid, group or assembly to a variable named `model`. Do not call s3d.export: the app exports it.
- Unless asked otherwise the part is for 3D printing: one connected body (or separate parts laid side by side on the bed), walls at least 1.2 mm thick, a flat face on z = 0, no floating pieces, nothing thinner than 0.8 mm, overhangs under 45 degrees. Lay a part so it prints strong (a bent bracket or a hook prints on its side: its outline extruded by its width).
- Use what a verb returns (`at` returns the placed object; objects used up by cut/join/intersect no longer exist). Every profile or solid you build must end up in `model`.
- Follow the worked examples below when one is close: they are tested and show working patterns (bent bands with stroke, cavities cut from a solid, revolved sections, holes made with rotated cylinders).
- Short comments in English are welcome. Keep the program under 120 lines.

sand3d reference:

"""

# ---------------------------------------------------------------------------------------------------------------------
class CodeModelError(RuntimeError):
    def __init__(self, code: str, message: str, **details):
        super().__init__(message)
        self.code, self.details = code, details


def _now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def _slug(name: str) -> str:
    text = unicodedata.normalize("NFKD", name or "part").encode("ascii", "ignore").decode()
    text = re.sub(r"[^A-Za-z0-9]+", "-", text).strip("-").lower()
    return (text or "part")[:40]


def api_doc() -> str:
    return API_DOC.read_text(encoding="utf-8")


def system_prompt(examples: list[dict] | None = None) -> str:
    return SYSTEM_PROMPT + api_doc() + checks.examples_prompt(examples or [])


def extract_code(text: str) -> str:
    """The program inside the first python code block of a reply (or the whole reply when it has no fence)."""
    text = (text or "").strip()
    match = re.search(r"```(?:python|py)?[ \t]*\r?\n(.*?)```", text, re.S | re.I)
    if match:
        return match.group(1).strip("\n") + "\n"
    if text.startswith("```"):  # unterminated fence
        return text.split("\n", 1)[1].strip("`\n") + "\n" if "\n" in text else ""
    return text + "\n" if "s3d" in text or "sand3d" in text else ""  # prose with no program in it is not code


# ---------------------------------------------------------------------------------------------------------------------
# the engine
def _python_in(venv: Path) -> Path:
    return venv / ("Scripts/python.exe" if os.name == "nt" else "bin/python")


def engine_candidates(data_dir: Path | None = None) -> list[Path]:
    out: list[Path] = []
    explicit = os.environ.get("VULCAN_SAND3D_PYTHON", "").strip().strip('"')
    if explicit:
        out.append(Path(explicit))
    out.append(_python_in(REPO / ".sand3d-venv"))
    if data_dir:
        out.append(_python_in(Path(data_dir) / "engines" / "sand3d-venv"))
    return out


def find_engine(data_dir: Path | None = None) -> Path | None:
    return next((p for p in engine_candidates(data_dir) if p.is_file()), None)


def _engine_env() -> dict[str, str]:
    keep = ("SYSTEMROOT", "SystemRoot", "TEMP", "TMP", "PATH", "PATHEXT", "COMSPEC", "USERPROFILE", "HOME", "LOCALAPPDATA", "APPDATA", "NUMBER_OF_PROCESSORS")
    env = {k: v for k, v in os.environ.items() if k in keep}
    env.update(PYTHONUTF8="1", PYTHONIOENCODING="utf-8", BLENDER_USER_RESOURCES=str(Path(tempfile.gettempdir()) / "vulcan-sand3d-blender"))
    return env


def engine_info(data_dir: Path | None = None, refresh: bool = False) -> dict:
    """What the engine python has: sand3d and bpy versions (cached; a short subprocess)."""
    python = find_engine(data_dir)
    if python is None:
        return {"installed": False, "python": None, "candidates": [str(p) for p in engine_candidates(data_dir)],
                "note": "The sand3d engine is not installed: use the install button (or code_model_status install=true) to create its environment."}
    try:
        stamp = f"{python}:{python.stat().st_mtime}"
    except OSError:
        stamp = f"{python}:0"
    cached = _INFO_CACHE.get(stamp)
    if cached and not refresh and time.time() - cached[0] < (600 if cached[1].get("installed") and not cached[1].get("unverified") else 30):
        return cached[1]
    probe = "import json,sys,sand3d,bpy;print(json.dumps({'sand3d':sand3d.__version__,'bpy':bpy.app.version_string,'python':sys.version.split()[0]}))"
    try:
        done = proc.run([str(python), "-I", "-c", probe], timeout=90, env=_engine_env())
        line = [x for x in (done.stdout or "").splitlines() if x.startswith("{")]
        if done.returncode != 0 or not line:
            raise RuntimeError((done.stderr or done.stdout or "no output").strip()[-300:])
        info = {"installed": True, "python": str(python), **json.loads(line[-1])}
    except subprocess.TimeoutExpired:  # a busy machine can take longer than that to import bpy: the engine is there, just slow
        info = {"installed": True, "unverified": True, "python": str(python),
                "note": "The engine did not answer the version check in 90 s (busy machine); it is assumed installed and checked again shortly."}
    except Exception as error:  # noqa: BLE001 - a broken environment is reported, not raised
        info = {"installed": False, "python": str(python), "broken": True, "error": f"{type(error).__name__}: {error}"[:400],
                "note": "The engine environment exists but sand3d/bpy cannot be imported: reinstall it."}
    _INFO_CACHE[stamp] = (time.time(), info)
    return info


def run_engine(data_dir: Path | None, code: str, timeout: float | None = None, cancel: threading.Event | None = None) -> dict:
    """Run one program in the engine. Returns ``{"work": Path, "glb": Path, "stl": Path, "meta": dict}``; the caller removes ``work``.
    Raises CodeModelError(kind in engine_missing | rejected | program | timeout | engine, message, line=...)."""
    python = find_engine(data_dir)
    if python is None:
        raise CodeModelError("engine_missing", "The sand3d engine is not installed.")
    if len(code) > SRC_LIMIT:
        raise CodeModelError("rejected", f"The program is longer than {SRC_LIMIT} characters.")
    problems = check_program(code)
    if problems:
        raise CodeModelError("rejected", "; ".join(problems))
    work = Path(tempfile.mkdtemp(prefix="vulcan-s3d-"))
    try:
        (work / "job.json").write_text(json.dumps({"code": code, "work": str(work)}), encoding="utf-8")
        limit = float(timeout or RUN_TIMEOUT_S)
        try:
            with _ENGINE_LOCK:
                done = proc.run([str(python), "-I", str(RUNNER), str(work / "job.json")], timeout=limit, cwd=work, env=_engine_env(), cancel=cancel)
        except subprocess.TimeoutExpired as error:
            raise CodeModelError("timeout", f"The program took longer than {int(limit)} s and was stopped (a loop with no end, or a far too detailed shape?).") from error
        error_file, result_file = work / "error.json", work / "result.json"
        if error_file.is_file():
            data = json.loads(error_file.read_text(encoding="utf-8"))
            raise CodeModelError(data.get("kind", "program"), data.get("message", "The program failed."), line=data.get("line"),
                                 code_line=data.get("code_line"), stdout=data.get("stdout"))
        if done.returncode != 0 or not result_file.is_file():
            tail = ((done.stderr or "") + (done.stdout or "")).strip()[-600:]
            raise CodeModelError("engine", f"The engine stopped without a result (exit {done.returncode}). {tail}")
        meta = json.loads(result_file.read_text(encoding="utf-8"))
        return {"work": work, "glb": work / "out.glb", "stl": work / "out.stl", "meta": meta}
    except BaseException:
        shutil.rmtree(work, ignore_errors=True)
        raise


# ---------------------------------------------------------------------------------------------------------------------
# files and library
def code_dir(services) -> Path:
    return services.config.data_dir / "code_models"


def _files(services, stem: str) -> dict:
    folder = code_dir(services)
    return {"stl": folder / f"{stem}.stl", "glb": folder / f"{stem}.glb", "code": folder / f"{stem}.s3d.py",
            "png": folder / f"{stem}.preview.png", "record": folder / f"{stem}.code.json"}


def _mesh_mm(stl: Path) -> tuple[np.ndarray, np.ndarray]:
    import trimesh

    mesh = trimesh.load(str(stl), file_type="stl", process=True)  # STL repeats every vertex: merge them so watertightness and bodies are real
    vertices = np.asarray(mesh.vertices, dtype=np.float64) * TO_MM
    return vertices, np.asarray(mesh.faces, dtype=np.int64)


def _urls(model_id: int) -> dict:
    base = f"/api/code-model/{model_id}"
    return {"preview_url": f"{base}/preview.png", "glb_url": f"{base}/model.glb", "stl_url": f"/api/models/{model_id}/file", "code_url": f"{base}/code",
            "model_url": f"#/modelo/{model_id}", "editor_url": f"#/codigo/{model_id}"}


def check_run(run: dict, reqs: list[dict]) -> tuple[dict, dict]:
    """(measured, check) of a built run against the requested measures (walls and round features only measured when asked)."""
    vertices, faces = _mesh_mm(run["stl"])
    kinds = {r["kind"] for r in reqs}
    measured = checks.measure_part(vertices, faces, want_rounds=bool(kinds & {"hole", "round"}), want_walls="thickness" in kinds)
    return measured, checks.verify(reqs, measured)


def save_result(services, run: dict, code: str, name: str, description: str, *, model_id: int | None = None, kind: str = "create",
                instruction: str | None = None, attempts: int = 1, notes_from: str = "program", progress=None,
                requirements: list[dict] | None = None, check: dict | None = None, extra: dict | None = None) -> dict:
    """Write the files of a successful run and index the STL (a new library model, or the same model id when ``model_id`` is given).
    ``requirements``/``check``: the measures asked for and how the part met them (checked here when only requirements are given);
    ``extra`` (examples used, parameter edit) is stored in the record and returned."""
    folder = code_dir(services)
    folder.mkdir(parents=True, exist_ok=True)
    previous = None
    if model_id is not None:
        stem, previous = _stem_and_record(services, model_id)
    else:
        stem = f"{_slug(name)}-{uuid.uuid4().hex[:8]}"
    files = _files(services, stem)
    if progress:
        progress("exporting")
    vertices, faces = _mesh_mm(run["stl"])
    if len(faces) == 0:
        raise CodeModelError("empty", "The program built an object with no surface.")
    measures = cad.measure(vertices, faces)
    atomic.write_bytes_atomic(files["stl"], cad.stl_bytes(vertices, faces))
    atomic.write_bytes_atomic(files["glb"], Path(run["glb"]).read_bytes())
    atomic.write_bytes_atomic(files["code"], code.encode("utf-8"))
    image = render(vertices, faces, 640, background=(244, 243, 238), adaptive=False)
    tmp = files["png"].with_name(files["png"].stem + ".tmp.png")
    image.convert("RGB").save(tmp, "PNG")
    atomic.replace_with_retry(tmp, files["png"])
    if progress:
        progress("validating")
    validation = validate_export(services, files["glb"], label="Code model")
    warnings = []
    if not measures["watertight"]:
        warnings.append("the mesh is not watertight; slicers usually repair it, but check it before printing")
    if measures["bodies"] > 1:
        warnings.append(f"the model has {measures['bodies']} separate bodies: check that nothing floats")
    if validation and validation.get("state") == "errors":
        warnings.append("the GLB has glTF validation errors: see the validation report")
    if requirements and check is None:
        _, check = check_run(run, requirements)
    if check and check.get("ok") is False:
        missed = [i["requirement"] for i in check["items"] if i["ok"] is False]
        warnings.append("requested measures not met: " + "; ".join(missed))
    history = list((previous or {}).get("history", []))
    history.append({"at": _now(), "kind": kind, "instruction": instruction, "attempts": attempts, "bbox_mm": measures["bbox_mm"], "volume_mm3": measures["volume_mm3"],
                    "measures_ok": check.get("ok") if check else None, **({"parameters_changed": extra["parameter_edit"]["changed"]}
                                                                         if extra and extra.get("parameter_edit") else {})})
    versions = list((previous or {}).get("previous_codes", []))
    if previous and previous.get("code") and previous["code"] != code:
        versions.append({"at": previous.get("updated_at"), "code": previous["code"]})
    record = {"version": 1, "name": name, "description": description or (previous or {}).get("description", ""), "created_at": (previous or {}).get("created_at") or _now(),
              "updated_at": _now(), "code": code, "engine": run["meta"], "measures": measures, "validation": validation, "warnings": warnings,
              "history": history[-HISTORY_LIMIT:], "previous_codes": versions[-VERSIONS_LIMIT:],
              "requirements": requirements if requirements is not None else (previous or {}).get("requirements", []),
              "requirements_check": check if check is not None else None, "examples": (previous or {}).get("examples"), **(extra or {})}
    atomic.write_json_atomic(files["record"], record)
    if progress:
        progress("saving")
    imported = import_file(services, str(files["stl"]))
    if previous is None:
        services.models.tag(imported["id"], ["codigo", "sand3d"], [])
    desc = (description or "")[:300]
    services.models.patch(imported["id"], {"name": name[:120], "notes": "Model written as a sand3d program" + (f" from: {desc}" if desc else "")
                                          + ". Open it in Modelar con código to edit the code and regenerate it."})
    return {"ok": True, "id": imported["id"], "ref": imported["ref"], "name": name, "attempts": attempts, "measures": measures, "gltf_validation": validation,
            "warnings": warnings, "engine": run["meta"], "paths": {k: str(v) for k, v in files.items()}, **_urls(imported["id"]),
            "requirements_check": check, "parameters": checks.public_parameters(code), **(extra or {}),
            "library": {k: imported[k] for k in ("status", "bbox", "triangles", "volume_cm3", "watertight", "bodies", "has_thumb")}}


def _stem_and_record(services, model_id: int) -> tuple[str, dict]:
    model = services.models.get(model_id)
    if model is None:
        raise LookupError(f"Model {model_id} does not exist.")
    path = Path(model["path"])
    try:
        inside = path.resolve().parent == code_dir(services).resolve()
    except OSError:
        inside = False
    files = _files(services, path.stem)
    record = atomic.read_json(files["record"], default=None) if inside else None
    if not isinstance(record, dict) or not isinstance(record.get("code"), str):
        raise LookupError(f"Model {model_id} was not written with code_model, so it has no program to edit.")
    return path.stem, record


def is_code_model(services, model_id: int) -> bool:
    try:
        _stem_and_record(services, model_id)
        return True
    except (LookupError, ValueError):
        return False


def info(services, model_id: int, with_code: bool = True) -> dict:
    stem, record = _stem_and_record(services, model_id)
    out = {k: record.get(k) for k in ("name", "description", "created_at", "updated_at", "measures", "validation", "warnings", "history", "engine",
                                      "requirements", "requirements_check", "examples", "parameter_edit")}
    out.update(ok=True, id=model_id, ref=model_ref(model_id), parameters=checks.public_parameters(record["code"]), **_urls(model_id))
    if with_code:
        out["code"] = record["code"]
        out["previous_codes"] = record.get("previous_codes", [])
    return out


def file_path(services, model_id: int, what: str) -> Path:
    stem, _ = _stem_and_record(services, model_id)
    key = {"preview.png": "png", "model.glb": "glb", "code": "code"}.get(what)
    path = _files(services, stem).get(key or "")
    if not path or not path.is_file():
        raise LookupError("No such file.")
    return path


# ---------------------------------------------------------------------------------------------------------------------
# the language model
@global_job
def _ask(link, messages: list[dict]) -> str:
    # a program is 40-100 lines: little thinking is needed, and a reasoning model that spends its whole budget thinking ends with finish_reason=length
    answer = link.sync.chat(messages, max_tokens=12000, effort="low")
    return answer.text or ""


def _retry_message(error: CodeModelError) -> str:
    where = f" (line {error.details['line']}: `{error.details['code_line']}`)" if error.details.get("line") and error.details.get("code_line") else ""
    return f"That program failed{where}:\n{error}\nReturn the complete corrected program in one python block."


def write_program(services, messages: list[dict], log, cancelled, name: str, description: str, progress, kind: str, instruction: str | None,
                  model_id: int | None, requirements: list[dict] | None = None, extra: dict | None = None) -> dict:
    """The loop: ask the model, run, hand errors back (MAX_ATTEMPTS calls to get a program that runs); when it runs, measure it
    against ``requirements`` and hand the misses back once more (MEASURE_RETRIES). The best run (most measures met, then the
    latest) is saved. Returns the saved result with ``requirements_check``."""
    requirements = requirements or []
    last: CodeModelError | None = None
    failures = measure_retries = attempt = 0
    best: dict | None = None  # {"run", "code", "check", "attempt", "met"}

    def keep(candidate: dict) -> None:
        nonlocal best
        if best is None or candidate["met"] >= best["met"]:
            if best is not None:
                shutil.rmtree(best["run"]["work"], ignore_errors=True)
            best = candidate
        else:
            shutil.rmtree(candidate["run"]["work"], ignore_errors=True)

    try:
        while failures < MAX_ATTEMPTS and attempt < MAX_ATTEMPTS + MEASURE_RETRIES:
            attempt += 1
            if cancelled():
                raise CodeModelError("cancelled", "Cancelled.")
            progress("writing", attempt=attempt)
            try:
                text = _ask(services.link, messages)
            except Exception as error:  # noqa: BLE001 - Unavailable, BackendError, timeouts
                if best is not None:
                    log(f"attempt {attempt}: no model answer, keeping the best program so far")
                    break
                raise CodeModelError("model_unavailable", f"No language model answered ({type(error).__name__}: {error}). Write the program yourself "
                                     "(see code_model_status for the reference) and call code_model_run with code=...") from error
            code = extract_code(text)
            if not code.strip():
                failures += 1
                last = CodeModelError("program", "The reply had no python code.")
                messages = messages + [{"role": "assistant", "content": (text or "")[:4000]}, {"role": "user", "content": _retry_message(last)}]
                log(f"attempt {attempt}: no code in the reply")
                if best is not None:
                    break
                continue
            progress("running", attempt=attempt)
            try:
                run = run_engine(services.config.data_dir, code)
            except CodeModelError as error:
                if error.code in ("engine_missing", "engine", "cancelled"):
                    raise
                failures += 1
                last = error
                log(f"attempt {attempt}: {error.code}: {str(error)[:200]}")
                if best is not None:  # the measure fix broke the program: the earlier part stands
                    break
                messages = messages + [{"role": "assistant", "content": f"```python\n{code}```"}, {"role": "user", "content": _retry_message(error)}]
                continue
            if not requirements:
                best = {"run": run, "code": code, "check": None, "attempt": attempt, "met": 0}
                break
            progress("measuring", attempt=attempt)
            try:
                measured, check = check_run(run, requirements)
            except Exception as error:  # noqa: BLE001 - a measuring failure never loses a working part
                log(f"attempt {attempt}: measuring failed: {type(error).__name__}: {error}"[:200])
                best = {"run": run, "code": code, "check": None, "attempt": attempt, "met": 0}
                break
            log(f"attempt {attempt}: measures {check['met']}/{check['total']} met")
            keep({"run": run, "code": code, "check": check, "attempt": attempt, "met": check["met"]})
            if check["ok"] is not False or measure_retries >= MEASURE_RETRIES:
                break
            measure_retries += 1
            messages = messages + [{"role": "assistant", "content": f"```python\n{code}```"}, {"role": "user", "content": checks.feedback(check, measured)}]
        if best is None:
            raise CodeModelError("model_invalid", f"The model did not produce a working program in {MAX_ATTEMPTS} attempts. Last error: {last}. "
                                 "Write or fix the program yourself and call code_model_run with code=...", attempts=MAX_ATTEMPTS)
        chosen = best
        result = save_result(services, chosen["run"], chosen["code"], name, description, model_id=model_id, kind=kind, instruction=instruction,
                             attempts=attempt, progress=lambda stage: progress(stage, attempt=attempt), requirements=requirements,
                             check=chosen["check"], extra=extra)
        result["code"] = chosen["code"]
        result["measure_retries"] = measure_retries
        log(f"attempt {attempt}: saved the program of attempt {chosen['attempt']}")
        return result
    finally:
        if best is not None:
            shutil.rmtree(best["run"]["work"], ignore_errors=True)


# ---------------------------------------------------------------------------------------------------------------------
# jobs
def jobs_dir(services) -> Path:
    return code_dir(services) / "jobs"


def _save_job(services, job: dict) -> None:
    jobs_dir(services).mkdir(parents=True, exist_ok=True)
    atomic.write_json_atomic(jobs_dir(services) / f"{job['job_id']}.json", {k: v for k, v in job.items() if not k.startswith("_")})


def _update(services, job: dict, **fields) -> None:
    with _LOCK:
        job.update(fields)
        job["updated_at"] = time.time()
        _save_job(services, job)


def job_view(job: dict) -> dict:
    keys = ("job_id", "kind", "status", "stage", "attempt", "max_attempts", "name", "description", "instruction", "model_id", "created_at", "updated_at",
            "error", "error_code", "result", "requirements", "examples", "edit_mode")
    view = {k: job.get(k) for k in keys}
    view["log"] = (job.get("log") or [])[-12:]
    view["ok"] = job.get("status") != "failed"
    return view


def get_job(services, job_id: str) -> dict:
    with _LOCK:
        job = _JOBS.get(job_id)
    if job is None:
        if not re.fullmatch(r"[0-9a-f]{12}", job_id or ""):
            raise LookupError("No such code model job.")
        job = atomic.read_json(jobs_dir(services) / f"{job_id}.json", None)
        if not job:
            raise LookupError("No such code model job.")
        if job.get("status") in ("queued", "running"):
            job["status"], job["error"], job["error_code"] = "failed", "The app restarted while the job was running.", "interrupted"
    return job_view(job)


def list_jobs(services, model_id: int | None = None, limit: int = 10) -> list[dict]:
    out = []
    root = jobs_dir(services)
    for file in root.glob("*.json") if root.is_dir() else []:
        try:
            view = get_job(services, file.stem)
        except LookupError:
            continue
        if model_id is None or view.get("model_id") == model_id:
            out.append(view)
    out.sort(key=lambda j: j.get("updated_at") or 0, reverse=True)
    return out[:limit]


def cancel_job(services, job_id: str) -> dict:
    _CANCEL.add(job_id)
    return get_job(services, job_id)


def _run_job(services, job: dict) -> None:
    def log(message: str) -> None:
        job["log"].append(f"{time.strftime('%H:%M:%S')} {message}")

    def progress(stage: str, attempt: int | None = None) -> None:
        extra = {"attempt": attempt} if attempt else {}
        log(stage + (f" (attempt {attempt})" if attempt else ""))
        _update(services, job, status="running", stage=stage, **extra)

    try:
        if job.get("_code") is not None:  # an edit that is only a measure: the named parameter changed, no language model
            result = _run_parameter_edit(services, job, progress, log)
        else:
            result = write_program(services, job["_messages"], log, lambda: job["job_id"] in _CANCEL, job["name"], job["description"], progress,
                                   job["kind"], job.get("instruction"), job.get("model_id"), requirements=job.get("_requirements"),
                                   extra=job.get("_extra"))
        result.pop("paths", None)
        _update(services, job, status="done", stage="done", result=result, model_id=result["id"])
    except CodeModelError as error:
        log(f"failed: {error.code}")
        _update(services, job, status="cancelled" if error.code == "cancelled" else "failed", error=str(error)[:900], error_code=error.code)
    except Exception as error:  # noqa: BLE001 - a job never dies silently
        log(f"failed: {type(error).__name__}: {error}")
        _update(services, job, status="failed", error=f"{type(error).__name__}: {error}"[:500], error_code="unexpected")
    finally:
        _CANCEL.discard(job["job_id"])
        for key in ("_messages", "_code", "_requirements", "_extra"):
            job.pop(key, None)


def _run_parameter_edit(services, job: dict, progress, log) -> dict:
    progress("running", attempt=1)
    code = job["_code"]
    run = run_engine(services.config.data_dir, code)
    try:
        requirements = job.get("_requirements") or []
        check = None
        if requirements:
            progress("measuring", attempt=1)
            _, check = check_run(run, requirements)
        result = save_result(services, run, code, job["name"], job["description"], model_id=job["model_id"], kind="edit", instruction=job.get("instruction"),
                             attempts=0, progress=lambda stage: progress(stage, attempt=1), requirements=requirements, check=check, extra=job.get("_extra"))
    finally:
        shutil.rmtree(run["work"], ignore_errors=True)
    changed = ", ".join(f"{c['name']} {c['from']:g} -> {c['to']:g}" for c in job["_extra"]["parameter_edit"]["changed"])
    log(f"parameters changed without the language model: {changed}")
    result["code"] = code
    return result


def _start(services, kind: str, messages: list[dict] | None, name: str, description: str, model_id: int | None, instruction: str | None, wait: bool,
           wait_timeout: float = 1800.0, requirements: list[dict] | None = None, extra: dict | None = None, code: str | None = None) -> dict:
    job = {"job_id": uuid.uuid4().hex[:12], "kind": kind, "status": "queued", "stage": "queued", "attempt": 0,
           "max_attempts": 1 if code is not None else MAX_ATTEMPTS + (MEASURE_RETRIES if requirements else 0), "name": name,
           "description": description, "instruction": instruction, "model_id": model_id, "created_at": time.time(), "updated_at": time.time(), "log": [],
           "result": None, "error": None, "requirements": requirements or [], "examples": (extra or {}).get("examples"),
           "edit_mode": ("parameters" if code is not None else "model") if kind == "edit" else None,
           "_messages": messages, "_requirements": requirements or [], "_extra": extra, "_code": code}
    with _LOCK:
        _JOBS[job["job_id"]] = job
    thread = threading.Thread(target=_run_job, args=(services, job), name=f"code-model-{job['job_id']}", daemon=True)
    thread.start()
    if wait:
        thread.join(wait_timeout)
    return job_view(job)


def _check_ready(services) -> None:
    if find_engine(services.config.data_dir) is None:
        raise CodeModelError("engine_missing", "The sand3d engine is not installed: install it from Modelar con código (or code_model_status install=true).")


def _default_name(description: str, name: str | None) -> str:
    return (name or "").strip()[:80] or " ".join((description or "").split())[:60] or "Modelo con código"


def start_from_text(services, description: str, name: str | None = None, wait: bool = False) -> dict:
    description = (description or "").strip()
    if len(description) < 3:
        raise ValueError("Describe the part (what it is, its size in mm, how thick).")
    _check_ready(services)
    examples = checks.pick_examples(description, EXAMPLES_IN_PROMPT)
    requirements = checks.parse_requirements(description)
    messages = [{"role": "system", "content": system_prompt(examples)},
                {"role": "user", "content": description + checks.requirements_prompt(requirements)}]
    extra = {"examples": [{"id": e["id"], "title": e["title"]} for e in examples]}
    return _start(services, "create", messages, _default_name(description, name), description, None, None, wait, requirements=requirements, extra=extra)


def _parameters_text(code: str) -> str:
    params = checks.program_parameters(code)
    if not params:
        return "The program has no named numeric parameters yet."
    rows = [f"- {p['name']} = {p['value']:g}" + (f" (in {p['scope']}())" if p["scope"] else "") + (f"  # {p['comment']}" if p["comment"] else "")
            for p in params]
    return "Its named parameters:\n" + "\n".join(rows)


def plan_edit(code: str, instruction: str) -> dict | None:
    """The parameter change an instruction asks for, when it only asks for measures: {code, changed, reasons}; else None."""
    plan = checks.parameter_edit(instruction, code)
    if not plan:
        return None
    new_code, changed = checks.set_parameters(code, plan["changes"])
    return {"code": new_code, "changed": changed, "reasons": plan["reasons"]}


def start_edit(services, model_id: int, instruction: str, wait: bool = False) -> dict:
    """Change a saved part. An instruction that only asks for measures ("hazla de 5 mm de grosor", "80 mm wide") changes the matching
    named parameter and rebuilds without the language model; anything else goes to the model with the program and its parameters."""
    instruction = (instruction or "").strip()
    if len(instruction) < 3:
        raise ValueError("Say what to change (for example: make the plate 5 mm thicker).")
    _, record = _stem_and_record(services, model_id)
    _check_ready(services)
    name, description = record.get("name") or "Modelo con código", record.get("description", "")
    requirements = checks.parse_requirements(instruction)
    plan = plan_edit(record["code"], instruction)
    if plan:
        extra = {"parameter_edit": {"changed": plan["changed"], "reasons": plan["reasons"], "mode": "parameters"}}
        return _start(services, "edit", None, name, description, model_id, instruction, wait, requirements=requirements, extra=extra, code=plan["code"])
    user = (f"Current program of the part:\n```python\n{record['code']}```\n{_parameters_text(record['code'])}\n\n"
            "Change it as follows and reply with the complete new program. Keep the named parameters and their names; when the change is a "
            "size, change the parameter that holds it rather than rewriting the part; keep everything that is not mentioned the same:\n"
            f"{instruction}" + checks.requirements_prompt(requirements))
    examples = checks.pick_examples(description + " " + instruction, 2)
    messages = [{"role": "system", "content": system_prompt(examples)}, {"role": "user", "content": user}]
    extra = {"parameter_edit": {"changed": [], "reasons": [], "mode": "model"}}
    return _start(services, "edit", messages, name, description, model_id, instruction, wait, requirements=requirements, extra=extra)


def run_code(services, code: str, name: str | None = None, model_id: int | None = None, description: str | None = None, save: bool = True,
             params: dict | None = None, requirements: str | None = None) -> dict:
    """Run a given program (no language model). With ``model_id`` the part is rebuilt in place (same library id); without ``code`` the
    stored program is run again (regenerate). ``params`` sets named parameters of the program first. ``requirements`` (text such as
    "60 x 40 x 30 mm, 2 mm walls") is checked against the built part; with ``model_id`` and no text, the part's stored ones are.
    ``save=False`` only builds and measures."""
    previous = None
    if model_id is not None:
        _, previous = _stem_and_record(services, model_id)
        if not (code or "").strip():
            code = previous["code"]
    code = (code or "").strip("\n") + "\n"
    if len(code.strip()) < 10:
        raise ValueError("Give the sand3d program (code) to run.")
    changed = []
    if params:
        code, changed = checks.set_parameters(code, params)
    reqs = checks.parse_requirements(requirements) if requirements else list((previous or {}).get("requirements") or [])
    run = run_engine(services.config.data_dir, code)
    try:
        check = check_run(run, reqs)[1] if reqs else None
        if not save:
            vertices, faces = _mesh_mm(run["stl"])
            return {"ok": True, "saved": False, "measures": cad.measure(vertices, faces), "engine": run["meta"], "requirements_check": check,
                    "parameters": checks.public_parameters(code), "parameters_changed": changed,
                    "note": "Nothing was saved (save=false). Call again with save=true to add it to the library."}
        label = _default_name(description or (previous or {}).get("description", ""), name or (previous or {}).get("name"))
        kind = "regenerate" if previous else "run"
        extra = {"parameter_edit": {"changed": changed, "reasons": ["params"], "mode": "parameters"}} if changed else None
        result = save_result(services, run, code, label, description or (previous or {}).get("description", ""), model_id=model_id, kind=kind, attempts=1,
                             requirements=reqs, check=check, extra=extra)
        result.pop("paths", None)
        return {"saved": True, "parameters_changed": changed, **result}
    finally:
        shutil.rmtree(run["work"], ignore_errors=True)


def examples_info(with_code: bool = False) -> list[dict]:
    rows = checks.load_examples()
    return [{k: r[k] for k in ("id", "title", "family", "keywords", "expected", "parameters")} | ({"code": r["code"]} if with_code else {}) for r in rows]


# ---------------------------------------------------------------------------------------------------------------------
# status and install
def llm_status(services) -> dict:
    cached = _INFO_CACHE.get("llm")
    if cached and time.time() - cached[0] < 30:
        return cached[1]
    try:
        from .studio.global_http import select_global

        selection = select_global()
        value = {"available": True, "model": selection.get("model")}
    except Exception as error:  # noqa: BLE001
        value = {"available": False, "error": f"{type(error).__name__}: {error}"[:300]}
    _INFO_CACHE["llm"] = (time.time(), value)
    return value


def status_info(services, install: dict | None = None) -> dict:
    engine = engine_info(services.config.data_dir)
    llm = llm_status(services)
    return {"engine": engine, "llm": llm, "ready": bool(engine.get("installed")), "text_ready": bool(engine.get("installed")) and llm["available"],
            "limits": {"attempts": MAX_ATTEMPTS, "measure_retries": MEASURE_RETRIES, "run_timeout_s": RUN_TIMEOUT_S, "program_chars": SRC_LIMIT},
            "examples": [{"id": e["id"], "title": e["title"], "family": e["family"]} for e in checks.load_examples()],
            "api_reference_chars": len(api_doc()), "install": install_state(), "jobs": list_jobs(services, limit=5)}


_INSTALL = {"status": "idle", "log": [], "started_at": None}


def install_state() -> dict:
    return {"status": _INSTALL["status"], "log": _INSTALL["log"][-15:], "started_at": _INSTALL["started_at"]}


def _find_base_python() -> list[str] | None:
    """A Python 3.11 or 3.13 (bpy has wheels for those, not for 3.12)."""
    candidates: list[list[str]] = []
    if sys.version_info[:2] in ((3, 11), (3, 13)):
        candidates.append([sys.executable])
    if os.name == "nt":
        candidates += [["py", "-3.13"], ["py", "-3.11"], [r"C:\Python313\python.exe"], [r"C:\Python311\python.exe"]]
    else:
        candidates += [["python3.13"], ["python3.11"]]
    for argv in candidates:
        try:
            done = proc.run([*argv, "-c", "import sys;print('%d.%d' % sys.version_info[:2])"], timeout=20)
        except Exception:  # noqa: BLE001
            continue
        if done.returncode == 0 and (done.stdout or "").strip() in ("3.11", "3.13"):
            return argv
    return None


def install_target(data_dir: Path) -> Path:
    return REPO / ".sand3d-venv" if (REPO / "pyproject.toml").is_file() else Path(data_dir) / "engines" / "sand3d-venv"


def start_install(services) -> dict:
    with _LOCK:
        if _INSTALL["status"] == "running":
            return install_state()
        _INSTALL.update(status="running", log=[], started_at=time.time())
    target = install_target(services.config.data_dir)

    def say(text: str) -> None:
        _INSTALL["log"].append(text)

    def work() -> None:
        try:
            base = _find_base_python()
            if base is None:
                raise RuntimeError("No Python 3.11 or 3.13 found (bpy has no wheel for other versions). Install Python 3.13 and try again.")
            say(f"creating {target} with {' '.join(base)}")
            target.parent.mkdir(parents=True, exist_ok=True)
            done = proc.run([*base, "-m", "venv", str(target)], timeout=300)
            if done.returncode != 0:
                raise RuntimeError((done.stderr or done.stdout or "venv failed")[-400:])
            python = _python_in(target)
            say("installing sand3d (it brings Blender's bpy, about 350 MB)")
            done = proc.run([str(python), "-m", "pip", "install", "--disable-pip-version-check", "sand3d"], timeout=1800)
            if done.returncode != 0:
                raise RuntimeError((done.stderr or done.stdout or "pip failed")[-600:])
            _INFO_CACHE.clear()
            found = engine_info(services.config.data_dir, refresh=True)
            if not found.get("installed"):
                raise RuntimeError(found.get("error") or "sand3d installed but cannot be imported")
            say(f"installed: sand3d {found.get('sand3d')}, bpy {found.get('bpy')}")
            _INSTALL["status"] = "done"
        except Exception as error:  # noqa: BLE001
            say(f"failed: {error}"[:600])
            _INSTALL["status"] = "failed"

    threading.Thread(target=work, name="sand3d-install", daemon=True).start()
    return install_state()
