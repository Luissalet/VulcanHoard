import sand3d as s3d

# Box with a slip-in lid. Both print open side up, side by side on the bed: the box on the left,
# the lid (plate down, lip up) on the right. The box's outside is exactly w x d x h.
w, d, h = 60, 40, 30   # mm, outside of the box
wall = 2               # mm, side walls
floor = 2              # mm, bottom of the box
r = 4                  # mm, corner radius of the outside
lid_t = 2              # mm, lid plate
lip_h = 5              # mm, lip that drops inside the box
clearance = 0.3        # mm, gap between the lip and the inside of the box
gap = 10               # mm, between the two parts on the bed

box = s3d.extrude("box", s3d.rect(w, d, r=r), h)
s3d.cut(box, s3d.at(s3d.extrude("cavity", s3d.rect(w - 2 * wall, d - 2 * wall, r=max(r - wall, 0.5)), h), z=floor))

lid = s3d.extrude("lid", s3d.rect(w, d, r=r), lid_t)
lip_w, lip_d = w - 2 * (wall + clearance), d - 2 * (wall + clearance)
lip_outline = s3d.rect(lip_w, lip_d, r=max(r - wall - clearance, 0.5))
lip = s3d.extrude("lip", lip_outline - s3d.offset(lip_outline, -1.6), lip_h)  # a 1.6 mm ring
s3d.join(lid, s3d.at(lip, z=lid_t))

s3d.at(box, x=-(w + gap) / 2)
s3d.at(lid, x=(w + gap) / 2)
model = s3d.group("box_and_lid", box, lid)
