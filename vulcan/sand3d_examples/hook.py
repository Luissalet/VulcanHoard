import sand3d as s3d

# Wall hook (for headphones, bags, cables): a back plate screwed to the wall and a J arm with a rounded
# cradle. The side outline is a bent band extruded by the width, so it prints lying on its side.
plate_h = 70        # mm, back plate on the wall (Y)
reach = 45          # mm, from the wall to the front of the cradle (X)
cradle_d = 30       # mm, inside diameter of the cradle (what hangs on it: a headband about 30 mm wide)
lift = 12           # mm, how high the tip rises above the cradle bottom
width = 22          # mm, hook width (Z, print height)
t = 5               # mm, thickness of the band
screw_d = 4.5       # mm, screw clearance

r = cradle_d / 2 + t / 2                          # radius of the band's centre line round the cradle
cx = reach - t / 2 - r                            # cradle centre (X); the band ends at the front at x = reach
cy = r + t / 2                                    # the cradle bottom touches y = 0
path = [(t / 2, plate_h), (t / 2, cy), (cx - r, cy),  # down the wall, then out to where the cradle starts
        s3d.arc((cx, cy), r, 180, 360),            # round the bottom of the cradle
        (cx + r, cy + lift)]                       # and up to the tip
hook = s3d.extrude("hook", s3d.stroke(path, t), width)
s3d.fillet(hook, 1, edges="top")

for y in (plate_h * 0.45, plate_h * 0.85):
    s3d.cut(hook, s3d.at(s3d.cylinder("screw", screw_d, 3 * t), x=-t, y=y, z=width / 2, ry=90))

model = hook
