# External reasoning through MCP

`backend.external_mcp` is a separate, CPU-only stdio server using the official
MCP Python SDK. The calling assistant supplies every semantic decision. It does
not import `backend.app`, start the saved batch queue, load a model, call Ollama,
launch Blender or evaluate displacement. Image previews and deterministic
segmentation use Pillow/NumPy/scikit-learn on CPU. Native input files are copied
byte-for-byte and never overwritten.

Install the repository requirements in an isolated virtual environment (without
system site packages), then configure an MCP client to launch its interpreter:

```text
python -m backend.external_mcp --data-dir C:/absolute/isolated-preparation-library
```

Set the working directory to this repository. The data directory argument is
mandatory; the repository's default `data` directory is explicitly forbidden.
Use one server instance per isolated library, without opening that library in
the normal application during preparation. No existing saved data is required.
The local tested environment is `.tooling/mcp-venv/Scripts/python.exe`.

`scripts/mcp_external_call.py` is an actual official-SDK MCP client, useful when
the assistant cannot register a new server during the current conversation:

```text
python scripts/mcp_external_call.py --data-dir C:/absolute/isolated-library list
python scripts/mcp_external_call.py --data-dir C:/absolute/isolated-library create_project --arguments C:/absolute/create.json --output C:/absolute/result.json
```

The JSON arguments file for that example is `{"name":"Figure name"}`. Output
files must be new. Image evidence returns an MCP image content block; a JSON
client output contains its base64 PNG. Decode that image to a new local preview
file for visual inspection, or use an MCP client that displays image blocks.

## Tools and plan contract

- `create_project(name)` creates the same editable document shape as the app.
- `import_source(project_id,path)` copies images and existing DAE/GLB files.
  `.blend` is deliberately rejected. Import native textures explicitly.
- `segment_texture(project_id,asset_id,clusters,resolution)` creates candidate
  masks. It refuses to replace masks already edited in that project.
- `read_project(project_id)` returns current revisions, all region IDs and history.
- `image_evidence(project_id,asset_id,mode)` returns `original`, numbered `ids`,
  `color`, or grayscale `height` previews. Numbered labels are region ID + 1.
- `apply_semantic_plan(project_id,plan)` validates a complete externally authored
  region list, applies deterministic group operations, and saves provenance.
- `export_editable_project(project_id,destination)` copies the entire editable
  project to a new folder, without overwriting an existing destination.
- `inspect_editable_directory(directory)` reads it back and checks stored source
  files, previews, masks, region membership and reconstruction centers.

Example `plan` (replace IDs and revision with current values):

```json
{
  "revision": 2,
  "author": "External assistant",
  "evidence": ["Existing completed figure reference", "original/ids image review"],
  "explanation": "Heights follow the supplied completed-figure reference.",
  "regions": [{
    "key": "0123456789:0", "name": "Surface", "height": 128,
    "reason": "Visible surface; geometry has not been evaluated.",
    "confidence": 0.7, "geometry": "unknown"
  }],
  "operations": [{"action":"merge","targets":["0123456789:0"],"name":"Surface"}]
}
```

Every current region must appear exactly once; heights are integer 0–255.
The revision must match. Metadata/height changes are validated before document
mutation. Optional operations reuse `commands.apply_operations`: `merge` creates
semantic and height groups, `equal` links heights, `split` detaches groups,
`set`/`raise`/`lower` edit linked heights. Operations require explicit region keys,
not `group:` selectors. Group linking may change the listed initial heights;
inspect the returned final project. `split` does not create new pixel masks.
The existing `color` preview uses exact `displacementColor` where present and
the app's deterministic height palette for other regions.
Optional `displacementColor: [R,G,B]` supplies an exact manual production color
per region (three strict integers, 0–255). `manual-palette` previews these colors;
it rejects regions without them. Heights remain internal ordinal values and do
not calibrate physical displacement for the manual RGB workflow.
Never infer geometry or height from darkness alone. Compare completed reference
figures first; preserve uncertain regions and record the missing evidence.

## Portable editable folders and current UI limitation

The exported folder contains `project.json`, `sources/`, native source images,
previews, `.npz` masks/centers and the `revisions/` history. Its source references
are relative to that folder. This is an editable project, not a generated STL
or an evaluated Blender scene. No completion/approval is fabricated.

The UI's **Abrir .gepettos / Abrir carpeta portable** imports an editable copy into its library;
see [PORTABLE_PROJECTS.md](PORTABLE_PROJECTS.md). The bridge's inspect tool reads
exports directly without registering them or starting the normal app queue.
`export_project_file(project_id, destination)` saves a versioned, integrity-checked
`.gepettos` file to a new absolute path. It includes native inputs, editable masks,
metadata and decision history, with approval cleared for later review; no external
SQLite undo database. See PORTABLE_PROJECTS.md for the format and limits.

The normal `/export` ZIP and its `sculptors-hoard-project.json` manifest alone
are insufficient: they omit the editable masks and native project document.
Existing GLB should embed its resources. DAE/GLB references to external files are
not rewritten or resolved by this adapter; import needed textures explicitly
and verify model appearance when later opened. No Blender scene/UV/material
verification is implied by the file-integrity check. The bridge cannot identify
which textures are applied inside a `.blend` without a prior export.

## Verification

### Explicit masks and manual palette maps

`segment_texture_with_anchors(project_id, asset_id, colors, revision, resolution=1536)`
uses 1–32 caller-selected RGB prototypes, not local model inference. Resolution
is 256–4096. It retains up to 64 significant disconnected regions per anchor;
tiny islands join the nearest retained region **of that same anchor**, never
another color. An anchor with no pixels raises an error. One anchor supports
uniform materials without inventing shading. IDs are zero-based; numbered
preview labels display ID+1. Inspect the result before assigning semantics.

`split_region_polygon(project_id, asset_id, region_id, points, revision)` splits
the intersection of a region and a polygon into a fresh ID with the same color
cluster. Supply 3–256 normalized `[x,y]` points, from top-left `[0,0]` to
bottom-right `[1,1]`. The polygon must leave pixels on both sides. Both tools
require the exact current revision. Anchor replacement refuses to erase an
existing semantic plan. Polygon refinement preserves prior names, colors and
heights, starts its new region with the source region's values, and invalidates
approval; submit an updated semantic plan for a changed interpretation. Only
the two affected regions are measured again. Native reconstruction uses nearest component identity from the work mask,
so inspect the polygon boundary at the chosen resolution.

`export_manual_palette_maps(project_id, destination)` requires an absolute NEW
directory and explicit `displacementColor` on every region of every texture.
It writes native-resolution RGBA PNGs, preserving the original alpha byte for
byte, and `manual-palette.json` with profile `manual-rgb-preserve-scene`.
Filenames use validated asset IDs; the manifest retains original material names.
Exports stage beside the destination and publish only after success. A failed
export can be retried to the same absent destination; successful destinations
are never overwritten. Reopen the isolated library to resume editable work.

These RGB maps are the manual production output. Keep the scene's existing
image colorspace and original Figure Tools nodes; do not feed them through the
legacy signed-height bridge or convert them with arbitrary luminance weights.
The tool does not force sRGB/Non-Color, open Blender, evaluate geometry, or claim
that ordinal heights reproduce the manual palette's physical displacement.

```text
python -m pytest -q tests/test_external_mcp.py
```

Tests use tiny synthetic images, reject stale/invalid plans and Blender inputs,
check non-overwriting full-folder round trips, forbid network/process execution
during CPU preparation, and perform a real MCP initialization/tools-list/call
stdio round trip. SDK reference: https://github.com/modelcontextprotocol/python-sdk
