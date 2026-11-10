import sand3d as s3d

# Print-in-place hinge: two flat leaves on the bed, three knuckles along X. The outer knuckles belong to
# leaf A and carry the pin; the middle knuckle belongs to leaf B and turns on the pin with a clearance.
length = 40        # mm, along the hinge axis (X)
leaf = 25          # mm, each leaf from the axis (Y)
t = 3              # mm, leaf thickness
knuckle_d = 8      # mm, knuckle diameter
pin_d = 3.5        # mm, pin diameter
clearance = 0.4    # mm, gap round the moving parts
knuckles = 3      # number of knuckles (odd, so leaf A has both ends)

seg = (length - (knuckles - 1) * clearance) / knuckles
axis_z = knuckle_d / 2


def along_x(name, d, l, x0):
    """A cylinder of diameter d along +X, from x0, on the hinge axis."""
    return s3d.at(s3d.cylinder(name, d, l), x=x0, y=0, z=axis_z, ry=90)


def segment_x(i):
    return -length / 2 + i * (seg + clearance)


leaf_a = s3d.at(s3d.box("leaf_a", length, leaf, t), y=-leaf / 2)
leaf_b = s3d.at(s3d.box("leaf_b", length, leaf, t), y=leaf / 2)
for i in range(knuckles):
    x0 = segment_x(i)
    if i % 2 == 0:  # leaf A: knuckle, and room for leaf B's knuckles cut out of it
        s3d.join(leaf_a, along_x("knuckle", knuckle_d, seg, x0))
        s3d.cut(leaf_b, along_x("room", knuckle_d + 2 * clearance, seg + 2 * clearance, x0 - clearance))
    else:
        s3d.join(leaf_b, along_x("knuckle", knuckle_d, seg, x0))
        s3d.cut(leaf_a, along_x("room", knuckle_d + 2 * clearance, seg + 2 * clearance, x0 - clearance))
        s3d.cut(leaf_b, along_x("bore", pin_d + 2 * clearance, seg + 2, x0 - 1))
s3d.join(leaf_a, along_x("pin", pin_d, length - 2, -length / 2 + 1))

model = s3d.group("hinge", leaf_a, leaf_b)
