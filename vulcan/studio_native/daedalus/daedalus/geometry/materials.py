"""CPU-only atlas export from generated geometry and the six original PBR channels."""
import json
from pathlib import Path
import sys
import time

import fast_simplification
import numpy as np
from PIL import Image
from scipy.ndimage import distance_transform_edt
from scipy.spatial import cKDTree
import trimesh


def run(folder):
    start = time.perf_counter()
    geometry = np.load(folder / 'geometry.npz')
    vertices, faces = geometry['vertices'], geometry['faces']
    print('simplify', len(vertices), len(faces), flush=True)
    vertices, faces = fast_simplification.simplify(vertices.astype(np.float64), faces,
                                                  target_count=200000, agg=7)
    vertices = vertices.astype(np.float32)
    print('triangle atlas', len(vertices), len(faces), flush=True)
    # Deterministic isolated-triangle UVs avoid expensive chart packing of an unoriented raw mesh.
    columns = int(np.ceil(np.sqrt(len(faces))))
    face_ids = np.arange(len(faces))
    origins = np.stack([face_ids % columns, face_ids // columns], axis=-1)
    corners = np.array([[0.12, 0.12], [0.88, 0.12], [0.12, 0.88]], dtype=np.float32)
    uvs = ((origins[:, None, :] + corners[None, :, :]) / columns).reshape(-1, 2).astype(np.float32)
    vertices = vertices[faces.ravel()]
    faces = np.arange(len(vertices), dtype=np.int32).reshape(-1, 3)
    print('rasterize', len(vertices), len(faces), flush=True)
    size = 4096
    positions = np.zeros((size, size, 3), dtype=np.float32)
    valid = np.zeros((size, size), dtype=bool)
    uv_pixels = uvs.copy() * size
    uv_pixels[:, 1] = size - uv_pixels[:, 1]
    for face in faces:
        tri = uv_pixels[face]
        x0, y0 = tri[0]
        x1, y1 = tri[1]
        x2, y2 = tri[2]
        denom = (y1 - y2) * (x0 - x2) + (x2 - x1) * (y0 - y2)
        if abs(denom) < 1e-12:
            continue
        lo = np.maximum(np.floor(tri.min(0)).astype(int), 0)
        hi = np.minimum(np.ceil(tri.max(0)).astype(int), size - 1)
        if (hi < lo).any():
            continue
        xx, yy = np.meshgrid(np.arange(lo[0], hi[0] + 1) + 0.5,
                             np.arange(lo[1], hi[1] + 1) + 0.5)
        b0 = ((y1 - y2) * (xx - x2) + (x2 - x1) * (yy - y2)) / denom
        b1 = ((y2 - y0) * (xx - x2) + (x0 - x2) * (yy - y2)) / denom
        b2 = 1 - b0 - b1
        inside = (b0 >= -1e-6) & (b1 >= -1e-6) & (b2 >= -1e-6)
        patch = positions[lo[1]:hi[1] + 1, lo[0]:hi[0] + 1]
        patch[inside] = (b0[..., None] * vertices[face[0]] + b1[..., None] * vertices[face[1]] + b2[..., None] * vertices[face[2]])[inside]
        valid[lo[1]:hi[1] + 1, lo[0]:hi[0] + 1] |= inside
    print('sample PBR', valid.sum(), flush=True)
    voxels = np.load(folder / 'pbr-voxels.npz')
    resolution = int(voxels['resolution'])
    centers = (voxels['coords'].astype(np.float32) + 0.5) / resolution - 0.5
    tree = cKDTree(centers)
    _, nearest = tree.query(positions[valid], workers=4)
    attrs = np.zeros((size, size, 6), dtype=np.float32)
    attrs[valid] = voxels['attributes'][nearest]
    padding = distance_transform_edt(~valid, return_distances=False, return_indices=True)
    attrs = attrs[tuple(padding)]
    color = Image.fromarray(np.clip(np.concatenate([attrs[..., :3],attrs[...,5:6]],axis=-1) * 255, 0, 255).astype(np.uint8))
    mr = np.zeros((size, size, 3), dtype=np.uint8)
    mr[..., 1] = np.clip(attrs[..., 4] * 255, 0, 255).astype(np.uint8)
    mr[..., 2] = np.clip(attrs[..., 3] * 255, 0, 255).astype(np.uint8)
    mr_image = Image.fromarray(mr)
    color.save(folder / 'base-color.png')
    mr_image.save(folder / 'metallic-roughness.png')
    material = trimesh.visual.material.PBRMaterial(name='Generated PBR', baseColorTexture=color,
                  metallicRoughnessTexture=mr_image, metallicFactor=1, roughnessFactor=1,
                  doubleSided=True, alphaMode='MASK' if np.any(attrs[...,5]<0.98) else 'OPAQUE')
    visual = trimesh.visual.TextureVisuals(uv=uvs, material=material)
    yup = vertices[:, [1, 2, 0]]
    mesh = trimesh.Trimesh(vertices=yup, faces=faces, visual=visual, process=False)
    mesh.export(folder / 'model-pbr.glb')
    check = trimesh.load(folder / 'model-pbr.glb', force='mesh', process=False)
    assert len(check.faces) == len(faces)
    proof = {'faces': len(faces), 'vertices_with_uv_seams': len(vertices), 'texture_size': size,
             'seconds': time.perf_counter() - start, 'covered_texels': int(valid.sum()),
             'method': 'target 200k-face simplification; isolated-triangle UV; nearest original PBR voxel; CPU only',
             'raw_geometry_preserved': True, 'alpha_mode': material.alphaMode, 'double_sided': True}
    (folder / 'pbr-export.json').write_text(json.dumps(proof, indent=2))
    print(json.dumps(proof), flush=True)
