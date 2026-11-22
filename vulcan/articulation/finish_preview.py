"""Produce same-camera articulation comparisons on the CPU."""
from types import SimpleNamespace

import bpy
import numpy as np
from mathutils import Vector

from worker import cameras, render


class Preview:
    def __init__(self, source, recipe, objects, rigs):
        scene = self.scene = bpy.data.scenes.new("Articulation comparison")
        for obj in [*objects, *rigs]:
            scene.collection.objects.link(obj)
        scene.render.engine = "CYCLES"
        scene.cycles.device = "CPU"
        scene.cycles.samples = 4
        scene.cycles.use_denoising = False
        scene.render.resolution_x = scene.render.resolution_y = recipe["view_size"]
        scene.render.resolution_percentage = 100
        scene.render.image_settings.file_format = "PNG"
        scene.render.threads_mode = "FIXED"
        scene.render.threads = 6
        scene.view_settings.view_transform = "Standard"
        scene.world = bpy.data.worlds.new("Comparison world")
        scene.world.use_nodes = True
        background = scene.world.node_tree.nodes.get("Background")
        background.inputs["Color"].default_value = (0.27, 0.27, 0.27, 1)
        background.inputs["Strength"].default_value = 0.8
        light = bpy.data.lights.new("Comparison light", "SUN")
        light.energy = 1.2
        light_obj = bpy.data.objects.new(light.name, light)
        scene.collection.objects.link(light_obj)
        scene.frame_set(source.frame_current, subframe=source.frame_subframe)
        points = [obj.matrix_world @ Vector(corner) for obj in objects for corner in obj.bound_box]
        self.views, self.metadata = cameras(
            scene, {"cameras": recipe["cameras"], "focus": None, "view_scale": None},
            SimpleNamespace(vertices=np.asarray(points)),
        )
        self.light = light_obj
        self.objects = objects
        self.clearance = float(np.linalg.norm(np.ptp(points, axis=0))) * 0.1
        for obj in scene.objects:
            if obj.type == "CAMERA":
                obj.data.clip_start = 0.0001

    def capture(self, output, prefix):
        previous = bpy.context.window.scene
        bpy.context.window.scene = self.scene
        try:
            bpy.context.view_layer.update()
            paths = []
            for name, camera in self.views.items():
                toward = camera.matrix_world.to_3x3() @ Vector((0, 0, 1))
                farthest = max((obj.matrix_world @ Vector(corner) - camera.location).dot(toward)
                               for obj in self.objects for corner in obj.bound_box)
                if prefix == "before" and farthest + self.clearance >= 0:
                    camera.location += toward * (farthest + self.clearance)
                    bpy.context.view_layer.update()
                elif farthest >= -camera.data.clip_start:
                    raise ValueError("The comparison camera intersects a mesh. / La cámara de comparación cruza una malla.")
                result = self.metadata["views"][name]
                result["matrix"] = [list(row) for row in camera.matrix_world]
                result["location"] = list(camera.location)
                result["clip_start"] = camera.data.clip_start
                offset = camera.matrix_world.to_3x3() @ Vector((-0.45, 0.6, 1))
                self.light.rotation_euler = (-offset).to_track_quat("-Z", "Y").to_euler()
                paths.extend(render(self.scene, output, {name: camera}, prefix, "MATERIAL"))
            return paths
        finally:
            bpy.context.window.scene = previous

    def close(self):
        world = self.scene.world
        bpy.data.scenes.remove(self.scene)
        bpy.data.worlds.remove(world)
        for item, data in [(self.light, bpy.data.lights), *((obj, bpy.data.cameras) for obj in self.views.values())]:
            block = item.data
            bpy.data.objects.remove(item, do_unlink=True)
            data.remove(block)
