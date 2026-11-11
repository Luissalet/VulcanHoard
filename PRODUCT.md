# Vulcan's Hoard

Vulcan's Hoard is a modular 3D studio with a model library, native editors and
shared projects. Its modules cover library management (Vulcan), figure relief and
Blender projects (Gepetto), image-to-3D generation (Daedalus), silhouette
composition and extrusion (Plato), measured CAD and assemblies (Heron), and
terrain and cities (Gulliver). Modules and automation require explicit activation;
discovering the API/MCP catalogue starts no engine.

The library's **Habitación** workspace adds a local sketch-to-room workflow:
upload a sketch or a photograph of one, trace or interpret an editable measured
plan, review openings and furniture volumes, then build a new textured GLB in the
library. It retains the plan, runnable reconstruction program and optional sketch.
The same room operations are available through the native REST API and MCP.

This workflow handles one simple, possibly concave room, vertical walls, a flat
floor, rectangular openings and furniture boxes. Image calibration uses the width
of the entire image in metres. Measurements and vision assumptions need review;
it does not recover hidden dimensions, correct photograph perspective
automatically, certify architectural measurements or produce detailed furniture
or a guaranteed watertight printable building.

Manual tracing, construction and deterministic procedural materials need no
language model or GPU. Automatic interpretation requires the configured global
vision service; the existing optional projection texturing flow requires its
render and image engines. Live QA exercised the manual flow; vision was tested
with a simulated contract response because the global service was unavailable.
Generative projection was inspected through status, not generated in that run.

See [README.md](README.md), [room workflow and limits](docs/ROOM-SKETCH.md), and
[the local surface brief](.impeccable/surfaces/room-sketch.md).
