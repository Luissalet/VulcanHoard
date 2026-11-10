import sand3d as s3d

# Spur gear with a hub, a D-shaped bore for a motor shaft and a set-screw hole.
# Outside diameter of an involute spur gear = module x (teeth + 2).
teeth = 24          # number of teeth
module = 1.5        # mm, tooth size (pitch diameter = module x teeth = 36 mm)
width = 8           # mm, face width of the teeth
hub_d = 16          # mm, hub diameter
hub_h = 6           # mm, hub above the teeth
bore = 5            # mm, shaft diameter
flat = 0.5          # mm, depth of the D flat on the shaft
screw_d = 3         # mm, set-screw hole through the hub

gear = s3d.gear("gear", teeth, module, width)
s3d.join(gear, s3d.at(s3d.cylinder("hub", hub_d, hub_h), z=width))
shaft = s3d.extrude("shaft", s3d.circle(bore + 0.2) - s3d.at(s3d.rect(bore, bore), y=bore - flat), width + hub_h + 2)
s3d.cut(gear, s3d.at(shaft, z=-1))
s3d.cut(gear, s3d.at(s3d.cylinder("set_screw", screw_d, hub_d), y=0, z=width + hub_h / 2, rx=-90))
s3d.chamfer(gear, 0.4, edges="top")

model = gear
