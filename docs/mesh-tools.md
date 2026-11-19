# Mesh tools of the library

Tools on top of the model library: lighter LOD variants, a multi-view look for assistants, opening models in a
slicer, a dense-mesh viewer mode, AI texturing by view projection and octahedral impostors for the many-model 3D table. Each one
has a UI control, a REST route and an MCP tool.
The library also exposes [mesh articulation](ARTICULATION.md) through MCP, with separate pieces and source-linked poses.

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
- Each export is checked with the Khronos validator and the result is stored as `gltf_validation` (see [glTF validation](gltf-validation.md)).
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

## AI texturing by view projection

`texture_project(model_id, prompt, views=4..8, style?, size, texture_size, seed, workflow, mode)` runs in a background job (stages: guides, generating, projecting, exporting, validating).

- Cameras: normalised model (largest side 2, base at z=0), lens 50 mm; 4 views = ring at 20 deg, 5 = ring alternating 14/34 deg, 6 = ring of 5 + top, 7 = ring of 6 + top, 8 = ring of 6 + top + bottom.
- Guides: Blender (3.6 to 5.x, Cycles CPU, GPUs untouched) writes 16-bit depth, camera-space normals and a clay shot per camera; guides are cached in `data/texture/guides/<key>`.
- Painting: ComfyUI at `HOARD_COMFY_URL` (else the link config, else http://127.0.0.1:8188). Workflow order: `controlnet` (depth ControlNet + image checkpoint), `kontext` (Flux Kontext), `img2img`. `texture_project_status` reports Blender, ComfyUI and the workflow that would be used.
- Projection: per texel, cameras that see it (depth test, cos > 0.15, eroded silhouette) are blended with weight cos^4; holes are filled by dilation then mean colour.
- Result: GLB (metres) in `data/variants` registered as a variant of the source model, PNG texture and front/back previews, glTF validation report. The recipe key (model, prompt, style, views, sizes, seed, workflow) makes repeated calls reuse the variant unless `force`.
- Without ComfyUI or models the call fails with a clear message; `mode=preview` still works.

## Octahedral impostors and the 3D table

For views with hundreds or thousands of models, each model gets an **impostor**: an atlas of N x N views baked on the CPU, so a far
model is drawn as two triangles. Code: `vulcan/impostor.py` (directions, bake, cache, batches), `client/src/impostor.js` (three.js
material and texture arrays), `client/src/pages/Mesa.jsx` (the **Mesa 3D** page).

- Views: hemi-octahedral map of the upper hemisphere (Z up, as the library prints). Cell (i, j) of an N x N grid looks along the direction
  decoded from u = -1 + 2i/(N-1), v = -1 + 2j/(N-1): x = (u+v)/2, y = (u-v)/2, z = 1 - |x| - |y|, normalised. Each view is an orthographic
  render of the bounding sphere (centre = bbox centre, radius = farthest vertex), camera basis right = normalize(Z x d) (X when d is
  vertical), up = d x right. Grids 4, 6, 8, 10, 12, 16 (default 8 = 64 views); 16 to 128 px per view (default 32, a 256 x 256 atlas).
- Bake: the thumbnail rasteriser (numpy + Pillow, no GPU, no Blender), meshes decimated to 20 000 triangles first, back faces skipped on
  closed meshes, every view drawn at 2x and box-filtered with premultiplied alpha. Lighting is baked in world space the way three.js lights
  the same material in the model views (hemisphere, key and fill lights, Lambert in linear space, sRGB out), so a model keeps its tone
  when it swaps to its mesh. Two PNGs per model: `color` (RGBA, transparent outside the model) and `depth` (8-bit,
  0.5 + dot(p - c, d) / 2R scaled to 1..255, 0 = empty). Measured: 1000 library models of up to 50 000 triangles baked in about 8 minutes
  on a busy CPU (about 0.5 s each); a 3MF of a million triangles takes about a minute (loading and decimating it). `BAKE_VERSION` in the
  metadata makes atlases from an older bake count as missing.
- Measured on the table (RTX 4070 Ti, Chromium without frame cap): 100 models, about 4 000 fps with impostors (3 draw calls, 200 triangles)
  against about 1 500 fps as meshes (100 calls, 540 000 triangles); 1000 models, about 3 300 fps with impostors (17 calls, 2 000
  triangles) against about 70 fps as meshes (about 960 calls, 4.9 million triangles).
- Cache: `data/impostors/<sha[:2]>/<sha>-g<N>-c<cell>.{png,depth.png,json}`, keyed by the file's SHA-256 (identical files share one atlas,
  an edited file gets a new one). The JSON keeps centre, radius, faces used and bake time.
- Viewer: atlases are packed into `DataArrayTexture`s (256 x 256 layers, 64 models per array) and each array is one instanced draw call.
  The vertex shader turns the quad to the camera and finds the triangle of the view grid that holds the camera direction; the fragment
  shader intersects the camera ray with each of the three views' planes (so views drawn with different orientations line up) and blends
  the three samples by barycentric weight (the blend can be switched off). The table keeps each model in its own square scaled to the same
  visual size; models nearer than the chosen distance (up to a cap) load their real mesh (STL/OBJ/3MF/GLB loaders of the model viewer) and
  hide their impostor; meshes that fall behind beyond the cap are released. Models without an impostor are always drawn as meshes. Double
  click on a model opens its page.
- Page **Mesa 3D**: number of models (50 to 2000), name/collection filter, maximum triangles per model, **Usar impostores para los lejanos**, **Mezclar las tres vistas
  vecinas**, mesh distance slider, maximum real meshes, **Girar la cámara**, **Generar los N impostores que faltan** (batch with progress);
  the HUD shows fps, impostors and meshes drawn, draw calls and triangles. The same numbers are in `window.__mesa` for automated checks.
- MCP: `model_impostor(id, grid, cell, generate, force)` and `model_impostors_batch(ids?, limit, q, collection, max_triangles, grid, cell, force,
  wait, job_id, cancel)`. REST: `GET /api/impostors/{id}` (info), `POST /api/impostors/{id}` (bake), `GET /api/impostors/{id}/color.png|depth.png`,
  `POST /api/impostors/batch`, `GET /api/impostors/batch/{job}`, `POST /api/impostors/batch/{job}/cancel`, `GET /api/impostors/scene`
  (distinct models with their atlas state), `GET /api/impostors/stats`.

Limits:

- Only views from above the horizon: the table keeps the camera above it (a view from below shows the horizon view).
- The depth atlas is baked and served but the table does not use it yet (no depth-correct overlap between impostors, which sit in
  separate squares); impostors do not cast or receive shadows, their lighting is baked (no specular highlight, no light that moves),
  and at 32 px per view a near impostor looks soft, which is why the near ones switch to their mesh.
- Atlases are not mipmapped (mipmaps would bleed between views), so very small far impostors can shimmer; 1000 models use about
  256 MB of texture memory at the default layer size.
- Batches live in memory until the app restarts; the cache on disk stays.
