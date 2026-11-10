"""Agent tools for the official Khronos glTF validation: validate a library model (or variant) or a file, and read the saved report.
Registered in agent_tools.TOOLS (one list drives /api/agent/* and the MCP bridge)."""

from __future__ import annotations

from pathlib import Path

from pydantic import BaseModel, Field

from .hoard_link.agentkit import Tool, ann
from .services import Services


class ValidateArgs(BaseModel):
    id: int | None = Field(None, ge=1, description="Library model or variant id. A GLB/glTF is validated as it is; an STL/OBJ/3MF through its web GLB export.")
    path: str | None = Field(None, max_length=2000, description="Or the absolute path of a .glb or .gltf file on this PC.")
    force: bool = Field(False, description="Validate again even when a report for the same file content is already saved.")


class ReportArgs(BaseModel):
    id: int | None = Field(None, ge=1, description="Library model or variant id.")
    path: str | None = Field(None, max_length=2000, description="Or the absolute path of a .glb or .gltf file.")


def _file(path: str) -> Path:
    file = Path((path or "").strip().strip('"')).expanduser()
    if not file.is_absolute():
        raise ValueError("Give the absolute path of a .glb or .gltf file.")
    if file.suffix.lower() not in (".glb", ".gltf"):
        raise ValueError("Only .glb and .gltf files can be validated.")
    if not file.is_file():
        raise LookupError(f"File not found: {file}")
    return file.resolve()


def run_validate(services: Services, args: ValidateArgs) -> dict:
    from . import gltf_validate

    if args.id:
        return gltf_validate.validate_model(services, args.id, args.force)
    if args.path:
        return {"source": "file", **gltf_validate.validate_and_store(services, _file(args.path), args.force)}
    raise ValueError("Give a model id or the path of a .glb/.gltf file.")


def run_report(services: Services, args: ReportArgs) -> dict:
    from . import gltf_validate

    if args.id:
        return gltf_validate.model_report(services, args.id)
    if args.path:
        file = _file(args.path)
        saved = gltf_validate.stored_report(services, file)
        if not saved:
            return {"source": "file", "path": str(file), "validated": False, "summary": gltf_validate.summarize(None), "status": gltf_validate.status()}
        return {"source": "file", "validated": True, "summary": gltf_validate.summarize(saved), **saved}
    raise ValueError("Give a model id or the path of a .glb/.gltf file.")


GLTF_TOOLS: list[Tool] = [
    Tool("gltf_validate", "Validate a model or .glb/.gltf with the official Khronos validator (write). Keywords: validar glTF, Khronos.\nRuns the Khronos glTF-Validator (npm gltf-validator, via Node) on a library model/variant by id (STL/OBJ/3MF through its web GLB export) or an absolute path; returns counts of errors, warnings, infos and hints, every message (code, severity, JSON pointer, text) and the asset info (version, generator, counts, extensions). The report is saved by content hash and reused until the file changes (force=true repeats). status=not_installed when Node or the package is missing. Warnings never block an export.\nSinónimos: validar glTF, validar GLB, validador oficial, glTF válido, errores del GLB, avisos del glTF.", ValidateArgs, ann(False, False, True), run_validate, timeout_s=180),
    Tool("gltf_report", "Read the saved glTF validation report of a model or file; runs nothing. Keywords: informe glTF.\nReturns the last report stored by gltf_validate or by an export (validated=false when there is none yet): summary state (valid | warnings | errors), counts, messages and asset info.\nSinónimos: informe de validación, último resultado glTF, insignia glTF, ¿es válido el GLB?, errores guardados.", ReportArgs, ann(True), run_report),
]
