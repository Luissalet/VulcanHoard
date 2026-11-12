# Rooms from sketches

The **Habitación** workspace in Biblioteca 3D turns a sketch or a photograph of a
sketch into a measured floor plan, wall and furniture volumes, and a textured room
in the library. Open the library module explicitly, then choose **Habitación**.
Existing modelling, projection texturing and library workflows remain available.

Load a PNG, JPEG or WebP. Set the width represented by the **entire image**, in
metres, including its margins. For a photographed planar sketch, open **Corregir perspectiva de la foto**,
mark the paper corners in top-left, top-right, bottom-right, bottom-left order,
and enter its true width/height ratio. The projective transform produces a
rectified image; the width calibration now describes the cropped paper. You can
undo the rectification before tracing. The rectified sketch is retained on build. Start a trace, click the perimeter corners in order,
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
base zero. Choose bed, table, chair, sofa, shelf or wardrobe in the furniture palette and
click its label on the sketch. Each type is a named assembly with editable centre,
dimensions and rotation. Free boxes remain available. Invalid or overlapping apertures and furniture outside the perimeter
are rejected before scene files are written.

Trace interior partitions between existing walls, or enter their start/end
coordinates. Near-wall clicks snap to the wall. Add doorways or open passages to
the partitions to connect the rooms. Place one optional name label inside each
room and choose its floor finish. The report includes each room's area and the
adjacent room indexes for every interior doorway. Furniture crossing partitions,
floating partition ends, overlapping walls and invalid apertures are rejected.

**Construir habitación** writes a new GLB with a concave floor, ceiling, real
wall apertures and separate furniture parts. Doors have jambs, lintel and a leaf
rotated around its hinge; windows have sill, frame, mullion and transparent
glass. An explicit passage has no frame or leaf. The old base-height heuristic
still selects a door or window when kind is auto.

Floors, ceilings, interior walls and furniture select plaster, sage paint, oak,
tile, concrete or brick. Individual wall finishes override the interior default.
An optional exterior skin uses an independent finish, so brick outside does not
replace plaster inside. Fabric, linen, metal and glass are used for furniture
and window components. These are deterministic procedural texture maps with planar UV
projection at physical scales, embedded in the GLB. The orbit viewer preserves scene materials and starts above the room. Daylight
enters through windows, with a lamp per room and soft shadow maps. Lighting is a
viewer setup derived from the saved report, not an exported lighting extension.
The inspection section hides the ceiling and camera-facing perimeter walls
without altering the GLB. Uncheck it to show the full scene. The static room
renders again on camera, viewport or section changes. Library
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

Four native tools are available through the library API and the studio's cold
MCP catalogue. Discovery starts no module or model.

| MCP tool | Library REST endpoint | Purpose |
| --- | --- | --- |
| `room_plan` | `POST /api/rooms/plan` | Interpret an image using the global vision model. Accepts exactly one of `image_path` (absolute path) or `image_base64`, plus `width_m` and optional `description`. |
| `room_build` | `POST /api/rooms/build` | Build a reviewed `plan`, optionally retaining `image_base64` and `sketch_width_m`. Returns the library ID, recipe, report, validation and `downloads`. |
| `room_get` | `GET /api/rooms/{id}` | Read the saved recipe and download URLs. |
| `room_rectify` | `POST /api/rooms/rectify` | Rectify `image_path` or `image_base64` with four normalized `corners` and the true paper `aspect` (width/height). Returns PNG data URL and pixel dimensions. |

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

This is a parametric floor plan with vertical walls, a flat floor/ceiling,
rectangular apertures and six recognisable furniture assemblies. It does not
infer hidden dimensions, certify architectural measurements, reconstruct
photographic furniture detail or model curved/sloped walls. Perspective
rectification assumes a planar sheet, an accurate corner selection and its true
aspect; it is not a lens distortion correction. Adjacent wall solids can meet or
overlap at corners; this is an editable inspection scene rather than a single
watertight printable building.

`tests/test_room_sketch.py` covers floor area, apertures, interior/exterior
finishes, ceiling, glass alpha, real door leaves, furniture envelopes, connected
rooms, invalid partitions, projective rectification, REST/native parity, cold
schemas and the global vision contract. Live browser and stdio MCP evidence for
the second pass is in `Research/vulcan-habitacion2921/` outside the repository.
The original vision contract remains tested with simulated responses; this pass
does not claim real global vision inference or generative projection.
