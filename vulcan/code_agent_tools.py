"""Agent tools for modelling with code (see code_model.py). Registered in agent_tools.TOOLS."""

from __future__ import annotations

from pydantic import BaseModel, Field

from .hoard_link.agentkit import Tool, ann
from .services import Services


class CodeModelArgs(BaseModel):
    description: str = Field(..., min_length=3, max_length=2000, description="The part to model, with sizes in mm, e.g. 'wall hook for a game controller, 3 mm thick'.")
    name: str | None = Field(None, max_length=80, description="Library name; default taken from the description.")
    wait: bool = Field(True, description="Wait for the result (up to 30 minutes); false returns a job id to follow with code_model_status.")


class CodeModelRunArgs(BaseModel):
    code: str | None = Field(None, max_length=30000, description="A sand3d program (import sand3d as s3d ... model = <solid>). Omit it with id to rebuild a saved part from its stored program.")
    id: int | None = Field(None, ge=1, description="A part made with code_model: rebuild this library model in place (same id).")
    name: str | None = Field(None, max_length=80, description="Library name for a new part.")
    description: str | None = Field(None, max_length=2000, description="What the part is (stored with it).")
    save: bool = Field(True, description="false only builds and measures; nothing is written to the library.")


class CodeModelEditArgs(BaseModel):
    id: int = Field(..., ge=1, description="A part made with code_model or code_model_run.")
    instruction: str = Field(..., min_length=3, max_length=2000, description="What to change, e.g. 'make the plate 5 mm thicker and add two M4 holes'.")
    wait: bool = Field(True, description="Wait for the result (up to 30 minutes); false returns a job id.")


class CodeModelStatusArgs(BaseModel):
    job_id: str | None = Field(None, pattern="^[0-9a-f]{12}$", description="A job id from code_model or code_model_edit: its stage, attempt, result and errors.")
    id: int | None = Field(None, ge=1, description="A saved part: its program, measures, history and links.")
    cancel: bool = Field(False, description="With job_id: stop the job.")
    install: bool = Field(False, description="Create the engine environment and pip install sand3d (about 350 MB; runs in the background).")
    api_reference: bool = Field(False, description="Return the sand3d reference (the text a model gets to write programs).")


def run_code_model(services: Services, args: CodeModelArgs) -> dict:
    from . import code_model as cm

    try:
        return cm.start_from_text(services, args.description, args.name, args.wait)
    except cm.CodeModelError as error:
        return {"ok": False, "error": str(error), "code": error.code, **error.details}


def run_code_model_run(services: Services, args: CodeModelRunArgs) -> dict:
    from . import code_model as cm

    try:
        return cm.run_code(services, args.code or "", args.name, args.id, args.description, args.save)
    except cm.CodeModelError as error:
        return {"ok": False, "error": str(error), "code": error.code, **error.details}


def run_code_model_edit(services: Services, args: CodeModelEditArgs) -> dict:
    from . import code_model as cm

    try:
        return cm.start_edit(services, args.id, args.instruction, args.wait)
    except cm.CodeModelError as error:
        return {"ok": False, "error": str(error), "code": error.code, **error.details}


def run_code_model_status(services: Services, args: CodeModelStatusArgs) -> dict:
    from . import code_model as cm

    if args.job_id:
        return cm.cancel_job(services, args.job_id) if args.cancel else cm.get_job(services, args.job_id)
    if args.install:
        return {"ok": True, "install": cm.start_install(services)}
    if args.id:
        return cm.info(services, args.id)
    out = {"ok": True, **cm.status_info(services)}
    if args.api_reference:
        out["api_reference"] = cm.api_doc()
    return out


CODE_TOOLS: list[Tool] = [
    Tool("code_model", "Model a 3D part from a description by writing a sand3d program (write). Keywords: modelar con código.\n"
         "The family's language model writes the program with the sand3d reference, it runs isolated with a time limit, errors go back to the "
         "model (3 attempts), then STL (mm) and GLB are exported, the GLB is validated, a preview is rendered and the part is saved in the library "
         "with its editable program. Returns id, measures, links, validation, program.\n"
         "Sinónimos: modelar con código, diseñar con Python, soporte, pieza, texto a 3D, sand3d, programa 3D.",
         CodeModelArgs, ann(False, False, False), run_code_model, timeout_s=1800, capped=False),
    Tool("code_model_run", "Run a sand3d program (no language model) and save or measure the part (write). Keywords: ejecutar código 3D.\n"
         "Runs the program in the isolated engine, exports STL (mm) and GLB, validates the GLB and saves a new library model, or rebuilds the "
         "part with id in place (without code it reruns the stored program: regenerate). save=false only measures. Errors name the program line.\n"
         "Sinónimos: regenerar pieza, ejecutar programa sand3d, editar código, medir pieza, rehacer modelo.",
         CodeModelRunArgs, ann(False, False, False), run_code_model_run, timeout_s=300),
    Tool("code_model_edit", "Change a part made with code by an instruction in plain words (write). Keywords: editar modelo con código.\n"
         "The language model gets the stored program and the instruction and returns the full new program; it is run and checked like in "
         "code_model, and the same library model is rebuilt in place (previous programs are kept). Returns measures, links and the new program.\n"
         "Sinónimos: modificar pieza, hazla más gruesa, añade agujeros, cambiar medidas, retocar modelo.",
         CodeModelEditArgs, ann(False, False, True), run_code_model_edit, timeout_s=1800, capped=False),
    Tool("code_model_status", "Code engine readiness, jobs and a part's program (install=true sets it up). Keywords: estado código 3D.\n"
         "Without arguments: sand3d/bpy versions, language model available, limits and recent jobs. job_id: stage and result (cancel=true stops "
         "it). id: program, measures, history. api_reference=true returns the sand3d reference. install=true creates the engine environment.\n"
         "Sinónimos: motor sand3d instalado, cómo va el modelo, ver el código de la pieza, instalar sand3d.",
         CodeModelStatusArgs, ann(False, False, True), run_code_model_status, capped=False),
]
