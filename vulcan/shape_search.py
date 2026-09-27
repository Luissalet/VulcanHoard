"""Rotation-tolerant voxel comparison for finding related printable shapes.

This is a retrieval hint, never an automatic duplicate classification. Only
closed meshes are filled; open meshes have no well-defined solid interior.
"""

from __future__ import annotations

from itertools import permutations, product
from pathlib import Path

import numpy as np
import trimesh

GRID = 32
LONGEST_SIDE = 24.0


def descriptor(path: str) -> np.ndarray | None:
    """A centered, scale-normalized occupancy grid, or None for unsupported geometry."""
    try:
        mesh = trimesh.load(Path(path), force="mesh", process=True)
        if not isinstance(mesh, trimesh.Trimesh) or not mesh.is_watertight or mesh.is_empty:
            return None
        extent = np.asarray(mesh.extents, dtype=float)
        longest = float(extent.max())
        if not np.isfinite(longest) or longest <= 0:
            return None
        mesh.vertices = (np.asarray(mesh.vertices) - np.asarray(mesh.bounds).mean(axis=0)) * (LONGEST_SIDE / longest)
        points = mesh.voxelized(pitch=1.0).fill().points
        if len(points) == 0:
            return None
        indices = np.rint(points).astype(int) + GRID // 2
        inside = np.all((indices >= 0) & (indices < GRID), axis=1)
        grid = np.zeros((GRID, GRID, GRID), dtype=bool)
        grid[tuple(indices[inside].T)] = True
        return grid if grid.any() else None
    except (OSError, ValueError, TypeError, ImportError):
        return None


def similarity(a: np.ndarray, b: np.ndarray) -> float:
    """Best voxel IoU over the 24 right-angle orientations of b."""
    count_a = int(a.sum())
    count_b = int(b.sum())
    if not count_a or not count_b:
        return 0.0
    best = 0.0
    for axes in permutations(range(3)):
        parity = -1 if sum(axes[i] > axes[j] for i in range(3) for j in range(i + 1, 3)) % 2 else 1
        transposed = np.transpose(b, axes)
        for flips in product((False, True), repeat=3):
            if parity * (-1 if sum(flips) % 2 else 1) != 1:
                continue
            oriented = np.flip(transposed, axis=tuple(i for i, flip in enumerate(flips) if flip))
            intersection = int(np.count_nonzero(a & oriented))
            best = max(best, intersection / (count_a + count_b - intersection))
    return round(best, 4)
