"""Rasterize the saved blocking camera at every authored frame inside Blender."""

import json
import sys
from pathlib import Path

import bpy
import numpy as np

sys.path.insert(0, str(Path(__file__).parent))
from blender_blocking_worker import png, raster_part


def main(receipt, output, frame_count, cuda):
    scene = bpy.context.scene
    if receipt.get("detail") == "articulated":
        scene.cycles.samples = 4
        if cuda:
            preferences = bpy.context.preferences.addons["cycles"].preferences
            preferences.compute_device_type = "CUDA"
            preferences.refresh_devices()
            devices = [device for device in preferences.devices if device.type == "CUDA"]
            if len(devices) != 1:
                raise RuntimeError("The reserved CUDA device is unavailable. / La GPU CUDA reservada no está disponible.")
            for device in preferences.devices:
                device.use = device.type == "CUDA"
            scene.cycles.device = "GPU"
            print(f"Cycles CUDA: {devices[0].name}", flush=True)
        for frame in range(frame_count):
            scene.frame_set(frame)
            scene.render.filepath = str(output / f"frame-{frame:03d}.png")
            bpy.ops.render.render(write_still=True)
        return
    camera = scene.camera
    size = receipt["camera"]["size"]
    parts = [bpy.data.objects[name] for name in receipt["parts"]]
    for frame in range(frame_count):
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
    recipe, output, frame_count, *device = sys.argv[sys.argv.index("--") + 1:]
    main(json.loads(Path(recipe).read_text(encoding="utf-8")), Path(output), int(frame_count), device == ["cuda"])
