# Modelling with code (sand3d)

`code_model` turns a description into a library part by having the family's language model write a short
[sand3d](https://github.com/Sandflow-ai/sand3d) program (Apache-2.0). sand3d is a Python library over Blender's `bpy` module
built for language models: a few verbs (`extrude`, `revolve`, `hole`, `fillet`, `shell`, `grid`, `at`, `join`, `cut`...), millimetres by default,
export to GLB, STL, OBJ, USDA and URDF. The reference that goes into the prompt is `vulcan/sand3d_api.md` (a copy of the project's `docs/api.md`, about 3,600 tokens).

## Tools and routes

| MCP tool | REST (under `/library/api` in the owner, `/api` in the plain app) | What it does |
|---|---|---|
| `code_model(description, name?, wait=true)` | `POST /code-model/from-text` (returns a job) | Language model writes the program, it runs, errors go back (3 attempts), exports, validates, saves. |
| `code_model_run(code?, id?, name?, description?, save=true)` | `POST /code-model/run`, `POST /code-model/{id}/regenerate` | Runs a given program (no language model). With `id` the same library model is rebuilt; without `code` the stored program runs again. `save=false` only measures. |
| `code_model_edit(id, instruction, wait=true)` | `POST /code-model/{id}/edit` (returns a job) | The stored program plus the instruction go to the language model; the new program replaces the old one (last five kept). |
| `code_model_status(job_id?, id?, cancel?, install?, api_reference?)` | `GET /code-model/status`, `/jobs/{id}`, `/{id}`, `POST /code-model/install`, `/jobs/{id}/cancel`, `GET /code-model/reference` | Engine and language model readiness, job stage/result, the program of a part, install. |

Files of a part: `GET /code-model/{id}/preview.png`, `/model.glb`, `/code`. They live in `data/code_models/<slug>-<id>.{stl,glb,s3d.py,preview.png,code.json}`; the library indexes the STL (millimetres, tags `codigo` and `sand3d`).

## The engine

`bpy` has wheels for Python 3.11 and 3.13 only, so the engine is a separate virtual environment, found in this order: `VULCAN_SAND3D_PYTHON`, `.sand3d-venv` next to the app, `data/engines/sand3d-venv`. **Instalar el motor** (UI) or `code_model_status install=true` creates it with `py -3.13`/`py -3.11` and runs `pip install sand3d`. The app's venv is untouched.

Every program runs as `python -I vulcan/sand3d_runner.py job.json` in a temporary folder with a cleaned environment:

- Before it starts, an AST check refuses imports other than `sand3d, math, itertools, functools, typing, dataclasses, collections, enum, random`, names such as `open, exec, eval, getattr, os, bpy`, and dunder attributes.
- It then runs with reduced builtins and a guarded `import`; the final object is the variable `model` (else `result`, else the only top object in the scene).
- The parent kills the whole process tree after `VULCAN_CODE_TIMEOUT` seconds (default 120).
- sand3d writes GLB/STL in metres; the STL is converted to millimetres, merged and measured (bounding box, volume, watertight, bodies) before saving. The GLB is kept as written and validated with the Khronos validator.
- Errors come back as `{code, error, line, code_line}`; codes: `engine_missing, rejected, program, timeout, model_unavailable, model_invalid, empty`.

This is defence in depth for model-written code, not a hardened sandbox: the process runs with the user's rights.

## Limits

- The shape is only as good as the language model's program; the tool returns the measures and warnings (open mesh, several bodies, glTF errors) so they are checked, not assumed.
- Materials, joints and assemblies survive in the GLB; the library part is the STL. URDF/USDA/OBJ export is not wired in.
- The program is limited to 30,000 characters; the prompt asks for printable parts (one body, walls of at least 1.2 mm).
