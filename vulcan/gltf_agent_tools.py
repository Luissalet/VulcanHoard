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
    Tool("gltf_validate", "Validate a model or a .glb/.gltf file with the official Khronos validator (write: saves the report). Keywords: validar glTF, validar GLB, Khronos.\nRuns the Khronos glTF-Validator (npm gltf-validator, through Node) on a library model/variant by id (GLB/glTF as it is; STL/OBJ/3MF through its web GLB export) or on an absolute path; returns the number of errors, warnings, infos and hints, every message (code, severity, JSON pointer, text) and the asset info (version, generator, meshes, materials, textures, animations, extensions used/required, triangles, vertices). The report is saved next to the file's content hash and reused until the file changes (force=true validates again). status=not_installed when Node or the package is missing. Warnings never block an export; errors only mark it.\nSinónimos: validar glTF, validar GLB, comprobar GLB, validador oficial, Khronos, glTF válido, errores del GLB, avisos del glTF, revisar el modelo web.", ValidateArgs, ann(False, False, True), run_validate, timeout_s=180),
    Tool("gltf_report", "Read the saved glTF validation report of a model or file without running anything. Keywords: informe glTF, resultado de la validación.\nReturns the last report stored by gltf_validate or by an export (validated=false when there is none yet): summary state (valid | warnings | errors), counts, messages and asset info.\nSinónimos: informe de validación, último resultado glTF, insignia glTF, ¿es válido el GLB?, errores guardados.", ReportArgs, ann(True), run_report),
]
