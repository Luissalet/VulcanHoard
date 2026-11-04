# Mesh tools of the library

Four tools on top of the model library: lighter LOD variants, a multi-view look for assistants, opening models in a
slicer, and a dense-mesh viewer mode. Each one has a UI control, a REST route and an MCP tool.

## Simplify / LOD variants

- Code: `vulcan/simplify.py` (engines, error measure, variants, web GLB), `scripts/meshopt_tool.mjs` (Node bridge).
- Engines, best first: **meshoptimizer** (zeux/meshoptimizer, MIT) through its WebAssembly build from npm
  (`meshoptimizer@1.3.0`), run with Node: quadric simplification with `LockBorder` and an absolute error target, and
  `simplifySloppy` for the sloppy mode. The PyPI `meshoptimizer` package only ships a source archive (no Windows
  wheel), hence the Node route. Fallbacks: `fast-simplification` (quadric, `preserve_border`), then vertex clustering
  (numpy). `GET /api/simplify/engines` says which ones are available.
- Parameters: `ratio` (fraction of triangles kept), `target_error`, `mode` `attribute` | `sloppy`, `keep_borders`,
  `save` (false only measures and returns a preview render), `allow_over_target`.
- `target_error` is a **maximum surface deviation in mm measured with the reported metric** (`error.hausdorff_mm`,
  below), not the engine's internal error. Each result is measured; while it is above the target the engine's own knob
  is tightened (meshoptimizer error limit, fast-simplification kept ratio, clustering cell size) in proportion to the
  overshoot, then bisected between the last passing and first failing setting, up to 8 measured tries; the passing
  result with the fewest triangles wins. With `ratio` too, the ratio is the floor of kept triangles. The result says
  `target_met`, `target_metric`, `target_attempts` (scale, triangles, measured max per try), and `engine_error_mm` is
  labelled as the engine's estimate only. When no try meets the target the call returns `ok: false`,
  `target_met: false` and the closest result (with a preview) and **saves nothing**, unless `allow_over_target: true`
  (then it is saved, still `ok: false`, and its notes say `NOT met`).
- `keep_borders` locks open-border vertices and edges in every engine: meshoptimizer `LockBorder` (attribute) or a
  lock mask (sloppy), fast-simplification `preserve_border`, and clustering, where each border vertex is its own
  cluster so it never moves and no border edge can collapse. The result checks it (`borders`: border vertices/edges
  kept, `borders_kept`); a result whose borders moved is reported `ok: false` and never saved.
- Output: a NEW binary STL in `<data dir>/variants/<file>-m<id>-lod-<mode><ratio|error>[-b]-<variant key>.stl`, indexed
  in the library (import root `Imported · variants`), named `<source> · LOD <n> %`, tagged `lod`, `variante`, with
  `source_ref = hoard://vulcan/model/<source id>`. The source file is never written. Identity: `variant_key` = first 16
  hex of SHA-256 over the canonical JSON `recipe` (recipe version, source id, source file SHA-256, effective engine,
  mode, full-precision ratio and target, keep_borders). The readable part of the name is only a label, so ratio 0.5001
  and 0.5004 are two variants with their own bytes; the same source + settings rewrite the same variant (idempotent);
  a new source revision makes a new one. The recipe is in the result and in the variant's notes.
- Error: measured, not estimated. Distances from every vertex (up to 30 000 per side) and 1 500 surface samples of
  the original to the simplified surface and back, with `trimesh.proximity.closest_point` (needs `rtree`, pinned in
  requirements). Reported: `hausdorff_mm` (max), `mean_mm`, `p95_mm`, `relative` (to the bbox diagonal).
- REST: `POST /api/models/{id}/simplify`, `GET /api/models/{id}/variants`. MCP: `model_simplify`.
- UI: model page → **Simplificar (LOD)**: slider with the expected triangle count, mode, borders, optional
  **Desviación máxima de la superficie** (mm, the measured Hausdorff maximum), **Vista previa** (before/after renders,
  measured deviation) and **Crear variante**; the result says whether the maximum was met (and offers **Guardar
  igualmente (marcada)** when it was not), whether the borders were checked in place, and the recipe key; the list of
  variants links to them.

## Compressed GLB for the web

- `export_web_glb` writes `<data dir>/exports/web/<file>-m<id>-<sha8>.glb` with `gltfpack -cc` (npm `gltfpack@1.3.0`,
  MIT): EXT_meshopt_compression plus quantization. Without Node/gltfpack it writes a plain GLB and says so.
- Units: glTF lengths are metres (glTF 2.0, "Coordinate System and Units"). The library geometry is millimetres
  (whatever unit a 3MF/OBJ declared is converted on load), so the export scales it by 0.001: a 40 x 20 x 10 mm part
  decodes as 0.04 x 0.02 x 0.01 m in any glTF viewer. The result and the `.json` sidecar declare `units: "m"`,
  `source_units: "mm"`, `scale_from_source: 0.001`, `extent_mm`, `extent_m`, `quantization_tolerance_m` (two
  14-bit position steps of the largest extent when compressed) and `semantics: "gltf-metres-v2"`. A cached GLB
  without that semantics tag (written with millimetre numbers) is rebuilt on the next request.
- `scripts/glb_extent.mjs <file.glb>` decodes a GLB with the shipping three.js `GLTFLoader` + `MeshoptDecoder`,
  applies every node transform and prints the world extent in metres (used by the tests and for QA).
- REST: `POST /api/models/{id}/web-glb`, `GET /api/models/{id}/web.glb`. MCP: `model_export_web_glb`.
- UI: model page → **GLB comprimido para la web**: export, download, the metre extent, and **Ver en el visor**
  (three.js `GLTFLoader` with `MeshoptDecoder`; the viewer scales the metre GLB by 1000 so it shows at the same
  millimetre scale as the library files, and prints the size it shows).

## Look and scene summary

- Code: `vulcan/look.py`, named views in `vulcan/thumbnail.py` (`iso`, `top`, `front`, `side`, `back`, `left`,
  `bottom`; Z up, front looks from -Y, right side from +X), rendered with the pure-Python thumbnail renderer onto one
  PNG sheet, cached in `<data dir>/look/` by file hash, views and size.
- MCP: `model_look` returns the sheet as MCP image content (`_image`, forwarded by the bridge with
  `image_content=True`) plus `image_url` and the scene summary; `model_scene_info` is the text-only summary: bbox,
  triangles, vertices, volume, surface, watertight, bodies, units guess, warnings (open mesh, several bodies, wrong
  units, under 0.8 mm, over 1 M triangles, missing file), source model and variants.
- REST: `GET /api/models/{id}/look.png?views=iso,top,front,side&size=384`, `GET /api/models/{id}/look`,
  `GET /api/models/{id}/scene`.
- UI: model page → **Vistas y comprobación** (warnings, view set, **Ver vistas**).

## Listing photos with Blender (headless)

- Code: `vulcan/blender_render.py` (settings, cache, contact sheet) and `vulcan/blender_photo_worker.py` (runs inside
  Blender: `blender -b --factory-startup --python blender_photo_worker.py -- spec.json`). Blender is found with
  `VULCAN_BLENDER`, then the newest `Program Files\Blender Foundation\Blender X.Y\blender.exe`, then PATH; works on
  3.6 to 5.x (STL importer and EEVEE engine ids changed in 4.2).
- The mesh goes to Blender as a temporary STL (STL/3MF/OBJ alike; over 1.5 M triangles it is decimated for the photo
  only). It is placed bottom-centre on a photo backdrop (floor, curved sweep and back wall turned towards each camera, so
  no horizon line shows), scaled to a fixed size, with an orbit camera fitted to its bounding sphere and studio light.
- Angles: `hero` (3/4 from the front right), `iso`, `front`, `side`, `back`, `left`, `top`. Engines: `workbench`
  (studio clay look, about a second per photo), `eevee` (area lights) and `cycles` (path traced on the CPU, the GPUs
  stay free; denoised). Filament and background colour, transparent PNG (Cycles keeps the floor shadow with a shadow
  catcher), size 256-2048 px, samples.
- Cached in `<data dir>/renders/m<id>-<file hash>-<settings hash>/<angle>.png` with `meta.json` and a contact sheet;
  only missing angles are rendered, in one Blender process.
- MCP: `model_render_photos` (returns the photo URLs and the contact sheet as an image). REST: `GET /api/blender`,
  `POST /api/models/{id}/renders`, `GET /api/models/{id}/renders`, `GET /api/models/{id}/renders/{key}/{file}.png`
  (503 with the reason when Blender is missing or fails).
- UI: model page → **Fotos para la ficha (Blender)** (angles, engine, filament and background colour, transparent PNG,
  size, **Renderizar fotos** / **Volver a renderizar**, the photos and the saved sets).

## Open in slicer

- Code: `vulcan/slicers.py`. PrusaSlicer, Bambu Studio, OrcaSlicer and UltiMaker Cura are autodetected in their usual
  Windows install folders (and on PATH); a path saved in `<data dir>/slicers.json` wins. The slicer is started
  detached with the file paths as arguments; up to 50 files per call.
- REST: `GET /api/slicers`, `PUT /api/slicers` (`{paths: {id: exe}, default}`), `POST /api/slicers/open`
  (`model_ids` or `root_id` + `path`, `slicer`, `dry_run`). MCP: `slicers_list`, `model_open_in_slicer`.
- UI: **Abrir en laminador** in the model header, on every root in Carpetas and on every folder row in Fichas
  (with a selector when several slicers exist); paths and default in Ajustes → **Laminadores**.

## Dense-mesh viewer mode

- For models of 300 000 triangles or more, when the browser exposes WebGPU, the viewer offers **Modo denso
  (WebGPU)**: `WebGPURenderer` (three `0.187.0`, from `three/webgpu`) plus `three-virtual-geometry@0.1.3` (MIT), which
  splits the mesh into clusters with a LOD hierarchy and picks the detail per frame on the GPU.
- If the GPU device cannot be created (three would silently fall back to WebGL2) or the conversion fails, the viewer
  says why and returns to the normal WebGL viewer. Without WebGPU, very dense models show a hint to create a LOD
  variant instead.
- three was moved from `^0.180.0` to `0.187.0` because three-virtual-geometry uses TSL nodes added after r180.
  `vite.config.js` makes runtime chunk URLs relative so lazy chunks also load when the studio serves the app under
  `/library/`.

## Third-party

meshoptimizer (MIT, Arseny Kapoulkine), gltfpack (MIT, same author), three-virtual-geometry (MIT, Elad Ben-Haim;
includes code derived from nanite-webgpu, MIT) and three.js (MIT) are npm dependencies; their licences ship in
`node_modules/*/LICENSE*` and in the client bundle. rtree (MIT) is a Python dependency.
