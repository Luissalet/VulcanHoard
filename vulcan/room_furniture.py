"""Small, editable furniture assemblies. Coordinates are fractions of the envelope."""
import math

import trimesh


def furniture_parts(block):
    """Return named solids inside the exact requested width, depth and height."""
    parts = []

    def box(name, size, center, material=None):
        mesh = trimesh.creation.box([v * s for v, s in zip(size, block.size)])
        mesh.apply_translation([v * s for v, s in zip(center, block.size)])
        parts.append((name, mesh, material or block.material))

    def legs(top=.85):
        for x in (-.41, .41):
            for y in (-.39, .39):
                box('leg', (.08, .08, top), (x, y, top / 2))

    kind = block.kind
    if kind == 'box':
        box('volume', (1, 1, 1), (0, 0, .5))
    elif kind == 'table':
        legs(.92)
        box('top', (1, 1, .08), (0, 0, .96))
    elif kind == 'chair':
        legs(.47)
        box('seat', (1, 1, .09), (0, 0, .5), 'fabric')
        for x in (-.43, .43):
            box('back-post', (.10, .10, .5), (x, .45, .75))
        box('back', (.8, .10, .30), (0, .45, .82), 'fabric')
    elif kind == 'bed':
        legs(.22)
        box('frame', (1, 1, .18), (0, 0, .25))
        box('mattress', (.96, .94, .25), (0, -.02, .465), 'linen')
        box('duvet', (.96, .63, .08), (0, -.17, .63), 'fabric')
        box('headboard', (1, .06, .9), (0, .47, .55))
        for x in (-.24, .24):
            box('pillow', (.4, .18, .12), (x, .31, .65), 'linen')
    elif kind == 'sofa':
        legs(.14)
        box('base', (1, 1, .2), (0, 0, .25), 'fabric')
        box('back', (1, .17, .65), (0, .415, .675), 'fabric')
        for x in (-.445, .445):
            box('arm', (.11, 1, .4), (x, 0, .55), 'fabric')
        for x in (-.25, 0, .25):
            box('cushion', (.24, .77, .19), (x, -.09, .445), 'linen')
    elif kind == 'shelf':
        for x in (-.475, .475):
            box('side', (.05, 1, 1), (x, 0, .5))
        box('back', (1, .04, 1), (0, .48, .5))
        for z in (.025, .26, .5, .74, .975):
            box('shelf', (1, 1, .05), (0, 0, z))
    elif kind == 'wardrobe':
        box('carcass', (1, .92, 1), (0, .04, .5))
        for x in (-.25, .25):
            box('door', (.488, .06, .96), (x, -.47, .5))
        for x in (-.045, .045):
            box('handle', (.018, .025, .13), (x, -.5125 + .025, .52), 'metal')
    for _, mesh, _ in parts:
        mesh.apply_transform(trimesh.transformations.rotation_matrix(math.radians(block.angle), [0, 0, 1]))
        mesh.apply_translation((*block.center, 0))
    return parts
