# glTF validation (Khronos)

Every GLB/glTF that Vulcan indexes, exports or generates can be checked with the official Khronos glTF-Validator
(npm `gltf-validator`, run with Node). The check never blocks anything: warnings are only counted and errors only mark the file.

## What it does and does not do

- Runs `scripts/gltf_validate.mjs <file>` with Node and returns one JSON report: errors, warnings, infos and hints, every
  message (code, severity, JSON pointer, text) and the asset info (glTF version, generator, meshes, materials, textures,
  animations, extensions used and required, triangles, vertices). A `.gltf` with external buffers or images is read from its own folder.
- Reports are saved in `<data dir>/gltf_validation/<sha256 of the file>.json` and reused until the file content changes
  (`force` validates again). A result of `not_installed` (no Node, or no `npm install` in the app folder) is returned but never saved.
- It validates the file as a glTF document. It does not judge whether the geometry is good for printing (see `model_scene_info`).
- Library models in STL, OBJ or 3MF are validated through their web GLB export (the glTF Vulcan would hand out), built first when missing.

## Where it runs automatically

| Writer | Where the report goes |
| --- | --- |
| Web GLB export (`model_export_web_glb`, LOD/library) | `gltf_validation` in the export's JSON beside the GLB and in the tool result |
| Asset preparation to GLB (`asset_prepare`) | `gltf_validation` in the preparation receipt |
| Animation preparation, retarget, FBX import (`anim_*`) | `gltf_validation` in the receipt |
| Gulliver maquette preview (`preview.glb`) | `gltf_validation` in `report.json` and the full report as `gltf-validation.json` beside it (when Gulliver runs inside Vulcan) |

`gltf_validation` is `{state, errors, warnings, infos, hints, label, validator_version}` with `state` one of `valid`,
`warnings`, `errors`, `not_installed`, `error`.

## REST

- `POST /api/models/{id}/gltf-validation?force=` validate a library model or variant; `GET` the saved report (`validated: false` if none).
- `POST /api/gltf/validate` `{path, force}` validate a `.glb`/`.gltf` by absolute path; `GET /api/gltf/report?path=` the saved report.
- `POST /api/gltf/validate-upload?name=file.glb` with the file as the raw body (a self-contained file, up to 256 MB).
- `GET /api/gltf/status` whether Node and the package are available.

## MCP tools

- `gltf_validate` (`id` or `path`, `force`): validates and saves the report.
- `gltf_report` (`id` or `path`): reads the saved report without running anything.

## UI

Model page: a chip in the header (**glTF válido** / **N avisos** / **N errores** / **glTF sin validar**) and the card
**Validación glTF (Khronos)** with the **Validar** button, the readable list of messages grouped by severity and the asset facts.
**Exportar GLB web** also shows the result of its own check.

## Setup

`npm install` in the app folder installs `gltf-validator` (pinned in `package.json`). Without Node or the package the app works
as before and reports `not_installed`.
