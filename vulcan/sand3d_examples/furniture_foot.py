import sand3d as s3d

# Furniture foot / door stop: a tapered round foot with a countersunk screw hole through its axis and a
# recess underneath for a felt or rubber pad. Prints standing on its wide top (the face that meets the
# furniture), so the countersink and the taper need no supports.
top_d = 40          # mm, diameter against the furniture
bottom_d = 32       # mm, diameter on the floor
h = 20              # mm, height
screw_d = 4.5       # mm, screw clearance
head_d = 9          # mm, countersink diameter at the surface
pad_d = 26          # mm, felt pad recess on the floor side
pad_depth = 1.5     # mm
edge = 1.5          # mm, rounding of the floor edge

foot = s3d.cone("foot", top_d, h, d2=bottom_d)     # wide face on z = 0 (on the bed), floor face on top
s3d.fillet(foot, edge, edges="top")
s3d.hole(foot, screw_d, on=foot.face("top"), csk=head_d)  # the screw head sits in the floor face
s3d.pocket(foot, s3d.circle(pad_d), depth=pad_depth, on=foot.face("top"))

model = foot
