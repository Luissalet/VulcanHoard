# Heron assemblies (CAD assemblies with relations and interference)

Heron's measured CAD module now places parts relative to each other with explicit relations between named references, solves rigid-body poses deterministically, and checks interference with the native CAD kernel (CadQuery/OCCT). Code: `vulcan/studio_native/heron/heron/assembly/`. UI: the **Ensamblajes** section of `/heron/`. Tests: `tests/test_heron_assembly*.py`.

## Audit before this work

- Heron recipes (`heron/geometry.py`) build one solid from unnamed primitives and export STEP/STL/SVG; there were no assemblies, no named references and no relations. Builds are immutable receipts with SHA-256 per export.
- `vulcan/cad.py` + `vulcan/csg.py` (library module) generate meshes with a BSP/manifold boolean engine; they have parameters but no components or poses.
- Gepetto's `assembly.py` combines figure/garment pieces in Blender; Gulliver's mounting guide describes how printed tiles fit. Neither solves relations between arbitrary parts.
- CadQuery 2.8 is a declared dependency (`requirements-studio.txt`). Its own `Assembly.solve` is an optimiser that returns one answer without classifying ambiguity or conflicts, so it is not used; its STEP assembly export/import is.

## Model

**Components**: an id, units (`mm`, or `in` converted to mm), a source and an initial pose.
- `family`: parametric parts with semantic references — `enclosure`, `standoff`, `plate` (board or lid), `bracket`, `screw` (nominal cylinder, optional declared thread metadata), `block`. Parameters are numbers or arithmetic over assembly variables (`"standoff_h + board_t + gap"`; only `+ - * /`, parentheses and names).
- `build`: a finished Heron build. Its STEP is hash-checked, read and never written; references are derived from the recipe primitives (`p2.axis`, `p0.top`…).
- `mesh`: inline vertices/faces (no file paths). Only datum references; interference uses the mesh approximation below.

Each component records its revision (family + mm parameters digest, or build id + STEP SHA-256 + recipe digest, or mesh digest).

**References** are planes (point + outward normal), axes (point + direction) and, since the second delivery, named points, all computed from the definition, e.g. `box.floor_top`, `box.mount_1`, `board.hole_3`, `screw.under_head`, plus datums `origin.xy/yz/xz/x/y/z`. Points: `<hole>_center` (`mount_i_center`, `hole_i_center`; on the bottom face), `corner_i` (bottom corners, numbered counter-clockwise from (-x,-y)), `top_corner_i` (plate, block), `bottom_center`/`top_center` (standoff), `head_center`/`tip_center` (screw). Hole axes also carry the hole diameter and axial extent (`hole_diameter_mm` in the result) for the thread check. Each one is checked against the built solid (`materialized`: a planar face on that plane with that normal, or a cylindrical face on that axis). After a parameter change the names are recomputed: `survived`, `moved_in_part`, `added` and `invalidated` are reported per component, and relations that used an invalidated reference are excluded explicitly (`complete: false`). Face references are not promised to be stable after arbitrary edits.

**Relations**: `fixed` (at least one), `coincident` (planes, normals `opposed` = faces mate, or `aligned` = flush), `distance` (plane b at a signed offset along the normal of plane a), `coaxial` (axes `aligned`/`opposed`), and `clearance` (requested minimum gap between two solids, checked after the solve). `orientation: either` is solved both ways and never chosen silently.

Added in the second delivery:
- `angle` (`value` in degrees, 0 to 180, an expression over variables is allowed): angle between the directions of two references, plane normals or axis directions *as listed* (a screw axis points into the hole, so its direction is the insertion direction). 0 aligns, 180 opposes; a general angle removes one rotational freedom and leaves the rotation about the first direction free (`under_constrained` says so). Planes and axes may be mixed. `orientation` does not apply and is refused if set.
- `point_on_plane` (`a` point, `b` plane, optional signed `value` offset in mm along the plane normal), `point_on_axis` (`a` point, `b` axis), `point_coincident` (two points). A hinge pin, for example, is `point_coincident` of two corners plus an `angle` pair for the leaf (`examples/hinge_panel.json`, variable `tilt_deg`).
- Reference kinds are checked per end: a relation that gets the wrong kind is excluded as `invalid_reference` and names the expected kinds (`point_on_plane needs point references at a; panel.top is a plane`).
- Statuses are unchanged (`solved`, `under_constrained`, `incompatible`, `ambiguous`). Per-relation residuals are in mm (points, distances) and degrees (angles, orientations); an angle's residual is the difference between the measured and requested angle.

## Solver and statuses

Unknowns: one rotation vector and translation per non-fixed component. Residuals are exact (orientation of normals/axes and positional offsets). A constructive seed aligns each newly reached component with its partner; least squares refines; deterministic rotated restarts leave orientation saddle points. Per relation the result keeps the residual in mm and degrees; tolerances 1e-5 mm and 1e-6 rad.

- `solved`: everything holds and no motion is left except rotations about declared symmetry axes (standoffs, screws), listed under `symmetric_dof`.
- `under_constrained`: everything holds; `remaining_dof` lists per component the free translations/rotations (null space of the relation Jacobian). Those components keep their previous or seeded pose; that is not a choice of solution.
- `incompatible`: relations are accepted in declaration order; the first one that cannot hold is the `failing_relation`, with its two components, residual and the accepted relations it conflicts with. Poses are those of the accepted relations.
- `ambiguous`: more than one `either` orientation works. Candidates are returned, poses stay at their initial values and interference is not evaluated.

Redundant but consistent relations (board holes 3–4, the box height formula) are counted in `redundant_equations`, not treated as errors. A general angle seeds the solve with a direction that already makes the angle, because the dot product is stationary at 0° and 180°; angles of exactly 0 or 180 use the aligned/opposed vector residual.

### Solver speed (second delivery)

The conflict search (accepting relations in declaration order, then re-solving subsets to find which accepted relations the failing one conflicts with) used to cost 46 s of CPU on the box example with `inner_height = 25` (45.6 s inside the solver; cProfile showed 129 000 residual calls from a numeric Jacobian with 42 unknowns). It now costs about 2.2 s of CPU in the whole evaluation (1.5 s in the solver), with the same result: failing relation `board_lid_gap`, components `board`/`lid`, residual 3.4 mm, conflicts with `standoff_1_on_floor`, `board_on_standoffs`, `lid_on_rim`. A plain solve of the example went from 0.35 s to 0.13 s in the solver. What changed, without altering semantics:
- Residuals and their Jacobian are exact and vectorised by relation type (the Jacobian of the free-motion analysis stays numeric as an independent check; a test compares both).
- A solve only includes the components the tested relations touch; the rest stay at their pose.
- Least squares runs in two stages: a loose run that abandons starts which end in a local minimum with a residual left, then a minimum-norm Gauss-Newton polish to rounding level for starts that reach the relations. The same restarts and seeds are used.
- Timing depends on the machine; the test only bounds CPU time loosely (12 s).

## Interference

- **Native** (both B-rep solids): OCCT Boolean common → penetration volume. A common volume equal to the smaller solid, confirmed by a point-in-solid test, is reported as `containment` — this catches a part fully inside another although no surfaces cross. Without common volume, the exact B-rep distance separates `contact` (≤ contact tolerance, default 0.01 mm) from `clearance`.
- **Mesh approximation** (`mesh_approximation_manifold3d`): only when a component has no solid. Both sides must be closed with consistent winding; manifold3d gives intersection volume and minimum gap, labelled with the tessellation tolerance. An open or unsuitable mesh makes the pair `not_checkable` and the verdict `incomplete`, never "no clash".
- Verdicts: `no_penetration`, `interference` (penetration or violated requested clearance), `incomplete`, `not_evaluated`.

## Revisions, exports and round trip

Every create, parameter change and import is a new immutable revision (`data/assemblies/<id>/r<n>/`), run in a worker process with a timeout. Parameter changes need the latest `base_revision`; a request id replays its result and is refused for different content. Invalid parameters are refused before a revision is created. Each revision stores spec, poses (matrix, translation, XYZ angles), relations with residuals, DOF, interference, reference changes and the hashes of referenced builds before/after (`originals.unchanged`).

Exports: `assembly.step` (named components with placements), `assembly.glb` (named nodes with transforms), `components/<id>.step` (local frame), `manifest.json` (spec, revisions, poses, relation results), isometric/front/top SVG. Both STEP and GLB are reimported after export and compared (names, poses, volumes) in `roundtrip`. `assembly_import` turns a manifest into a new assembly and reports the pose difference.

**Revision check** (`assembly_revision_check`): one component is swapped for another revision at the pose stored in the assembly frame. No re-solve, best fit or registration is applied, so a moved hole shows up as a relation residual and as interference.

## Surfaces

- REST: `GET /api/assembly/families`, `GET|POST /api/assemblies`, `GET /api/assemblies/{id}?revision=n`, `POST /api/assemblies/{id}/parameters`, `POST /api/assemblies/{id}/revision-check`, `POST /api/assemblies/import`, `GET /api/assemblies/{id}/revisions/{n}/files/{path}` (hash-verified).
- Agent tools (native names; Vulcan's catalogue prefixes `heron_`): `assembly_families`, `assembly_create`, `assembly_get`, `assembly_list`, `assembly_set_parameters`, `assembly_revision_check`, `assembly_import`. The cold studio catalogue has 181 rows + 8 controls = 189 tools (the second delivery adds no tools; only the `assembly_create` schema grew).
- UI: examples (box, bracket with screw, hinged panel), JSON definition, parameter/expression editor that creates revisions, status badge, relation table with residuals, conflicts, DOF, interference table, reference changes, poses, views, downloads, revision navigation and revision check.

## Example: enclosure, board, four standoffs and lid

`heron/assembly/examples/box_board_lid.json`. Variables: `standoff_h = 12`, `board_t = 1.6`, `gap = 8` (mm).

| Component | Family | Dimensions (mm) |
|---|---|---|
| box | enclosure | interior 100 × 70 × (standoff_h + board_t + gap = 21.6), wall 3, floor 3, four Ø3.2 holes at 80 × 50 |
| standoff_1..4 | standoff | Ø6 × standoff_h, bore Ø3.2 |
| board | plate | 90 × 60 × board_t, four Ø3.2 holes at 80 × 50 |
| lid | plate | 106 × 76 × 3, four Ø3.2 holes at 80 × 50 |

Relations: box fixed; each standoff `bottom` coincident with `box.floor_top` and `axis` coaxial with `box.mount_i`; `board.bottom` coincident with `standoff_1.top`; board holes 1–4 coaxial with the standoff axes; `lid.bottom` coincident with `box.rim`; lid holes 1–2 coaxial with box mounts 1–2; `board.top` → `lid.bottom` distance `gap`; clearance board/lid ≥ 5 mm.

Result (revision 1): `solved`, no remaining DOF besides the four standoff spins; standoffs at (±40, ±25, 3), board at z = 15, lid at z = 24.6; box/standoffs, standoffs/board and box/lid in contact; board to walls 5 mm, board to lid 8 mm (clearance relation met); STEP and GLB round trips exact.

Edits verified by the tests and the browser walk-through:
- `standoff_h = 20`: box grows by the formula, board at z = 23, lid 8 mm above the board, mounting axes shift 0 mm.
- `box.inner_height = 25` (fixed number): `incompatible`; failing relation `board_lid_gap` between board and lid, residual 3.4 mm, conflicting with `lid_on_rim`, `board_on_standoffs`, `standoff_1_on_floor`.
- `board.length = 104`: penetration box ↔ board, 384 mm³ (2 × 2 × 60 × 1.6).
- `gap = 3`: clearance relation board/lid violated (3 < 5 mm), no penetration.
- `board.holes = 2`: `board_hole_3`/`board_hole_4` invalidated explicitly; still solved by holes 1–2.

Bracket example (`bracket_screw.json`): L bracket 40 × 20 × 4 with a Ø4.5 hole, nominal M4 × 12 screw seated and coaxial. Revision with a Ø3.5 hole at the same pose → penetration 11.78 mm³; revision with the hole moved 1 mm → `screw_in_hole` residual 1.0 mm and penetration, both without realignment.

## Thread clearance (declared thread metadata)

A `screw` component may declare `thread: {"designation": "M4", "pitch": 0.7}` (M2, M2.5, M3, M4, M5, M6, M8; the pitch defaults to the ISO 261 coarse pitch and may be finer, never coarser). The solid stays a nominal cylinder; `shaft_d` defaults to the nominal diameter and is refused when it differs from it by more than 0.25 mm. After solving, every threaded screw is checked against hole axes that are collinear with its axis and overlap its shaft span (`fasteners` in the result; `hole_diameter_mm` on the reference). The hole is compared with the ISO 273 clearance series of the designation (M3: 3.2 / 3.4 / 3.6, M4: 4.3 / 4.5 / 4.8 mm for fine / medium / coarse):
- `too_small`: below the fine series ("Clearance hole too small for M4: bracket.hole_1 is Ø3.5 mm, the fine clearance series needs at least Ø4.3 mm");
- `tap_drill`: within 0.1 mm of the tapping drill (nominal minus pitch), so it reads as a threaded hole rather than a clearance hole;
- `clearance_ok` with the series reached (`fine`, `medium`, `coarse`, `loose`).

This finding is distinct from geometric penetration: a Ø4.2 hole does not touch a Ø3.9 shaft (no interference) but is still too small for M4, while a Ø3.5 hole produces both findings. The bracket example now declares M4; with its Ø4.5 hole the finding is `clearance_ok` (medium series).

## Limits

No strength, fit classes or manufacturing tolerances. Threads are declared metadata, not modelled helixes, and the clearance series are the ISO 273 tables for the listed designations only (no tapped-hole depth, no inch threads, no thread engagement length). The thread check only sees holes that carry diameter metadata (family holes, standoff bores), not holes of imported builds or meshes. Relations are planes, axes and named points with angles in degrees; there are no gears, joint limits or mixed units. Named points exist on the families, not on imported builds. An angle of exactly 0/180 and a general angle are different residual forms, so an angle that sweeps through 0 across revisions can change which saddle the solver starts from (the seed handles this, but it is a heuristic like the rest of the solve). The conflict set is the set of accepted relations whose removal alone makes the failing one satisfiable, found with the same restarts as before; it is a numerical search, not a proof of infeasibility. Remaining DOF keep their previous/seeded pose. The mesh path is an approximation within its tessellation tolerance. A plain evaluation of the box example takes a few seconds, mostly the CAD kernel (solids, interference, exports); a conflict search adds about 1.5 s.
