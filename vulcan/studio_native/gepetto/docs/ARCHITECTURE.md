# Architecture

## Separate recognition from height decisions

The vision stage first describes the complete figure without height instructions or UV IDs. It then assigns physical roles to existing spatial texture regions using that observation and a contact sheet: full original, selected IDs projected onto the original 3D figure, and numbered UV atlas. Keeping surrounding materials colored helps distinguish real eyes from separate cheek markings. Allowed role strings and response shape are included in the prompt as well as validated by the schema. The deterministic artist profile assigns initial heights afterwards. This reduces the risk of an eye-height instruction becoming a mistaken analogy for a nose or beak.

A focused audit selects interior colors that differ substantially from their assigned surface. Color is a reason to inspect a region, never a rule that dark means recessed. Thin fragments, ordinary tone variations and candidates covering more than 30% of their group are filtered. The last limit prevents an isolated crop from rewriting a large modeled surface. The audit keeps an image, selected IDs and model response for every change.

Small parts receive a separate original-versus-uniform comparison. The model first lists painted shapes that disappear and shapes already present in the geometry. A neutral map is considered only for a part spanning at most 35% of the figure's extent, with at least 95% of its atlas pixels belonging to regions observed from the front, and with no missing painted shape reported. This avoids converting baked shading on a modeled muzzle into terraces. It remains a conservative heuristic, not proof about every occluded texel. A generic empty or contradictory response cannot establish that a part is already modeled.

Printed decorations receive a bounded contrast pass after recognition. A palette class also identified as fabric can continue the fabric background through a motif. Distinct nested ink shapes receive separate heights when the model initially flattened them together. This operates only on recognized decorations, preserves already distinct levels and records each change; it does not reinterpret skin or ocular roles. Focused visual inspections carry provenance so their specific result can continue across a compatible shared edge without being overwritten by the initial broad assignment.

## Keep geometry and UV identity

UV triangles determine texture coverage. Palette colors split into sufficiently large disconnected regions before interpretation. At native resolution, nearest-color classification preserves edges while a spatial lookup distinguishes different uses of the same color. Shared mesh edges constrain compatible neighboring surfaces across materials; a specific feature can continue across an edge without changing the entire skin group.

Exports use 16-bit RGBA rows with overlapping blur margins. Unused UV pixels receive padding from the actual surface rather than unrelated background colors. The blur margin matches the filter's complete support, avoiding horizontal band boundaries. A content cache includes source data, masks, heights and the exporter version.

Batch import also builds a lightweight GLB from the source world triangles and UV coordinates. The workshop links the existing texture assets by material name; it does not embed another copy of every 8K image. This provides navigation and region selection independently of the rendered displacement evidence.

## Evaluate the result, not just the plan

The worker imports the installed Figure Tools nodes and uses its demonstrated preparation settings. A uniform-height control distinguishes existing geometry from added displacement. Body and clothing retain independent modifier stacks. For a complete figure, intermediate phases export their maps and defer displacement renders and visual review until assembly; compact-part inspection can still request a uniform control. The reviewer sees the finished displaced union and an equivalently finished control. Complete figures receive a separate rear garment review as well as the face: a correct face cannot hide a missing back print. Corrections are bounded and candidate selection can retain an earlier result. Automatic revisions that invert an already valid eye-layer ordering are rejected; explicit user edits remain literal.

Geometry measurements are diagnostics. Zero open edges cannot prove that all intended surfaces exist; a failed trial lost a thin garment while producing a closed mesh. Original, front, back and detail images remain part of validation.

## Recover without losing work

Project history uses SQLite WAL plus immutable, hashed mask blobs. Publishing a revision commits its document and mask references before replacing current files. Startup recovery repairs interrupted publication. Undo/redo restores complete project state, while monotonic revisions reject stale edits.

The queue is a persistent single consumer with immutable phase paths. Recovery marks interrupted runs instead of pretending they completed. Source signatures and a pipeline fingerprint prevent retries from silently combining phases made from different inputs or implementations. Model memory operations are blocked while application inference is active.

## Boundaries

The current complete-figure recipe recognizes the demonstrated `mTops` clothing convention. Arbitrary rig/material layouts and Pokémon Masters have not yet received equivalent end-to-end validation. All game assets, model weights, generated evaluations and local histories stay outside the public repository. Faustus informed operational design; its AGPL server code was not incorporated.


## Scene reasoning under evaluation

A new scene contract compares original and uniform-control views from the front and back before classifying UV regions. It records observed pieces, appearance, location, visible counts, modeled versus painted evidence, and relations such as covers, inside and continues. Keys and relation references are validated; the contract never assigns numeric heights. Models advertising Ollama's thinking capability can reason during this scene stage. The UV assignment stage uses the contract and actual projected image locations, links each group to an observed part, and retains the deterministic artist style. Hidden regions receive no invented screen position.

The contract is a hypothesis grounded in images, not ground truth. Its errors remain possible and downstream image evidence takes precedence. Separate focused checks receive both the UV crop and the selected region IDs on the original model. Complete-figure batches reuse the body's scene contract for clothing. Current trials separate cached preparation from new semantic inference; no benchmark claim should treat reuse of a prepared scene as a cold start.


When a specific feature or a conspicuous color outlier is hidden in the mapping view, seven geometry projections score where it is actually visible. The worker renders an original and a uniform control from the selected angle and asks for a short grounded identification, with the front view retained for orientation. This separates missing visual evidence from the amount of reasoning used. The stage is bounded per material; unavailable views and incomplete model answers remain recorded rather than being presented as successful verification.


Printing intent is not delegated to perception. A model may correctly see a painted freckle and still suggest leaving it flat for later painting. `apply_print_intent` retains the observed classification and geometric evidence, but derives the printing requirement from the user's unpainted-figure objective. Raw model responses remain in the evidence directory. The editor presents the observation and this printing objective separately in native per-part disclosures, persisted with the evaluation.

Focused checks preserve both attempts when a thinking response runs out of output. One compact retry sees the same images; if that also truncates, the previous heights survive and the unresolved inspection is recorded as a warning. This bounded recovery does not certify the semantic decision.


Active inspections operate only on IDs visible in the chosen camera. Other IDs retain their previous values. Spatially distant patches of one color are separated before requesting a single identity; mirrored locations may share evidence, while different levels and depths are inspected separately. This partition uses normalized mesh bounds and never assigns semantic roles or heights itself.


Extended thinking is reserved for whole-scene interpretation and ambiguous color checks. Active physical-view checks default to a compact decision after identical-input ablations preserved useful part identities and heights at substantially lower latency; an explicit reasoning parameter remains for experiments. Color-check ablations changed a sucker center into a rim, so that stage retains the more deliberate default.
