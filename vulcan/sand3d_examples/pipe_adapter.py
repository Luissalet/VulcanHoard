import sand3d as s3d

# Reducer between two round tubes: a socket that slides OVER tube A, a cone, and a spigot that slides
# INTO tube B. Revolved from its half section, so it prints standing on the wide socket with no supports
# (the cone stays under 45 degrees). Sizes are the tubes' measured outside (A) and inside (B) diameters.
tube_a_od = 32      # mm, outside diameter of tube A (the socket's bore = this + clearance)
tube_b_id = 20      # mm, inside diameter of tube B (the spigot's outside = this - clearance)
socket_l = 20       # mm, socket depth
spigot_l = 20       # mm, spigot length
wall = 2.4          # mm
clearance = 0.3     # mm, on the diameter, for a snug slip fit
stop = 2            # mm, inner ledge the tube A rests against

r_sock_in = (tube_a_od + clearance) / 2
r_sock_out = r_sock_in + wall
r_spig_out = (tube_b_id - clearance) / 2
r_spig_in = r_spig_out - wall
cone_h = max(r_sock_out - r_spig_out, 5)          # 45 degrees or less

# half section in (radius, height), from the bottom of the socket up to the top of the spigot
z1 = socket_l
z2 = z1 + stop
z3 = z2 + cone_h
z4 = z3 + spigot_l
section = s3d.poly([(r_sock_in, 0), (r_sock_out, 0), (r_sock_out, z2), (r_spig_out, z3), (r_spig_out, z4),
                    (r_spig_in, z4), (r_spig_in, z3), (r_spig_in, z2), (r_spig_in, z1), (r_sock_in, z1)])
model = s3d.revolve("adapter", section)
