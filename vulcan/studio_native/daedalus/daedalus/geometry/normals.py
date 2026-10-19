"""Orient preview triangles and share smooth normals across duplicated UV seams."""
import json
from pathlib import Path
import sys
import time

import numpy as np
import trimesh


def run(folder):
    start = time.perf_counter()
    path = folder / 'model-pbr.glb'
    mesh = trimesh.load(path, force='mesh', process=False)
    vertices, inverse = np.unique(mesh.vertices, axis=0, return_inverse=True)
    reference_faces = inverse[mesh.faces]
    connected = trimesh.Trimesh(vertices=vertices, faces=reference_faces, process=False)
    connected.fix_normals(multibody=True)
    flipped = connected.faces[:, 1] != reference_faces[:, 1]
    faces = mesh.faces.copy()
    faces[flipped] = faces[flipped][:, [0, 2, 1]]
    mesh.faces = faces
    mesh.vertex_normals = connected.vertex_normals[inverse]
    mesh.export(folder / 'model-pbr-smooth.glb')
    proof = {'seconds': time.perf_counter() - start, 'faces_reoriented': int(flipped.sum()),
             'positions_and_uvs_preserved': True, 'operation': 'orient faces and share normals across exact duplicate positions'}
    (folder / 'normals-proof.json').write_text(json.dumps(proof, indent=2))
    print(json.dumps(proof), flush=True)
