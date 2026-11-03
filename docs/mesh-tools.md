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
- Parameters: `ratio` (fraction of triangles kept), `target_error` (mm; with both, whichever comes first), `mode`
  `attribute` | `sloppy`, `keep_borders`, `save` (false only measures and returns a preview render).
- Output: a NEW binary STL in `<data dir>/variants/<file>-m<id>-lod-<mode><ratio|error>[-b].stl`, indexed in the
  library (import root `Imported · variants`), named `<source> · LOD <n> %`, tagged `lod`, `variante`, with
  `source_ref = hoard://vulcan/model/<source id>`. The source file is never written. The same settings rewrite the same
  variant.
- Error: measured, not estimated. Distances from every vertex (up to 30 000 per side) and 1 500 surface samples of
  the original to the simplified surface and back, with `trimesh.proximity.closest_point` (needs `rtree`, pinned in
  requirements). Reported: `hausdorff_mm` (max), `mean_mm`, `p95_mm`, `relative` (to the bbox diagonal).
- REST: `POST /api/models/{id}/simplify`, `GET /api/models/{id}/variants`. MCP: `model_simplify`.
- UI: model page → **Simplificar (LOD)**: slider with the expected triangle count, mode, borders, optional max error,
  **Vista previa** (before/after renders, measured error) and **Crear variante**; the list of variants links to them.

## Compressed GLB for the web

- `export_web_glb` writes `<data dir>/exports/web/<file>-m<id>-<sha8>.glb` with `gltfpack -cc` (npm `gltfpack@1.3.0`,
  MIT): EXT_meshopt_compression plus quantization. Without Node/gltfpack it writes a plain GLB and says so.
- REST: `POST /api/models/{id}/web-glb`, `GET /api/models/{id}/web.glb`. MCP: `model_export_web_glb`.
- UI: model page → **GLB comprimido para la web**: export, download, and **Ver en el visor** (three.js `GLTFLoader`
  with `MeshoptDecoder`).

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
