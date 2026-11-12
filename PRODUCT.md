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

This workflow handles concave perimeters, partitions with doorways connecting
multiple named rooms, flat ceilings and independently finished interiors and
exteriors. Doors and windows include frames, leaves and glass. Six parametric
furniture types can be placed by clicking labels on the sketch. Four-corner
projective rectification corrects photographs of planar sketches before tracing.
Measurements and vision assumptions remain reviewable. It does not recover
hidden dimensions, certify architectural measurements, reconstruct photographic
furniture detail or guarantee a watertight printable building.

Manual tracing, construction and deterministic procedural materials need no
language model or GPU. Automatic interpretation requires the configured global
vision service; the existing optional projection texturing flow requires its
render and image engines. Live QA exercised the manual flow; vision was tested
with a simulated contract response because the global service was unavailable.
Generative projection was inspected through status, not generated in that run.

See [README.md](README.md), [room workflow and limits](docs/ROOM-SKETCH.md), and
[the local surface brief](.impeccable/surfaces/room-sketch.md).
