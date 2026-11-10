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
    params: dict[str, float] | None = Field(None, description="Named parameters of the program to set first, e.g. {'t': 4, 'w': 90} (see code_model_status id).")
    requirements: str | None = Field(None, max_length=500, description="Measures to check on the built part, e.g. '60 x 40 x 30 mm, 2 mm walls, hole 5 mm'.")


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
    examples: bool = Field(False, description="List the tested example programs (family, parameters, expected size); with example, return one with its code.")
    example: str | None = Field(None, max_length=40, description="An example id (wall_bracket, box_with_lid, hinge, hook, organizer, spur_gear, pipe_adapter, furniture_foot): its program.")


def run_code_model(services: Services, args: CodeModelArgs) -> dict:
    from . import code_model as cm

    try:
        return cm.start_from_text(services, args.description, args.name, args.wait)
    except cm.CodeModelError as error:
        return {"ok": False, "error": str(error), "code": error.code, **error.details}


def run_code_model_run(services: Services, args: CodeModelRunArgs) -> dict:
    from . import code_model as cm

    try:
        return cm.run_code(services, args.code or "", args.name, args.id, args.description, args.save, args.params, args.requirements)
    except cm.CodeModelError as error:
        return {"ok": False, "error": str(error), "code": error.code, **error.details}
    except ValueError as error:  # an unknown parameter name, or no program
        return {"ok": False, "error": str(error), "code": "invalid"}


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
    if args.example:
        from .code_checks import example

        try:
            row = example(args.example)
        except LookupError as error:
            return {"ok": False, "error": str(error), "code": "not_found"}
        return {"ok": True, **{k: row[k] for k in ("id", "title", "family", "expected", "parameters", "code")}}
    if args.examples:
        return {"ok": True, "examples": cm.examples_info()}
    if args.id:
        return cm.info(services, args.id)
    out = {"ok": True, **cm.status_info(services)}
    if args.api_reference:
        out["api_reference"] = cm.api_doc()
    return out


CODE_TOOLS: list[Tool] = [
    Tool("code_model", "Model a 3D part from a description by writing a sand3d program (write). Keywords: modelar con código.\n"
         "The family's language model writes the program from the sand3d reference and the 2-3 closest tested examples; it runs isolated, "
         "errors go back (3 attempts); the mesh is measured against the sizes, thickness and holes in the description and a miss goes back once "
         "more. STL+GLB, validation, preview, saved with its program. Returns id, measures, requirements_check, parameters.\n"
         "Sinónimos: modelar con código, diseñar con Python, soporte, pieza, texto a 3D, sand3d, programa 3D.",
         CodeModelArgs, ann(False, False, False), run_code_model, timeout_s=1800, capped=False),
    Tool("code_model_run", "Run a sand3d program (no language model) and save or measure the part (write). Keywords: ejecutar código 3D.\n"
         "Runs the program in the isolated engine, exports STL (mm) and GLB, validates the GLB and saves a new library model, or rebuilds the "
         "part with id in place (no code: reruns the stored one). params sets named parameters; requirements checks measures. save=false only measures.\n"
         "Sinónimos: regenerar pieza, ejecutar programa sand3d, editar código, medir pieza, rehacer modelo.",
         CodeModelRunArgs, ann(False, False, False), run_code_model_run, timeout_s=300),
    Tool("code_model_edit", "Change a part made with code by an instruction in plain words (write). Keywords: editar modelo con código.\n"
         "An instruction that is only a measure (5 mm thicker, 90 mm wide) changes that named parameter with no model; otherwise the model gets "
         "the program, its parameters and the instruction. Rebuilt in place, measured, old programs kept. Returns edit_mode, changes, measures.\n"
         "Sinónimos: modificar pieza, hazla más gruesa, añade agujeros, cambiar medidas, retocar modelo.",
         CodeModelEditArgs, ann(False, False, True), run_code_model_edit, timeout_s=1800, capped=False),
    Tool("code_model_status", "Code engine readiness, jobs and a part's program (install=true sets it up). Keywords: estado código 3D.\n"
         "Without arguments: sand3d/bpy versions, language model available, limits and recent jobs. job_id: stage and result (cancel=true stops "
         "it). id: program, parameters, measures, check. examples / example=id: tested programs. api_reference: sand3d text. install: set up engine.\n"
         "Sinónimos: motor sand3d instalado, cómo va el modelo, ver el código de la pieza, instalar sand3d.",
         CodeModelStatusArgs, ann(False, False, True), run_code_model_status, capped=False),
]
