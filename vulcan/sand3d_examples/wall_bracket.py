import sand3d as s3d

# L bracket for a wall shelf: a leg screwed to the wall, an arm under the shelf and a diagonal brace.
# Built as the L outline (a bent band) extruded by the bracket's width, so it prints lying on its side
# and the layers run along the legs (the corner does not split along a layer line).
wall_h = 80      # mm, leg on the wall (Y)
arm_l = 70       # mm, arm under the shelf (X)
width = 25       # mm, bracket width (Z, the print height)
t = 5            # mm, thickness of both legs and the brace
screw_d = 4.5    # mm, clearance holes for the screws
brace = 0.6      # where the brace meets each leg, as a fraction of its length

# the L drawn on its centre line, t / 2 inside the outer corner, so the outside is exactly arm_l x wall_h
legs = s3d.stroke([(t / 2, wall_h), (t / 2, t / 2), (arm_l, t / 2)], t)
diagonal = s3d.stroke([(t / 2, wall_h * brace), (arm_l * brace, t / 2)], t)
bracket = s3d.extrude("bracket", legs, width)
s3d.join(bracket, s3d.extrude("brace", diagonal, width))

# screw holes: two through the wall leg (along X), one through the arm (along Y)
for y in (wall_h * 0.35, wall_h * 0.85):
    s3d.cut(bracket, s3d.at(s3d.cylinder("screw", screw_d, 3 * t), x=-t, y=y, z=width / 2, ry=90))
s3d.cut(bracket, s3d.at(s3d.cylinder("screw", screw_d, 3 * t), x=arm_l * 0.8, y=-t, z=width / 2, rx=-90))

model = bracket
