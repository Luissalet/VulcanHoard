# Vulcan's Hoard

Vulcan's Hoard is a local 3D studio: a searchable library of your printable models plus editors for relief, image-to-3D, silhouettes, measured CAD and terrain.

[Español](README.es.md)

## What it is

Vulcan indexes the STL, 3MF, OBJ, GLB, glTF and FBX files you keep on your own PC. It measures every model, renders a thumbnail on the CPU, finds duplicates and keeps tags, notes and a marketplace listing per model. An assistant reaches the same library through MCP, so it can find a model, quote its real dimensions and draft its listing without inventing anything.

Around the library sit six independently activated modules: the library itself (Vulcan), editable figure relief and Blender projects (Gepetto), image-to-3D generation and refinement (Daedalus), silhouette composition and extrusion (Plato), measured CAD with assemblies (Heron) and printable terrain and cities (Gulliver). All of them start off on first use; open Vulcan, switch on the function you need and stop it from the same screen. Listing the MCP tools starts nothing. Shared projects keep native object ids, revisions and exported-file references with SHA-256, so an exported STL can be indexed in the library without converting the native project.

The library stays on the machine: SQLite with FTS5 and WebP thumbnails, no account.

## What it does

**Library**
- Roots are folders you choose, with include and exclude globs, per-root thumbnail policy and a minimum file size. Scans are incremental (size and mtime, then SHA-256), run in a process pool, show progress and ETA, and watch folders for changes.
- Geometry through `trimesh`: triangles, bounding box in mm, volume, surface, watertight flag, body count and a unit guess. GLB and glTF are measured as scenes; FBX goes through Blender when it is installed.
- Pure-Python thumbnails (numpy and Pillow, no GPU or OpenGL), a three.js viewer with orbit controls and a build-plate grid.
- Search with SQLite FTS5 (accent-insensitive, prefix match) over name, tags, notes, collection and listing text, with filters by root, format, tag, collection, album, watertight, listing, duplicates, status, size, recorded size range and triangle range.
- Exact duplicates by hash, near duplicates by triangle count, volume and extents, with a review page for both.
- Folder collections plus albums you build by hand; tags and notes survive rescans.
- Revision comparison of two STL, OBJ or 3MF files in physical units, with optional rigid alignment and a deviation view.
- LOD variants with a measured maximum deviation, multi-view renders, listing photos rendered by Blender, web GLB export, glTF validation with the Khronos validator, and slicer discovery.
- AI texturing by view projection (Blender and ComfyUI), a 3D table of many models with octahedral impostors, and rooms built from a sketch or photographed plan into a textured GLB.

**Marketplace listings**
- A listing per model (title, markdown description, tags, category, price hint, language) and a "copy listing" button.
- Folder listings for products that are a whole folder: `cults3d.json` with title, English description and exactly 20 tags, validated against an optional template, with statuses none, draft, checked and approved, local-model drafting, a deterministic skeleton mode for every folder at once, and export to CSV, Markdown or JSON.
- Organise a collection: paste a list of groups and members and Vulcan plans, applies and undoes the folder moves without deleting or overwriting anything.
- A combined catalogue export that Mercator can import.

**Studio**
- Text or spec to parametric CAD in millimetres: primitives, booleans, patterns, dimensioned sketches, extrusions, revolves, named parameters, design-intent reports, fitting a recipe to a measured mesh, regeneration and variants.
- Constrained sketches, named-face operations, assemblies with relations, interference and thread-clearance checks (Heron).
- Modelling with code: a short program written by the family model, run in an isolated Blender-backed process and checked against the measures in the description.
- Destination inspection and preparation for web configurators and FDM printing, and for animated assets (rig and clip checks, trimming, resampling, FBX import and export), always writing a new file with a receipt.
- Image-to-3D with a joint multiview route, editable figure relief, silhouette extrusion, and printable terrain and cities.
- Spatial analysis: where to place service sites to cover demand within a travel time, and parcel screening by slope, access and restrictions.

The interface is in Spanish.

## Run it

Requirements: Windows 10/11 (Linux and macOS also work), Python 3.11 or newer with SQLite FTS5, Node 22 only to build the client. CPU only for the library. Blender, ComfyUI and the Daedalus GPU environment are optional and used only by the operations that need them.

```bat
git clone <this repo> vulcan-hoard
cd vulcan-hoard
python -m venv venv
venv\Scripts\pip install -r requirements.txt
npm install
npm run build
venv\Scripts\python -m vulcan
```

Open http://127.0.0.1:5186, go to Carpetas and add a folder. `python scripts/launch.py` starts it on a free port and opens the browser; `python scripts/dev.py` runs the development servers; `python scripts/selftest.py <folder>` scans a folder into a temporary data directory and prints a report. The studio interfaces are built with `python scripts/build_studio.py` (see `docs/STUDIO.md`).

| Variable | Default | Meaning |
|---|---|---|
| `VULCAN_PORT` / `PORT` | 5186 | Preferred port (`PORT_STRICT=1` pins it) |
| `VULCAN_DATA_DIR` | `<repo>/data` | Database, `mcp-token`, thumbnails, stored plans |
| `VULCAN_ALLOWED_HOSTS` | | Extra host names accepted behind a tunnel, comma separated |
| `VULCAN_THUMBS` | 1 | 0 skips thumbnails |
| `VULCAN_THUMB_SIZE` | 512 | Thumbnail side in pixels |
| `VULCAN_MAX_FILE_MB` | 300 | Larger files are listed as skipped |
| `VULCAN_SCAN_WORKERS` | cpu count minus 2 | Worker processes for hashing, parsing and rendering |
| `VULCAN_SKIP_SMALL_BYTES` | 0 | Default minimum file size for new roots |
| `VULCAN_WATCH` | 1 | 0 disables folder watching |
| `VULCAN_AUTOSTART` | 0 | 1 rescans every root at startup |
| `VULCAN_FAUSTUS_URL` | | Faustus endpoint (also `HOARD_FAUSTUS_URL`) |
| `VULCAN_BLENDER` | newest install | Blender executable |

The server binds to 127.0.0.1 and answers only localhost hosts; to reach it from a phone through a tunnel, list the host names in `VULCAN_ALLOWED_HOSTS`. Local-model drafting uses Hoard Link, which finds Faustus's registry or a loopback llama.cpp or Ollama server; pin one with `<DATA_DIR>/backend.json` or the `HOARD_LLM_*` variables.

## Connection with Faustus and the Hoard Hub

Declares production handoffs for image-to-3D reference meshes and print mesh measurements. The Hub can also use the native studio switches. A reference mesh is the input to further modelling, and a print measurement feeds the slicer check.

- Vulcan is a Faustus plugin (`faustus-plugin.json`, port 5186) and a member of the Hoard Hub family; it vendors Hoard Link in `vulcan/hoard_link/`. The global model is read from wherever Faustus answers, including a Spark when the Hub's "Run on" has moved it there.
- The floating Faustus button opens the shared copilot panel in every view and passes the current model, folder listing, root or album, filters, selection and form draft. Conversation and execution belong to Faustus. See `docs/copilot.md`.
- Opened beside a Faustus chat, the page registers `vulcan_project`, `vulcan_filter`, `vulcan_module` and `vulcan_view` through the App Dock.
- MCP: `mcp_server.py` is a stdio bridge to `GET /api/agent/tools` and `POST /api/agent/call` using the token in `<DATA_DIR>/mcp-token` (`VULCAN_URL`, `VULCAN_TOKEN_FILE`). The catalogue holds more than two hundred tools, grouped as: library (search, info, listings, tags, notes, delete, stats, duplicates, roots, rescan, import, comparison), folder listings and organising, CAD (spec schema, from text, regenerate, design intent, fit), destination inspection and preparation, animation, glTF validation, texturing, code modelling, impostors, rooms, Heron assemblies, spatial analysis, and the modules' own tools. Long names have short aliases. The tool instructions tell the assistant to describe models only from measured data.
- Skills from the family library for the connected agents: `print-ready-3d-model`, `web-3d-asset-pipeline`, `blender-asset-production` and `verification-loop`.
- Older MCP and Hub identities keep working through cold compatibility adapters (`docs/NATIVE-CONNECTIONS.md`).

REST routes live under `/api/` (health, status, roots, models, search, collections, folder listings, organise, CAD, asset inspection, animation, copilot, agent); the full list is in `docs/API.md`.

## Layout

| Path | Contents |
|---|---|
| `vulcan/` | Python package: scanner, geometry, thumbnails, database, API, CAD, MCP tools |
| `vulcan/studio_native/` | The studio modules (Daedalus, Gepetto, Gulliver, Heron, Plato) |
| `vulcan/hoard_link/` | Vendored Hoard Link |
| `client/` | React interface of the library |
| `scripts/` | Launcher, dev server, self-test, studio build, Node helpers |
| `docs/` | Guides: studio, CAD fitting, comparison, mesh tools, rooms, spatial analysis, copilot |
| `tests/` | pytest and Node tests |
| `data/` | Database, token, thumbnails, exports (not tracked) |

## Tests

```bat
venv\Scripts\python -m pytest -q
```

## Limits

- The library scans STL, 3MF, OBJ, GLB, glTF and FBX; STEP, AMF and PLY are not scanned.
- The volume of an open mesh is a signed-volume estimate. Near-duplicate groups are suggestions, not proof of copies.
- The CAD generator writes STL (no STEP), and its built-in boolean engine is pure Python, so very heavy specs take seconds.
- Minimum wall thickness is a sampled estimate, not a bound.
- Room sketches produce a parametric scene with vertical walls, not certified measurements.

## Licence

GPL-3.0 for the combined studio. Original component grants are kept in [NOTICE-STUDIO.md](NOTICE-STUDIO.md).
