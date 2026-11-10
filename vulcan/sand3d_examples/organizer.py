import sand3d as s3d

# Desk organizer: an open tray split into compartments by inner walls (cols x rows), rounded outside.
# The outside is exactly w x d x h; every compartment is the same size.
w, d, h = 150, 80, 45   # mm, outside
cols, rows = 3, 1       # compartments along X and Y
wall = 2                # mm, outer and inner walls
floor = 2               # mm, bottom
r = 5                   # mm, outside corner radius
inner_r = 2             # mm, corner radius inside each compartment

cell_w = (w - (cols + 1) * wall) / cols
cell_d = (d - (rows + 1) * wall) / rows
tray = s3d.extrude("tray", s3d.rect(w, d, r=r), h)
pocket = s3d.extrude("pocket", s3d.rect(cell_w, cell_d, r=inner_r), h)
centres = s3d.grid(cols, rows, cell_w + wall, cell_d + wall)   # a frame per compartment, centred on the tray
s3d.cut(tray, s3d.at(pocket, on=centres, z=floor))               # one copy of the pocket per frame

model = tray
