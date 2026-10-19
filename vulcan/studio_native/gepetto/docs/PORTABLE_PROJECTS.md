# Opening prepared portable projects

In **Biblioteca → Abrir .gepettos**, select an editable project file. **Abrir carpeta
portable** remains available for folders containing `project.json`. Native file
and folder pickers work in the desktop app; browser mode accepts an absolute path.
Use **Guardar .gepettos** to save a new portable file of the current project.
Existing files are never overwritten; choose a new filename for another revision.

The importer copies native images, existing DAE/GLB, previews, masks and the
editable document into the app's library. The original folder stays untouched.
Regions, heights, semantic metadata, custom color fields and document provenance
are retained. No analysis, model loading, Blender evaluation or queue action is
triggered by importing. Existing background work is not modified.

If that project ID already exists, import refuses to overwrite it. Open the
existing entry in **Proyectos guardados** instead. Invalid IDs, missing files,
external paths, links, mismatched masks or out-of-range heights reject the import
before publishing a library entry. Inputs are bounded to 2 GB / 10,000 files.

The source undo database is not imported. The app starts a fresh local undo
timeline at the imported state when history is first requested or changes are
saved; the document's provenance/history entries remain available. This avoids
trusting a database supplied with an external folder.

This route supports CPU-prepared projects, not Blender evaluation bundles.
Import does not verify external DAE/GLB resource references or scene material
assignments; keep model resources embedded or import their textures explicitly.

## .gepettos version 1

The file is a ZIP container with `manifest.json` (`format: gepettos-project`,
`version: 1`, project ID, revision, byte lengths and SHA-256 for every payload),
`project.json`, `history.json`, native `sources/`, previews and editable `.npz`
masks/centers. Decision history is portable; an external SQLite undo database is
never packed or loaded. The exported copy is unapproved and must be reviewed.
Import checks the project identity, revision, history and all payload digests.
Unknown versions, duplicate names, unsafe paths, symlinks and oversized archives
are rejected before publishing anything to the library. Limits are 2 GB total
uncompressed, 512 MB per entry, and 10,000 entries. Export publishes atomically
from a temporary sibling file and refuses an existing destination.

Manual-palette regions display their actual `displacementColor` in color preview.
**Color de relieve** edits that production RGB and saves it in the project file.
The numerical height is only an ordinal reference for these regions and its
slider is disabled; the edit API also rejects height changes affecting a manual
region. Other regions retain their existing grayscale height workflow. No
automatic RGB-to-physical-height equivalence is claimed.

MCP: `export_project_file(project_id, destination)` where destination is a new
absolute `.gepettos` filename. UI API: `POST /api/projects/{id}/export-portable`
with `{"path":"E:/.../Figure.gepettos"}`. Import accepts the same file path through
the existing `/api/projects/import-portable` route. Saving and opening never
start local models, Blender, or the processing queue.

API: `POST /api/projects/import-portable` with `{"path":"E:/.../editable"}`.
Success returns the current project. Existing IDs return 409; invalid inputs
return 400. The handler uses a staging folder and publishes only after validation.

Tests use a minimal FastAPI instance containing only this route, so they never
start the normal application or its saved queue:

```text
.tooling/mcp-venv/Scripts/python.exe -m pytest -q tests/test_portable_project.py
```
