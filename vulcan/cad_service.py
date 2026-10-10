"""Text -> parametric CAD -> library item. Ties `cad.py` to the library: writes the STL and its spec next to each other in
`<data dir>/cad/`, indexes the STL like any imported model, and keeps the spec so the part can be regenerated with new values.

Per part, in `<data dir>/cad/`:  `<slug>-<id>.stl`  the mesh the library indexes;  `<slug>-<id>.cad.json`  the spec with
its current parameter values, the description it came from and a short history;  `<slug>-<id>.check.png`  a render for
looking at the result next to the measured numbers.
"""

from __future__ import annotations

import re
import unicodedata
import uuid
from datetime import datetime, timezone
from pathlib import Path

from . import cad, csg
from .family_tools import import_file
from .hoard_link import atomic
from .services import Services
from .thumbnail import render

HISTORY_LIMIT = 20
CAPABILITIES = {
    "primitives": ["box", "rounded_box", "cylinder (cones with radius_top)", "sphere"],
    "operations": ["union", "difference (+ hole)", "intersection", "translate", "rotate", "scale", "linear_pattern", "polar_pattern"],
    "formats": {"stl": True, "step": False},
    "unsupported": sorted(cad.UNSUPPORTED),
}


def _now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def _slug(name: str) -> str:
    text = unicodedata.normalize("NFKD", name or "part").encode("ascii", "ignore").decode()
    text = re.sub(r"[^A-Za-z0-9]+", "-", text).strip("-").lower()
    return (text or "part")[:40]


class CadService:
    def __init__(self, services: Services):
        self.services = services

    @property
    def folder(self) -> Path:
        return self.services.config.data_dir / "cad"

    # ---------- public ----------
    def schema(self) -> dict:
        return {"schema": cad.spec_schema(), "reference": cad.REFERENCE, "example": cad.EXAMPLE_SPEC, "engine": csg.backend(),
                "capabilities": CAPABILITIES,
                "notes": ["Pass a spec to cad_from_text (spec=...) to skip the language model and build exactly what you wrote.",
                          "STEP export is not available (no CAD kernel is installed); the part is saved as STL.",
                          "Add an 'expect' block with the requested bounding box so the result is checked against it."]}

    def from_text(self, description: str = "", spec=None, name: str | None = None, params: dict | None = None,
                  expect: dict | None = None, save: bool = True) -> dict:
        description = (description or "").strip()
        attempts, source = 0, "spec"
        if spec is None:
            if not description:
                raise ValueError("Give a description of the part, or a spec (see cad_spec_schema).")
            spec, compiled, attempts = self._spec_from_model(description, params)
            source = "llm"
        else:
            spec = cad.parse_spec(spec)
            compiled = None
        spec = dict(spec)
        if name:
            spec["name"] = name.strip()[:80]
        spec.setdefault("name", (description[:60] or "Parametric part"))
        if expect:
            spec["expect"] = expect
        if compiled is None or params or expect:
            compiled = cad.compile_spec(spec, params)
        spec = self._with_values(spec, params)
        result = self._report(spec, compiled)
        result.update({"source": source, "attempts": attempts, "description": description})
        if not save:
            return {"ok": True, "saved": False, **result,
                    "note": "Nothing was saved (save=false). Call again with save=true to add it to the library."}
        stem = f"{_slug(spec['name'])}-{uuid.uuid4().hex[:8]}"
        files = self._write(stem, spec, compiled, result, description, source)
        imported = import_file(self.services, str(files["stl"]))
        self._label(imported["id"], description, source)
        return {"ok": True, "saved": True, "id": imported["id"], "ref": imported["ref"], **self._paths(files, imported["id"]), **result,
                "library": {k: imported[k] for k in ("status", "bbox", "triangles", "volume_cm3", "watertight", "bodies", "has_thumb")}}

    def regenerate(self, model_id: int, params: dict | None = None, spec=None, expect: dict | None = None) -> dict:
        files, record = self._load(model_id)
        new_spec = cad.parse_spec(spec) if spec is not None else dict(record["spec"])
        if expect:
            new_spec["expect"] = expect
        if not params and spec is None and not expect:
            raise ValueError("Give the parameters to change (params={name: value}) or a new spec; cad_info-style details come from the cad_spec of model_info.")
        compiled = cad.compile_spec(new_spec, params)
        new_spec = self._with_values(new_spec, params)
        result = self._report(new_spec, compiled)
        before = record.get("last_measures") or {}
        result["changed"] = {"bbox_mm": {"before": before.get("bbox_mm"), "after": result["measures"]["bbox_mm"]},
                             "volume_mm3": {"before": before.get("volume_mm3"), "after": result["measures"]["volume_mm3"]}}
        self._write(files["stl"].stem, new_spec, compiled, result, record.get("description", ""), record.get("source", "spec"), previous=record)
        imported = import_file(self.services, str(files["stl"]))
        return {"ok": True, "id": imported["id"], "ref": imported["ref"], "regenerated": True, **self._paths(files, imported["id"]), **result,
                "library": {k: imported[k] for k in ("status", "bbox", "triangles", "volume_cm3", "watertight", "bodies", "has_thumb")}}

    def info(self, model_id: int) -> dict:
        files, record = self._load(model_id)
        spec = record["spec"]
        return {"id": model_id, "spec": spec, "params": cad.params_info(spec), "description": record.get("description", ""), "source": record.get("source"),
                "created_at": record.get("created_at"), "updated_at": record.get("updated_at"), "history": record.get("history", []),
                "measures": record.get("last_measures"), "verification": record.get("last_verification"), **self._paths(files, model_id)}

    def check_png(self, model_id: int) -> Path:
        files, _ = self._load(model_id)
        if not files["png"].is_file():
            raise LookupError("The check render does not exist (regenerate the part).")
        return files["png"]

    def is_cad(self, model_id: int) -> bool:
        try:
            self._load(model_id)
            return True
        except (LookupError, ValueError):
            return False

    # ---------- internals ----------
    def _spec_from_model(self, description: str, params: dict | None):
        """Ask the shared language model for a spec: strict JSON, validated and compiled; one repair attempt with the errors."""
        link = self.services.link
        messages = [{"role": "system", "content": cad.SYSTEM_PROMPT}, {"role": "user", "content": description}]
        errors: list[str] = []
        for attempt in (1, 2):
            try:
                answer = link.sync.chat(messages, max_tokens=4000, response_format={"type": "json_object"}, effort="medium")
                text = answer.text
            except Exception as error:  # noqa: BLE001 - Unavailable, BackendError, timeouts: the assistant can still write the spec itself
                raise cad.CadError([f"No language model answered ({type(error).__name__}: {error}). Write the spec yourself from cad_spec_schema and call cad_from_text with spec=..."],
                                   "model_unavailable") from error
            try:
                spec = cad.extract_json(text)
                return spec, cad.compile_spec(spec, params), attempt
            except cad.CadError as error:
                errors = error.errors
                messages = messages + [{"role": "assistant", "content": (text or "")[:6000]},
                                       {"role": "user", "content": "That spec was rejected:\n" + "\n".join(f"- {e}" for e in errors[:12])
                                        + "\nReturn the corrected, complete JSON object only."}]
        raise cad.CadError(["The language model did not produce a valid spec after one repair attempt: " + "; ".join(errors[:6])
                            + ". Write the spec yourself (cad_spec_schema) and call cad_from_text with spec=..."], "model_invalid")

    @staticmethod
    def _with_values(spec: dict, params: dict | None) -> dict:
        """The spec with the overriding parameter values written into it, so the stored spec is always the current truth."""
        if not params:
            return spec
        merged = dict(spec)
        declared = dict(merged.get("params") or {})
        for key, value in params.items():
            entry = declared.get(key)
            declared[key] = {**entry, "value": value} if isinstance(entry, dict) else value
        merged["params"] = declared
        return merged

    def _report(self, spec: dict, compiled: cad.Compiled) -> dict:
        measures = cad.measure(compiled.vertices, compiled.faces)
        verification = cad.verify(spec.get("expect"), measures)
        warnings = list(compiled.warnings)
        if not measures["watertight"]:
            warnings.append("the mesh is not watertight; slicers usually repair it, but check it before printing")
        if verification.get("ok") is False:
            warnings.append("the part does not match the requested measures: see verification.items")
        return {"name": spec["name"], "spec": spec, "params": cad.params_info(spec, compiled.params), "engine": compiled.backend, "measures": measures,
                "verification": verification, "warnings": warnings,
                "step": {"supported": False, "reason": "no CAD kernel is installed; the part is exported as STL only"}}

    def _write(self, stem: str, spec: dict, compiled: cad.Compiled, result: dict, description: str, source: str, previous: dict | None = None) -> dict:
        self.folder.mkdir(parents=True, exist_ok=True)
        files = self._files(stem)
        atomic.write_bytes_atomic(files["stl"], cad.stl_bytes(compiled.vertices, compiled.faces))
        image = render(compiled.vertices, compiled.faces, 512, background=(244, 243, 238), adaptive=False)
        tmp = files["png"].with_name(files["png"].stem + ".tmp.png")
        image.convert("RGB").save(tmp, "PNG")
        atomic.replace_with_retry(tmp, files["png"])
        history = list((previous or {}).get("history", []))
        history.append({"at": _now(), "params": compiled.params, "bbox_mm": result["measures"]["bbox_mm"], "volume_mm3": result["measures"]["volume_mm3"]})
        record = {"version": 1, "name": spec["name"], "spec": spec, "description": description, "source": source,
                  "created_at": (previous or {}).get("created_at") or _now(), "updated_at": _now(), "engine": compiled.backend,
                  "last_measures": result["measures"], "last_verification": result["verification"], "history": history[-HISTORY_LIMIT:]}
        atomic.write_json_atomic(files["spec"], record)
        return files

    def _files(self, stem: str) -> dict:
        return {"stl": self.folder / f"{stem}.stl", "spec": self.folder / f"{stem}.cad.json", "png": self.folder / f"{stem}.check.png"}

    def _paths(self, files: dict, model_id: int) -> dict:
        return {"stl_path": str(files["stl"]), "spec_path": str(files["spec"]), "check_png_path": str(files["png"]),
                "check_png_url": f"/api/cad/{model_id}/check.png"}

    def _load(self, model_id: int) -> tuple[dict, dict]:
        model = self.services.models.get(model_id)
        if model is None:
            raise LookupError(f"Model {model_id} does not exist.")
        path = Path(model["path"])
        try:
            inside = path.resolve().parent == self.folder.resolve()
        except OSError:
            inside = False
        files = self._files(path.stem)
        if not inside or not files["spec"].is_file():
            raise LookupError(f"Model {model_id} was not generated by cad_from_text, so it has no spec to regenerate.")
        record = atomic.read_json(files["spec"], default=None)
        if not isinstance(record, dict) or not isinstance(record.get("spec"), dict):
            raise LookupError(f"The spec file of model {model_id} is unreadable.")
        return files, record

    def _label(self, model_id: int, description: str, source: str) -> None:
        self.services.models.tag(model_id, ["cad", "parametric"], [])
        note = "Generated parametric part" + (f" from: {description[:300]}" if description else " from a spec") + f" ({source}). Edit its dimensions with cad_regenerate."
        self.services.models.patch(model_id, {"notes": note})


def get(services: Services) -> CadService:
    cached = getattr(services, "_cad", None)
    if cached is None:
        cached = services._cad = CadService(services)
    return cached
