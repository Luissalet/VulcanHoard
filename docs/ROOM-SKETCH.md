# Rooms from sketches

The **Habitación** workspace in Biblioteca 3D turns a sketch or a photograph of a
sketch into a measured floor plan, wall and furniture volumes, and a textured room
in the library. Open the library module explicitly, then choose **Habitación**.
Existing modelling, projection texturing and library workflows remain available.

Load a PNG, JPEG or WebP. Set the width represented by the **entire image**, in
metres, including its margins. A frontal or rectified photograph gives a more
useful manual tracing scale. Start a trace, click the perimeter corners in order,
then close the plan. Coordinates can also be edited using the keyboard or loaded
from a plan JSON. The included concave example is labelled as an example.

Alternatively, **Interpretar boceto** sends the image and known measurements to
the configured global vision model. It returns a plan and explicit assumptions;
the plan remains editable before construction. The service must be running with
image support. An unavailable service, unsupported vision, unreadable sketch or
invalid model response produces an error, rather than a fabricated rectangular
room. Manual tracing and construction need no language model or GPU.

Review the perimeter, height and wall thickness; add doors and windows by wall
number, distance from that wall's first vertex, width, base height and opening
height. The plan labels the walls, lengths, apertures and furniture. A door has
base zero. Furniture is represented by named boxes with centre, dimensions and
rotation. Invalid or overlapping apertures and furniture outside the perimeter
are rejected before scene files are written.

**Construir habitación** writes a new GLB with an open roof for inspection, real
wall apertures, a concave floor and separate furniture volumes. Floors, walls and
furniture have independently selectable plaster, oak, tile, concrete or brick
materials. These are deterministic procedural texture maps with planar UV
projection at physical scales, embedded in the GLB. The orbit viewer preserves
scene materials, starts above the room and shows its measurements. Library
measurements remain millimetres; recipe coordinates and GLB units are metres,
with Y up in the GLB and Z up in the workshop.

Construction retains `plan.json`, a runnable `room.py`, the model's `.room.json`
metadata and an optional normalized `sketch.png` beside `room.glb` under
`data/rooms/<uuid>/`. The metadata includes sketch calibration and the existing
glTF validator's report. Download links and `#/habitacion/<model-id>` reopen the
saved recipe. The program rebuilds the same scene using Vulcan's geometry code;
run it in the installed Vulcan environment. Editing a saved recipe and building
again creates a new library ID, preserving the original. The current draft also
survives page changes within the tab; large sketch storage failures are visible.

The existing **Texturizar variante** projection workflow can paint another variant
from a surface description. Its render and image engines must be available. It
retains its own jobs and status tools, and leaves the constructed room intact.
Procedural materials do not require those engines.

## API and MCP

Three native tools are available through the library API and the studio's cold
MCP catalogue. Discovery starts no module or model.

| MCP tool | Library REST endpoint | Purpose |
| --- | --- | --- |
| `room_plan` | `POST /api/rooms/plan` | Interpret an image using the global vision model. Accepts exactly one of `image_path` (absolute path) or `image_base64`, plus `width_m` and optional `description`. |
| `room_build` | `POST /api/rooms/build` | Build a reviewed `plan`, optionally retaining `image_base64` and `sketch_width_m`. Returns the library ID, recipe, report, validation and `downloads`. |
| `room_get` | `GET /api/rooms/{id}` | Read the saved recipe and download URLs. |

Files are served from `GET /api/rooms/{id}/files/{glb|plan|code|sketch}`. Studio MCP
download URLs are correctly prefixed with `/library/`.

A minimal build needs no sketch image or model call:

```json
{
  "plan": {
    "name": "Study",
    "outline": [[0, 0], [5, 0], [5, 4], [3, 4], [3, 3], [0, 3]],
    "height": 2.7,
    "thickness": 0.15,
    "floor_material": "oak",
    "wall_material": "plaster",
    "openings": [{"wall": 0, "offset": 0.6, "width": 0.9, "bottom": 0, "height": 2.1}],
    "blocks": [{"name": "Desk", "center": [2, 2], "size": [1.2, 0.6, 0.75], "angle": 0, "material": "oak"}],
    "assumptions": []
  }
}
```

## Scope and verification

This is one room with a simple, possibly concave perimeter. It supports vertical
walls, a flat floor, rectangular apertures and furniture boxes. It does not infer
hidden dimensions, certify architectural measurements, create detailed furniture,
model curved or sloped walls, or divide an entire multi-room building. Photograph
perspective and uncertain features remain reviewable assumptions from vision;
manual tracing does not correct perspective automatically. Wall solids can meet
or overlap at corners; this scene is intended for inspection and texturing, and is
not advertised as a single watertight printable building.

`tests/test_room_sketch.py` checks concave floor area and volume, actual empty door
and window space, embedded material maps and UVs, metre dimensions and Y-up GLB,
input validation, recipe/variant preservation, REST/native parity, cold schemas
and the global vision contract. Live Chromium and stdio MCP evidence for task2790
is in `Research/vulcan-boceto2790/` outside the repository. The full manual workflow
was exercised live. Automatic interpretation was contract-tested with a simulated
vision response; the configured global service was unavailable during live QA.
Generative image projection was inspected through its status tool, not generated
during that QA run.
