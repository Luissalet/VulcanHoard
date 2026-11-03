# Compare 3D revisions

The model detail's **Compare revisions** panel reads two indexed models and compares their geometry in millimetres. It preserves the existing viewer, editable notes, listings and CAD controls. No source file or library record is changed by a comparison.

Select B, the units of each file, a comparison tolerance and the sample count. **Estimate rotation and translation** estimates B's placement in A's frame. Scaling and reflections are excluded, so changes in size or handedness remain visible. **Keep original placement** measures the existing coordinates. Results always retain the raw distances; the viewer can switch between the two placements. The original placement is retained when the estimated registration makes the sampled error worse, and the panel says so.

The viewer draws A as wireframe and B as a solid, with selected out-of-tolerance points drawn above the surfaces. The coordinates of those points are also available as text, in the displayed frame. Camera buttons provide zoom and fit. Selecting a different pair, unit, tolerance or seed invalidates the displayed result; the form is disabled while a comparison runs, and a failure does not retain old results as current.

Measurements use adaptive significant precision and scientific notation for very small or large values. A positive tolerance such as0.000001mm appears as1E-6mm, not zero. The same formatter is used for distances and coordinates; two Node cases cover positive/negative small values and unavailable measurements. When no alignment was requested, the interface does not label another row as an estimated placement.

When the comparison has focus, the common Faustus panel receives its typed draft, current phase and a compact previous-result snapshot including source hashes and measurement limits. Draft tolerance and the previous operation's tolerance remain separate. The parent model's listing/notes and CAD editors retain their own context. A changed reference hash invalidates the comparison.

## Native API and tools

MCP `model_compare` and `POST /api/model-comparison` accept the same JSON:

```json
{
  "model_a": 1,
  "model_b": 2,
  "units_a": "auto",
  "units_b": "auto",
  "alignment": "rigid",
  "tolerance_mm": 0.1,
  "samples": 2048,
  "seed": 7
}
```

Model IDs are native library IDs, not arbitrary paths. Units are `auto`, `mm`, `cm`, `m` or `in`. Auto uses declared file units, otherwise assumes mm and records `units_assumed`. Explicit units override file metadata and record `units_overridden`. STL does not declare physical units. Model size is never used to guess units or to normalise scale. Default samples are 2048 per direction; the caller can choose 64–20000, with the actual count and seed included in the result.

The result contains source IDs/names/paths/SHA256, unit interpretation, measured extents/bounds/area/body count and volume validity, the raw and aligned directional distance statistics, selected locations, and `alignment.matrix_b_to_a`. The matrix is row-major homogeneous 4×4 and maps the mm coordinates of B into A. `outcome` distinguishes an estimated registration, retaining the better original placement, and no requested registration. Raw source files remain unchanged. If a file has changed since indexing, rescan and compare again.

`GET /api/models/{id}/comparison-mesh?units=auto&sha={sha256}` serves an in-memory STL in mm for the comparison viewer. The supplied hash must match the source; it does not retain another model, export file or data backup. Library and other studio modules retain their explicit activation rules. Cold discovery exposes the added tool without starting an engine, store, watcher, model or server.

## What the measurements mean

- Both A→B and B→A are measured, using vertices, face centres and seeded surface-area samples. A new hole can affect the two directions differently.
- Each sample is measured against the opposite triangular surface, not just its vertices. Maximum, mean, RMS and p95 describe the sampled distances. The outside fraction describes samples, not the fraction of surface area: vertices and face centres deliberately supplement the area samples.
- The report and viewer include up to eight most distant samples above tolerance per direction, with their opposite closest point and face index. These are locations for inspection, not an exhaustive segmentation of every changed region.
- A sampled maximum is not an exact Hausdorff distance or an upper bound for the whole surface. Small details can fall between samples. No detected deviation does not prove identity.
- Rigid registration starts from principal axes and refines with ICP. It is local, may be ambiguous for symmetric parts and can leave residual error even for the same shape. Its fit is an estimate, not a certificate of the original placement. Raw results remain available.
- Volumes are omitted for open or inconsistently wound meshes. A closed, consistently wound mesh is not certified free from self-intersection, printable or within a manufacturing tolerance. No materials, mechanical strength or fabrication suitability are inferred.
- Comparisons cover STL, OBJ and 3MF mesh geometry. They do not compare textures, rigging or CAD entity identity, and do not promise stable topological references after an edit.

## Evidence and status

Hangar, space `vulcan`, task 2016; parent research 1926 and ecosystem 1937. Revision 2 is independently approved and integrated (reviewed sources `2e570027` and `564d4118`, combined installation `1535d51`). Twelve new cases cover arbitrary rigid placement, new through hole, removed detail, uniform scale, reflection, remeshing, open geometry, explicit mm/cm, declared 3MF units, changed/missing/non-finite sources, native HTTP/tool parity and the viewer mesh endpoint. The combined geometry/API/CAD/family/cold-discovery/mesh/terrain group passes 128 Python cases. Six Node cases include the actual component context producer's success/edit/pending/error/success transitions. These overlapping suites are not added together as independent totals.

Native Main stdio exposes 170 tools (162 static rows plus eight studio controls) and agrees with HTTP on all three invented revision comparisons. The installed environment uses Trimesh 5.1.1 and Rtree 1.4.1. The running normal port 5186 was restarted through the Hub after checking its scanner was idle; stored model counts, enabled modules and automation choices were preserved. Its direct Faustus connector remains disabled and disconnected. QA used a separate data directory. The successful Main comparison does not approve the separate LOD error/border constraints under correction in task 2020.

Real stdio MCP and native HTTP were compared with invented models. With tolerance0.1mm, a recolocated cylindrical part leaves a sampled maximum0.018mm, while a new hole gives about3.98mm and a10% size change about1.26mm. Those are fixture measurements, not bounds on arbitrary jobs. Browser checks cover the shared-frame viewer, raw/aligned controls and responsive composition. Exact receipts live in `E:/Development/IA/Research/Hangar-2026-11-02/`.

Runtime dependencies are the existing NumPy/Trimesh/SciPy stack plus Rtree's CPU spatial index. No GPU, LLM or external application/CLI is used. Trimesh APIs: [registration](https://trimesh.org/trimesh.registration.html), [surface proximity](https://trimesh.org/trimesh.proximity.html); [Rtree installation](https://rtree.readthedocs.io/en/stable/install.html). The interface reuses the actual dark family theme and the incumbent light geometry viewport; no logo or raster asset is introduced.
