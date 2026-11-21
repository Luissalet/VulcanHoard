# Modelling with code (sand3d)

`code_model` turns a description into a library part by having the family's language model write a short
[sand3d](https://github.com/Sandflow-ai/sand3d) program (Apache-2.0). sand3d is a Python library over Blender's `bpy` module
built for language models: a few verbs (`extrude`, `revolve`, `hole`, `fillet`, `shell`, `grid`, `at`, `join`, `cut`...), millimetres by default,
export to GLB, STL, OBJ, USDA and URDF. The reference that goes into the prompt is `vulcan/sand3d_api.md` (a copy of the project's `docs/api.md`, about 3,600 tokens).

## Tools and routes

| MCP tool | REST (under `/library/api` in the owner, `/api` in the plain app) | What it does |
|---|---|---|
| `code_model(description, name?, wait=true)` | `POST /code-model/from-text` (returns a job) | Picks the closest tested examples, parses the measures asked for, the language model writes the program, it runs, errors go back (3 attempts), the mesh is measured and a miss goes back once more, exports, validates, saves. |
| `code_model_run(code?, id?, name?, description?, save=true, params?, requirements?)` | `POST /code-model/run`, `POST /code-model/{id}/regenerate` | Runs a given program (no language model). With `id` the same library model is rebuilt; without `code` the stored program runs again. `params` sets named parameters first; `requirements` ("60 x 40 x 30 mm, 2 mm walls") is checked on the result. `save=false` only measures. |
| `code_model_edit(id, instruction, wait=true)` | `POST /code-model/{id}/edit` (returns a job) | An instruction that is only a measure changes the matching named parameter with no language model; anything else goes to the model with the program and its parameters (last five programs kept). |
| `code_model_status(job_id?, id?, cancel?, install?, api_reference?, examples?, example?)` | `GET /code-model/status`, `/jobs/{id}`, `/{id}`, `POST /code-model/install`, `/jobs/{id}/cancel`, `GET /code-model/reference`, `GET /code-model/examples`, `/examples/{id}` | Engine and language model readiness, job stage/result, the program of a part with its parameters and measure check, the example library, install. |
| — | `POST /code-model/requirements` `{text, code?}` | What a description asks for (measures, examples it would pick) and, with `code`, the parameter change an instruction maps to. |

Files of a part: `GET /code-model/{id}/preview.png`, `/model.glb`, `/code`. They live in `data/code_models/<slug>-<id>.{stl,glb,s3d.py,preview.png,code.json}`; the library indexes the STL (millimetres, tags `codigo` and `sand3d`).

## Example library

`vulcan/sand3d_examples/` holds eight tested programs, one per family of part, each with named, commented parameters at the top and its
expected measures in `index.json` (a test builds every one in the real engine and checks its size, wall thickness or round feature):

| id | family | builds | patterns it shows |
|---|---|---|---|
| `wall_bracket` | soporte | 70 x 80 x 25, 5 mm walls | L outline as a bent band (`stroke`) extruded by its width, diagonal brace, screw holes with rotated cylinders |
| `box_with_lid` | caja | box 60 x 40 x 30 + lid beside it | cavity cut from a rounded solid, lid with a 1.6 mm lip ring (`offset` profile difference), clearance |
| `hinge` | bisagra | 40 x 50 x 8, print in place | knuckles along X, pin, clearance rooms cut from the other leaf |
| `hook` | gancho | 45 x 70 x 22 | J outline with an `arc` cradle, prints on its side, fillet, screw holes |
| `organizer` | organizador | 150 x 80 x 45, 3 cells | pockets placed with `grid` frames, equal cells from the wall thickness |
| `spur_gear` | engranaje | 39 x 39 x 14 | involute `gear`, hub, D-shaped bore, set-screw hole, chamfer |
| `pipe_adapter` | adaptador | 37 x 37 x 51 | revolved half section: socket over tube A, cone under 45 degrees, spigot into tube B |
| `furniture_foot` | pie | 40 x 40 x 20 | tapered foot (`cone`), countersunk hole and pad recess on a named face |

`code_checks.pick_examples` ranks them by the words of the description (Spanish and English keywords, accents and plurals folded, the
family name counting more, earlier words weighing a little more so "bisagra para una caja" is a hinge); the two or three best go into
the system prompt after the sand3d reference (two general ones when nothing matches). The local language model is not used for this
choice: the keyword ranking is instant and deterministic. The page **Código** can load any example into the editor.

## Measures

`code_checks.parse_requirements` reads what the description asks for, in mm (cm converted):

- size: "80 x 40 mm", "60x40x30", "6 x 4 x 3 cm" (2 or 3 values, matched to the axes in any order);
- thickness: "3 mm de grosor", "paredes de 2 mm", "6 mm thick", "2 mm walls";
- round fit: "para tubo de 25 mm", "eje de 8 mm", "40 mm de diámetro", "Ø 30";
- hole: "agujeros de 5 mm", "M4" (clearance 4.5 mm);
- one extent: "80 mm de largo", "altura de 30 mm", "100 mm wide".

They go into the user message, and after each successful run the STL is measured (`code_checks.measure_part`):

- bounding box of the part and of each body (biggest first): a size matches the part **or its main body**, so a box printed beside its lid
  passes as 60 x 40 x 30; tolerance max(0.5 mm, 2 %);
- wall thickness: 600 points spread evenly over the surface (seeded), a ray inward from each along the face normal; a thickness passes
  when at least 12 % of the samples are within max(0.3 mm, 12 %) of it (the plates and walls of the part, not every edge);
  only on closed meshes;
- round features: sections across X, Y and Z at five heights; rings with circularity >= 0.88 give the diameters of round openings and
  outlines; a hole passes within max(0.6 mm, 3 %), a round fit 0.6 mm more (a socket or spigot needs clearance).

Wall and round-section measurements need the `rtree` spatial index in the app’s Python environment. The studio’s `GET /api/health`
and library’s `GET /library/api/status` report whether the running process can load it in `mesh_index.available`. Run the mesh tests with the
app’s virtual environment (`venv/Scripts/python.exe -m pytest tests/test_code_model.py` on Windows or
`venv/bin/python -m pytest tests/test_code_model.py` elsewhere). Those tests fail with installation guidance when the
index cannot load. Round sections also need the optional studio dependency `shapely`, listed in `requirements-studio.txt`.
The round-section tests report an explicit skip when Shapely is absent.

If something misses, the measured values go back to the model ("built 70 x 40 x 6 mm overall; size 80 x 40 mm: built 70 x 40 ...")
for **one more attempt** (so up to 3 calls to get a program that runs plus 1 for measures); the run that meets most measures is
saved, and the result carries `requirements_check` `{checked, ok, met, total, items[{kind, wanted, actual, ok, tolerance_mm, note}]}`
and a warning listing the measures not met. If the fix breaks the program, the earlier part is kept.

## Named parameters and editing

`code_checks.program_parameters` lists the numeric top-level assignments (`t = 3`, `w, d, h = 60, 40, 30`) and function defaults, with
the comment of their line; `set_parameters` replaces only those literals in the source. The page shows them as **Parámetros con
nombre** (number fields and **Aplicar parámetros**, which rebuilds the same id without a language model), and `code_model_run` takes
`params`.

**Cambiar hablando** (`code_model_edit`) first tries `parameter_edit`: when the instruction is only measures — "hazla de 8 mm de grosor",
"5 mm más gruesa", "2 mm thinner", "make it 100 mm wide", "agujeros de 5 mm", "brazo de 90 mm", "que mida 80x50x35" (onto a size tuple),
"t = 6" — each measure maps to the one parameter whose name or comment fits (thickness -> `t`/`wall`, width -> `w`/`width`, height -> `h`,
holes -> `screw_d`/`hole_d`, arm -> `arm_l`/`reach`...), relative changes add or subtract, and the part is rebuilt with no model call
(`edit_mode: "parameters"`, `parameter_edit.changed`). If the instruction asks for anything else, or a measure fits two parameters
equally, the model gets the program, the list of its parameters and the rule to change the parameter that holds a size instead of
rewriting the part (`edit_mode: "model"`); measures in the instruction are checked like in `code_model`.

## The engine

`bpy` has wheels for Python 3.11 and 3.13 only, so the engine is a separate virtual environment, found in this order: `VULCAN_SAND3D_PYTHON`, `.sand3d-venv` next to the app, `data/engines/sand3d-venv`. **Instalar el motor** (UI) or `code_model_status install=true` creates it with `py -3.13`/`py -3.11` and runs `pip install sand3d`. The app's venv is untouched.

Every program runs as `python -I vulcan/sand3d_runner.py job.json` in a temporary folder with a cleaned environment:

- Before it starts, an AST check refuses imports other than `sand3d, math, itertools, functools, typing, dataclasses, collections, enum, random`, names such as `open, exec, eval, getattr, os, bpy`, and dunder attributes.
- It then runs with reduced builtins and a guarded `import`; the final object is the variable `model` (else `result`, else the only top object in the scene).
- The parent kills the whole process tree after `VULCAN_CODE_TIMEOUT` seconds (default 300: on a busy machine importing `bpy` alone can take a minute).
- sand3d writes GLB/STL in metres; the STL is converted to millimetres, merged and measured (bounding box, volume, watertight, bodies) before saving. The GLB is kept as written and validated with the Khronos validator.
- Errors come back as `{code, error, line, code_line}`; codes: `engine_missing, rejected, program, timeout, model_unavailable, model_invalid, empty, invalid` (an unknown parameter name).

This is defence in depth for model-written code, not a hardened sandbox: the process runs with the user's rights.

## Limits

- The shape is still the language model's: the examples and the measure check make it follow the right patterns and hit the numbers
  asked for, but the check only covers what the description states (overall size, wall thickness, round features); it says nothing
  about whether a hook holds headphones well.
- A thickness passes on its share of matching samples, so a part with mostly 3 mm plates and one thin rib still passes "3 mm"; the thinnest
  sampled wall (p5) is in the note.
- Requirements are parsed with patterns, not understood: "for a 25 mm tube" checks that something round of about 25 mm exists, not
  which side of the part it is on.
- Parameter edits by instruction only cover sizes with an obvious parameter; everything else goes to the model.
- Materials, joints and assemblies survive in the GLB; the library part is the STL. URDF/USDA/OBJ export is not wired in.
- The program is limited to 30,000 characters; the prompt asks for printable parts (one body or parts side by side, walls of at least 1.2 mm).
