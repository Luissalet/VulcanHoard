"""Rasterize the saved blocking camera at every authored frame inside Blender."""

import json
import sys
from pathlib import Path

import bpy
import numpy as np

sys.path.insert(0, str(Path(__file__).parent))
from blender_blocking_worker import png, raster_part


def main(receipt, output):
    scene = bpy.context.scene
    camera = scene.camera
    size = receipt["camera"]["size"]
    parts = [bpy.data.objects[name] for name in receipt["parts"]]
    for frame in range(receipt["keyframes"][-1]["frame"] + 1):
        scene.frame_set(frame)
        depsgraph = bpy.context.evaluated_depsgraph_get()
        view = camera.matrix_world.inverted()
        depth = np.full((size, size), np.inf)
        colors = np.full((size, size, 3), (236, 239, 241), dtype=np.uint8)
        for part in parts:
            part_depth, part_color, _ = raster_part(part, depsgraph, view, camera, size)
            visible = part_depth < depth
            depth[visible] = part_depth[visible]
            colors[visible] = part_color[visible]
        png(output / f"frame-{frame:03d}.png", colors)


if __name__ == "__main__":
    recipe, output = sys.argv[sys.argv.index("--") + 1:]
    main(json.loads(Path(recipe).read_text(encoding="utf-8")), Path(output))
