# sand3d

`import sand3d as s3d`; mm, or `units("m")` first. Makers take a name, return the solid; edits return
it. Edges and faces go by their maker's names (top bottom side rim seam start end cut, tool names,
plate.top).

1. A placement is a frame plus values. `at` sets the values it is given and keeps the others; `on=`
   changes the frame, and x, y, z then run along its axes; `transform` moves the whole thing, as a parent
   would, mirror included.
2. To repeat, pass a list of frames to `on=`: once per frame, in `at`, `hole` and `pocket`.
3. A list of objects is worth its elements, wherever a verb takes objects.
4. What a verb uses up no longer exists. `copy` keeps it; always use what `at` returns.
5. Every solid starts at z = 0, centred in x and y: `box` is the `extrude` of its `rect`; `extrude(both=True)`
   centres one on its plane. A face's frame starts where the origin projects onto it: a vertical face's, at
   its foot. A frame's Z is the face's normal; on a face whose normal is ±Z its X is +X, on any other
   X is Z × normal: on a face looking +X, X is +Y and Y is up. A side, an edge or a corner of a box is
   named by directions: `bbox(ob, "+X")` is the centre of that side of its bounding box, `"+X-Y"` the
   middle of an edge, `"+X-Y+Z"` a corner, with the axes of a face looking the first way named, on a part
   or a group; `at`'s `anchor` takes the same ("-Z", "+Y-Z"; its words top bottom left right front back
   are +Z -Z -X +X -Y +Y).
6. A joint lets a part move on another. Place the part first: `joint` does not move it, it says how it
   moves — on the part its frame `on` was read on (`ob.face`, `bbox`, `local`, an assembly's anchor; it
   stays read on it through `.move`, `.turn`, `.offset`), about or along `axis`: a direction ("+Z", "-X"…)
   in the space the parent lives in, as in `face`, or "normal", `on`'s Z. A pivot given as numbers — a
   source's joint origin, xyz and rpy — is `local(parent, x, y, z, rx, ry, rz)`, in the parent's own space:
   `at(child, on=…)` places the child there, `joint` hangs it there. Positive values slide along the axis,
   or turn by the right hand about it: about +Z, +X turns toward +Y — a door hinged on the -Y edge of a +X
   face opens outward about "-Z". The range contains 0; a joint that follows another takes its range from
   it. Everything is built at rest, every joint at 0; `pose(child, value)` moves the mechanism. A part with
   no joint moves with its group; a jointed part moves by its joint, whatever group it is in; `on` is never
   read on the child itself. Joints may come before or after `group`/`assembly`. `copy` copies a jointed
   part's joints (it hangs on the same parent); `transform` moves the part with its pivot (a mirror image
   moves as the image of the motion); `at` refuses it. Two joints on one child compose in order (a screw: a
   turn, and a slide that follows it with `ratio=pitch / 360`).

```python
# profiles
rect(w, d, r=0, chamfer=0)  # Centred; corners rounded or cut
circle(d)  # Centred
ellipse(w, d)  # Centred
slot(l, d)  # Round-ended slot along X
polygon(n, d)  # Regular polygon, corner on +X
poly(pts, fillet=0, chamfer=0)  # Outline through points, arcs, splines
arc(center=(0, 0), r=1, start=0, end=360, *, through=None, to=None)  # Arc from start to end, anticlockwise if end > start: a path, or a poly run
tangent_arc(to)  # Poly run: arc leaving tangent
spline(pts, closed=False, tangents=None, loop=False)  # Smooth curve; closed: a profile; loop: a ring path
superellipse(w, d, n=2, taper=0)  # Ellipse (n=2) to box (n=4); taper narrows its +Y side, an egg
station(z, w, d, x=0, y=0, n=2, taper=0)  # Loft section at height z; n and taper as superellipse
text(s, size, font=None)  # Centred outline
offset(p, t)  # Grown by t
stroke(path, t)  # A band t wide along an open path, centred on it: a closed outline
wrap(*profiles)  # Convex outline round profiles
mirror(p, flip='x')  # Plus its image, flip negated
# solids, paths
extrude(name, profile, h=None, taper=0, twist=0, both=False, h2=None, on=None, until=None)  # Prism up Z (taper > 0 narrows it upward), or until a plane or solid; on a face, by on
revolve(name, profile, angle=360)  # X radius, Y height, turned about Z
sweep(name, profile, path, scale=1, twist=0, frenet=False)  # Profile along a path
loft(name, *sections, follow=False, loop=False)  # Through placed sections, capped; loop: a ring
body(name, plan, top, bottom, n=2)  # Solid along Y from its views, where they draw it: plan (y, width), side top and bottom (y, z) or numbers; superellipse sections
wire(name, path, d, closed=None, fillet=0)  # Round wire along a path: (x, y, z) points, a spline, an arc, a helix; fillet rounds the corners of points, d / 2 at least
helix(d, pitch, h, left=False)  # Coil path up Z
box(name, w, d, h)  # On Z = 0, centred in X and Y
cylinder(name, d, h)  # Along Z, from Z = 0
tube(name, d, d_in, h)  # Along Z, from Z = 0
cone(name, d, h, d2=0)  # Along Z, from Z = 0
sphere(name, d)  # On Z = 0
wedge(name, w, d, h, top=0)  # Box narrowed on top
torus(name, d, d_wire)  # Ring about Z, on Z = 0
dome(name, d, h)  # Cap on Z = 0
# edits
cut(ob, *tools)  # Subtract tools
join(ob, *others)  # Fuse others in; ob's material wins: group keeps each its own
intersect(ob, tool)  # Keep the common part
split(ob, frame, keep='above')  # By a frame's plane
delete(ob)  # Remove it
copy(ob)  # An independent copy, its joints with it
hole(ob, d, on=(0, 0), depth=None, cbore=None, csk=None)  # On frames or points: through, or depth deep
pocket(ob, profile, depth=None, on=None)  # Profile cut in from a face
merge(*obs)  # No boolean
scale(ob, s)  # About its origin
fillet(ob, r, edges='all')  # Round named edges
chamfer(ob, d, edges='all', d2=None, angle=None)  # Bevel named edges
soften(ob, r, angle=30)  # Round all sharp edges
shell(ob, t, open=None)  # Hollow; open named faces, or the flat faces looking "+Y", "-X +X"
dent(ob, points, r, depth)  # Press hollows at points
# frames, placement
Frame(origin=(0, 0, 0), normal=(0, 0, 1), up=None)  # A place to build on
.offset(d) .turn(rx=0, ry=0, rz=0) .move(x=0, y=0, z=0) .point(x=0, y=0, z=0)
ob.face(which)  # Frame of a named face, or furthest along "+Z"
ob.edge(which)  # Path along the edges named which, in the space ob is in: the longest run (as long: the one reaching furthest -X, then -Y, -Z), closed if it closes
bbox(ob, which)  # Frame on its bounding box: a side "+X", an edge "+X-Y", a corner "+X-Y+Z"
local(ob, x=0, y=0, z=0, rx=0, ry=0, rz=0)  # Frame at (x, y, z) of its own space, turned (rx, ry, rz) degrees
at(ob, x=None, y=None, z=None, rx=None, ry=None, rz=None, on=None, anchor=None)  # Place it: set what is given, keep the rest; anchor: "-Z", "+Y-Z"…
transform(ob, x=0, y=0, z=0, rx=0, ry=0, rz=0, mirror=None)  # Move it by, as a parent would; its placement, not its mesh
grid(nx, ny=1, px=0, py=0, stagger=False, on=None)  # On a grid
ring(n, r, start=0, angle=360, on=None)  # On a circle
along(path, pitch=None, count=None)  # Along a path
snap(targets, points, direction=None)  # On a surface
# parts
gear(name, teeth, module, width, bore=0, angle=20)  # Involute spur gear
knurl(name, d, h, pitch=None, straight=False)  # Knurled grip; pitch 1 mm by default
thread(name, d, length, pitch, left=False)  # Threaded rod
# materials
material(ob, kind, color=None, roughness=None, metallic=None, opacity=None, emission=None)  # Kind: oak walnut pine birch steel brushed_steel chrome aluminium brass copper painted_metal plastic glossy_plastic rubber glass ceramic fabric velvet leather marble concrete light; color "#rrggbb"
# articulation
joint(name, child, on, kind='revolute', axis='normal', range=None, follows=None, ratio=1)  # How child moves on the part on was read on: revolute (range in degrees), continuous (no range), prismatic (range in the unit), free (no axis, no range); follows: a child or a joint name, and this joint's value is ratio × its (degrees per unit, or units per degree), no range; returns child
pose(ob, value, joint=None)  # Move the mechanism: that joint to value (degrees or the unit), an error outside its range; what it carries follows; joint= when child has two
seat(host, part)  # Take out of host the volume part fills, as it sits at rest; part untouched
# assembly
group(name, *children)  # Parent of children
assembly(name, *children, **anchors)  # Parts and anchors
attach(handle, name, *children)  # A detail, under a handle
Handle(root, **anchors)  # Root and anchors
export(root, path)  # root and what hangs on it by its joints, in metres: .glb at the current pose, joints in extras; .urdf a link per moving part, at rest, meshes beside it; .usda the URDF's tree as OpenUSD text, with the materials; .obj and .stl its meshes at the current pose
empty_scene()  # Fresh scene
units(name=None)  # Length unit: "mm" (default), "cm", "m"
fingerprint()  # Digest of the source
```

```python
def bracket(w=80, d=40, t=6, hole_d=6.6, pitch=50):
    plate = s3d.extrude("plate", s3d.rect(w, d, r=4), t)
    s3d.hole(plate, hole_d, on=s3d.grid(2, 1, pitch, 0))
    s3d.fillet(plate, 1, edges="top")
    return plate

def lamp(stem_h=300):
    base = s3d.revolve("base", [(0, 0), (70, 0), (60, 20), (0, 20)])
    stem = s3d.at(s3d.cylinder("stem", 12, stem_h), on=base.face("top"))
    lamp = s3d.assembly("lamp", base, stem, head=s3d.Frame((0, 0, 20 + stem_h)))
    add_shade(lamp)
    return lamp

def add_shade(lamp, d=180):
    shade = s3d.shell(s3d.cone("shade", d, 120, d2=d / 2), 1.5, open="top bottom")
    s3d.attach(lamp, "shade", s3d.at(shade, on=lamp.head))

def rack(n=4, pitch=30, depth=40, t=5):
    base = s3d.extrude("base", s3d.rect(n * pitch, depth, r=3), t)
    pegs = s3d.at(s3d.cylinder("peg", 6, 20), on=s3d.grid(n, 1, pitch), z=t)         # a copy per frame
    hook = s3d.at(s3d.extrude("hook", s3d.rect(4, 12), 12), x=n * pitch / 2 - 4, z=t)
    image = s3d.transform(s3d.copy(hook), mirror="x")                                # its image across x = 0
    s3d.join(base, pegs, hook, image)                                                # a list is its elements
    s3d.hole(base, 4, on=s3d.grid(2, 1, (n - 1) * pitch).move(y=depth / 2 - 6))
    s3d.chamfer(base, 0.6, edges="peg")
    return base

def gate(w=750, h=1200, post=100):
    l_post = s3d.at(s3d.box("post", post, post, h + 300), x=-w - post / 2)
    r_post = s3d.transform(s3d.copy(l_post), mirror="x")
    board = s3d.at(s3d.box("board", w, 40, h), x=-w / 2, z=150)                    # from the post to x = 0
    handle = s3d.at(s3d.cylinder("handle", 20, 60), on=s3d.bbox(board, "+Y").move(x=60 - w / 2))  # near x = 0
    leaf = s3d.group("leaf", board, handle)                                        # the handle moves with it
    s3d.joint("hinge", leaf, s3d.bbox(l_post, "+X+Y"), axis="+Z", range=(0, 90))   # the post's edge it opens toward: +X turns toward +Y
    twin = s3d.transform(s3d.copy(leaf), mirror="x")                               # its image, hinged at x = +w: opens toward +Y too
    gate = s3d.assembly("gate", l_post, r_post, leaf, twin)
    s3d.pose(leaf, 60)
    return gate
```
