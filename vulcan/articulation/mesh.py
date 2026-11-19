"""Source-space face selection, topology and attribute transfer inside the mesh worker."""
from collections import defaultdict
from heapq import heappop, heappush
from itertools import groupby

import bpy
import numpy as np
from mathutils import Vector
from mathutils.geometry import tessellate_polygon

from seams import connect_ends, cycles


def read_array(collection, property_name, width, dtype=np.float32):
    data = np.empty(len(collection) * width, dtype=dtype)
    collection.foreach_get(property_name, data)
    return data.reshape(-1, width) if width > 1 else data


def edge_keys(faces, stride):
    ends = np.roll(faces, -1, axis=1)
    return np.minimum(faces, ends).astype(np.int64) * stride + np.maximum(faces, ends)


def ranges(indices):
    """Compact, inclusive runs of sorted source or output face IDs."""
    if not len(indices):
        return []
    split = np.flatnonzero(np.diff(indices) != 1) + 1
    return [[int(run[0]), int(run[-1])] for run in np.split(indices, split)]


class Source:
    def __init__(self, obj):
        mesh = self.mesh = obj.data
        lengths = read_array(mesh.polygons, "loop_total", 1, np.int32)
        if not len(lengths) or np.any(lengths != 3):
            raise ValueError("A triangulated source mesh is required.")
        self.vertices = read_array(mesh.vertices, "co", 3)
        self.faces = read_array(mesh.loops, "vertex_index", 1, np.int32).reshape(-1, 3)
        self.centres = self.vertices[self.faces].mean(axis=1)
        self.material_indices = read_array(mesh.polygons, "material_index", 1, np.int32)
        self.smooth = read_array(mesh.polygons, "use_smooth", 1, np.bool_)
        self.stride = len(self.vertices)
        self.keys = edge_keys(self.faces, self.stride)
        edges, counts = np.unique(self.keys, return_counts=True)
        self.preexisting = edges[counts == 1]
        self.nonmanifold = int(np.count_nonzero(counts > 2))
        self.colours = {
            colour.name: (colour.domain, read_array(colour.data, "color", 4))
            for colour in mesh.color_attributes if colour.domain in {"CORNER", "POINT"}
        }
        self.uvs = {uv.name: read_array(uv.uv, "vector", 2) for uv in mesh.uv_layers}
        self.active_colour = mesh.color_attributes.active_color_name

    def select(self, criteria, available):
        mask = available.copy()
        requested = criteria.get("face_ranges")
        if requested:
            if requested[-1][1] >= len(mask):
                raise ValueError("A face range exceeds the source mesh. / Un tramo de caras rebasa la malla de origen.")
            explicit = np.zeros_like(mask)
            for start, end in requested:
                explicit[start:end + 1] = True
            mask &= explicit
        if criteria["lower"] is not None:
            mask &= np.all(self.centres >= criteria["lower"], axis=1)
        if criteria["upper"] is not None:
            mask &= np.all(self.centres <= criteria["upper"], axis=1)
        for plane in criteria["planes"]:
            mask &= (self.centres - plane["point"]) @ np.asarray(plane["normal"]) >= 0
        if colour := criteria["colour"]:
            if not self.colours:
                raise ValueError("Colour selection requires source vertex or corner colours.")
            colour_name = self.active_colour if self.active_colour in self.colours else next(iter(self.colours))
            domain, rgba = self.colours[colour_name]
            selected = np.flatnonzero(mask)
            rgb = (rgba[self.faces[selected]] if domain == "POINT" else rgba.reshape(-1, 3, 4)[selected])
            mask[selected] = np.linalg.norm(rgb.mean(axis=1)[:, :3] - colour["rgb"], axis=1) <= colour["tolerance"]
        selected = np.flatnonzero(mask)
        if criteria["seed"] and len(selected):
            selected = self.grow(selected, criteria["seed"])
        return selected

    def grow(self, selected, seed):
        centres = self.centres[selected]
        first = int(np.argmin(np.linalg.norm(centres - seed["point"], axis=1)))
        keys = self.keys[selected].ravel()
        face_order = np.argsort(keys)
        neighbours = defaultdict(list)
        for _, occurrences in groupby(face_order, key=keys.__getitem__):
            adjacent = [int(index) // 3 for index in occurrences]
            if len(adjacent) == 2:
                a, b = adjacent
                step = float(np.linalg.norm(centres[a] - centres[b]))
                neighbours[a].append((b, step))
                neighbours[b].append((a, step))
        distances = {first: 0.0}
        queue = [(0.0, first)]
        while queue:
            distance, face = heappop(queue)
            if distance > distances[face]:
                continue
            for neighbour, step in neighbours[face]:
                total = distance + step
                if total <= seed["distance"] and total < distances.get(neighbour, float("inf")):
                    distances[neighbour] = total
                    heappush(queue, (total, neighbour))
        return selected[sorted(distances)]

    def piece(self, name, selected, repair_original, opposite_edges=None):
        source_faces = self.faces[selected]
        edge_ids, first, counts = np.unique(self.keys[selected], return_index=True, return_counts=True)
        eligible = counts == 1
        if not repair_original:
            eligible &= ~np.isin(edge_ids, self.preexisting)
        repairs = []
        occupied = np.zeros(len(edge_ids), dtype=bool)
        for opposite, other_keys in (opposite_edges or {}).items():
            cut = (counts == 1) & np.isin(edge_ids, other_keys)
            if not cut.any():
                raise ValueError(f"No open cut edges between {name!r} and {opposite!r}.")
            if np.any(occupied & cut):
                raise ValueError(f"Repair boundaries overlap at {name!r}.")
            edges = np.column_stack(np.divmod(edge_ids[cut], self.stride))
            repairs.append((opposite, cut, edges))
            occupied |= cut
        eligible &= ~occupied
        corner_indices = first[eligible]
        face_ids, edges = np.divmod(corner_indices, 3)
        # Reverse the source-facing edge so the cap has the opposite winding on the seam.
        closure = list(zip(source_faces[face_ids, (edges + 1) % 3], source_faces[face_ids, edges]))
        caps = cap_loops(closure, self.vertices)
        caps, inserted = separate_cap_diagonals(caps, edge_ids, self.stride)
        cap_batches = [caps]
        output_start = len(selected) + len(caps)
        repair_facts = {}
        for opposite, cut, seam_edges in repairs:
            rings, supplemental = self.repair_seam(
                edge_ids, counts, first, source_faces, cut, seam_edges, occupied | eligible
            )
            occupied[supplemental] = True
            start = output_start
            for ring in rings:
                patch, new_vertices = fill_ring(ring, self.vertices, edge_ids)
                patch[patch >= self.stride] += len(inserted)
                inserted.extend(new_vertices)
                cap_batches.append(patch)
                output_start += len(patch)
            repair_facts[opposite] = (edge_ids[cut], edge_ids[supplemental],
                                      preexisting_counts(counts[supplemental]), [start, output_start - 1])
        caps = np.concatenate(cap_batches)
        joined = np.concatenate((source_faces, caps))
        used, compact = np.unique(joined, return_inverse=True)
        vertices = with_interpolated(self.vertices, inserted)
        mesh = make_mesh(name, vertices[used], compact.reshape(-1, 3))
        for material in self.mesh.materials:
            mesh.materials.append(material)
        cap_count = len(caps)
        nearest = np.argmin(np.linalg.norm(self.centres[selected].mean(axis=0) - self.centres[selected], axis=1))
        cap_material = np.full(cap_count, self.material_indices[selected[nearest]], dtype=np.int32)
        mesh.polygons.foreach_set("material_index", np.concatenate((self.material_indices[selected], cap_material)))
        mesh.polygons.foreach_set("use_smooth", np.concatenate((self.smooth[selected], np.zeros(cap_count, dtype=np.bool_))))
        corner_ids = (selected[:, None] * 3 + np.arange(3)).ravel()
        for label, (domain, rgba) in self.colours.items():
            layer = mesh.color_attributes.new(name=label, type="FLOAT_COLOR", domain=domain)
            if domain == "POINT":
                colours = with_interpolated(rgba, inserted)[used]
            else:
                average = rgba[corner_ids].mean(axis=0)
                colours = np.concatenate((rgba[corner_ids], np.tile(average, (cap_count * 3, 1))))
            layer.data.foreach_set("color", colours.ravel())
        if self.active_colour in self.colours:
            mesh.color_attributes.active_color_name = self.active_colour
        for label, uv in self.uvs.items():
            layer = mesh.uv_layers.new(name=label)
            values = np.concatenate((uv[corner_ids], np.zeros((cap_count * 3, 2), dtype=np.float32)))
            layer.uv.foreach_set("vector", values.ravel())
        stride = len(vertices)
        keys, uses = np.unique(edge_keys(joined, stride), return_counts=True)
        boundary = keys[uses == 1]
        start, end = np.divmod(boundary, stride)
        original_edges = (end < self.stride) & np.isin(start * self.stride + end, self.preexisting)
        open_cut = np.count_nonzero(~original_edges)
        separation = {}
        for opposite, (cut, supplemental, use_counts, output_range) in repair_facts.items():
            vertices_from, vertices_to = np.divmod(cut, self.stride)
            seam_keys = vertices_from * stride + vertices_to
            final_counts = uses[np.searchsorted(keys, seam_keys)]
            if np.any(final_counts != 2):
                failures = preexisting_counts(final_counts[final_counts != 2])
                raise ValueError(f"The repair of {name!r} against {opposite!r} has unexpected edge incidence: {failures}.")
            separation[opposite] = {
                "open_before": len(cut), "open_after": int(np.count_nonzero(final_counts == 1)),
                "cut_source_edges": np.column_stack((vertices_from, vertices_to)).tolist(),
                "route_source_edges": np.column_stack(np.divmod(supplemental, self.stride)).tolist(),
                "route_prior_uses": use_counts,
                "output_face_range": output_range,
            }
        return mesh, {"lineage": "source_faces", "source_faces": len(selected), "cap_faces": cap_count,
                      "boundary_edges": len(boundary), "open_cut_edges": int(open_cut),
                      "nonmanifold_edges": int(np.count_nonzero(uses > 2))}, separation

    def repair_seam(self, edge_ids, counts, first, faces, cut, seam_edges, occupied):
        # Restrict rerouting to the neighbourhood of this separation. All original
        # triangles, including those along the routes, remain in source order.
        points = self.vertices[seam_edges.ravel()]
        margin = max(float(np.max(np.ptp(points, axis=0))) * 0.10, 1e-6)
        local = np.all((self.vertices >= points.min(axis=0) - margin)
                       & (self.vertices <= points.max(axis=0) + margin), axis=1)
        a, b = np.divmod(edge_ids, self.stride)
        allowed = ~occupied & local[a] & local[b]
        candidate_edges = np.column_stack((a[allowed], b[allowed]))
        routes = connect_ends(seam_edges, zip(map(tuple, candidate_edges), counts[allowed]), self.vertices)
        route_ids = np.asarray([a * self.stride + b for a, b in routes], dtype=np.int64)
        supplemental = np.searchsorted(edge_ids, route_ids)
        face_indices, sides = np.divmod(first[cut], 3)
        forward = set(zip(faces[face_indices, sides], faces[face_indices, (sides + 1) % 3]))
        backward = {(b, a) for a, b in forward}
        rings = []
        for ring in cycles([*map(tuple, seam_edges), *routes]):
            directed = set(zip(ring, ring[1:] + ring[:1]))
            if len(directed & forward) > len(directed & backward):
                ring.reverse()
            rings.append(ring)
        return rings, supplemental


def preexisting_counts(uses):
    return {str(int(count)): int(total) for count, total in zip(*np.unique(uses, return_counts=True))}


def fill_ring(ring, points, source_keys):
    stride = len(points)
    following = ring[1:] + ring[:1]
    filled = tessellate_polygon([[Vector(points[i]) for i in ring]])
    if len(filled) == len(ring) - 2:
        triangles = np.asarray([tuple(ring[i] for i in tri) for tri in filled], dtype=np.int32)
        keys, counts = np.unique(edge_keys(triangles, stride), return_counts=True)
        expected = sorted(min(a, b) * stride + max(a, b) for a, b in zip(ring, following))
        if np.array_equal(keys[counts == 1], expected):
            return separate_cap_diagonals(triangles, source_keys, stride)
    return np.asarray([(a, b, stride) for a, b in zip(ring, following)], dtype=np.int32), [tuple(ring)]


def make_mesh(name, vertices, faces):
    mesh = bpy.data.meshes.new(name)
    mesh.vertices.add(len(vertices))
    mesh.vertices.foreach_set("co", np.asarray(vertices, dtype=np.float32).ravel())
    mesh.loops.add(faces.size)
    mesh.loops.foreach_set("vertex_index", np.asarray(faces, dtype=np.int32).ravel())
    mesh.polygons.add(len(faces))
    mesh.polygons.foreach_set("loop_start", np.arange(0, faces.size, 3, dtype=np.int32))
    mesh.update(calc_edges=True)
    return mesh


def cap_loops(edges, points):
    outgoing = defaultdict(list)
    incoming = defaultdict(int)
    for start, end in edges:
        outgoing[start].append(end)
        incoming[end] += 1
    consumed = set()
    triangles = []
    for start in outgoing:
        loop = []
        cursor = start
        while cursor not in consumed and len(outgoing.get(cursor, ())) == incoming[cursor] == 1:
            consumed.add(cursor)
            loop.append(cursor)
            cursor = outgoing[cursor][0]
        if cursor != start or len(loop) < 3:
            continue
        outline = [Vector(points[index]) for index in loop]
        triangles.extend(tuple(loop[index] for index in triangle) for triangle in tessellate_polygon([outline]))
    return np.asarray(triangles, dtype=np.int32).reshape(-1, 3)


def separate_cap_diagonals(caps, source_keys, stride):
    # A cap diagonal can also belong to the retained skin. Give each such diagonal
    # a private midpoint so adding the cap cannot put four faces on the same edge.
    keys = edge_keys(caps, stride)
    unique, uses = np.unique(keys, return_counts=True)
    diagonals = unique[uses > 1]
    spots = np.searchsorted(source_keys, diagonals)
    inside = spots < len(source_keys)
    shared = diagonals[inside][source_keys[spots[inside]] == diagonals[inside]]
    if not len(shared):
        return caps, []
    sources = [tuple(map(int, divmod(key, stride))) for key in shared]
    midpoints = {int(key): stride + index for index, key in enumerate(shared)}
    triangles = []
    for tri, tri_keys in zip(caps.tolist(), keys.tolist()):
        outline = []
        for vertex, key in zip(tri, tri_keys):
            outline.append(vertex)
            if key in midpoints:
                outline.append(midpoints[key])
        if len(outline) == 3:
            triangles.append(tuple(tri))
            continue
        centre = stride + len(sources)
        sources.append(tuple(tri))
        triangles.extend((a, b, centre) for a, b in zip(outline, outline[1:] + outline[:1]))
    return np.asarray(triangles, dtype=np.int32), sources


def with_interpolated(values, inserted):
    if not inserted:
        return values
    interpolated = np.asarray([values[list(indices)].mean(axis=0) for indices in inserted])
    return np.concatenate((values, interpolated))


def panel_mesh(panel):
    outline = [Vector(point) for point in panel["outline"]]
    normal = sum((p.cross(q) for p, q in zip(outline, outline[1:] + outline[:1])), Vector())
    if normal.length < 1e-10:
        raise ValueError("A panel must have a simple, non-degenerate outline.")
    normal.normalize()
    if any(abs((p - outline[0]).dot(normal)) > 1e-5 for p in outline):
        raise ValueError("A panel outline must be planar.")
    triangles = tessellate_polygon([outline])
    if len(triangles) != len(outline) - 2:
        raise ValueError("The panel outline could not be triangulated.")
    half = normal * (panel["thickness"] / 2)
    size = len(outline)
    vertices = np.asarray([tuple(p + half) for p in outline] + [tuple(p - half) for p in outline])
    faces = [tuple(tri) for tri in triangles] + [tuple(i + size for i in reversed(tri)) for tri in triangles]
    for i in range(size):
        j = (i + 1) % size
        faces.extend(((j, i, i + size), (j, i + size, j + size)))
    mesh = make_mesh(panel["name"], vertices, np.asarray(faces))
    rgba = (*panel["rgb"], 1.0)
    material = bpy.data.materials.new(panel["name"])
    material.diffuse_color = rgba
    material.use_nodes = True
    material.node_tree.nodes.get("Principled BSDF").inputs["Base Color"].default_value = rgba
    mesh.materials.append(material)
    colour = mesh.color_attributes.new(name="Color", type="FLOAT_COLOR", domain="POINT")
    colour.data.foreach_set("color", np.tile(np.asarray(rgba, dtype=np.float32), len(vertices)))
    mesh.color_attributes.active_color_name = colour.name
    return mesh
