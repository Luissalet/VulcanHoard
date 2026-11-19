"""Find a flat, border-connected background when the source has no usable mask."""
import numpy as np
from PIL import Image
from scipy.ndimage import binary_propagation


def foreground(image):
    image = image.convert('RGBA')
    rgba = np.asarray(image).copy()
    # Small alpha variations across an otherwise opaque export do not encode a cutout.
    if rgba[..., 3].min() < 250:
        return image
    rgb = rgba[..., :3].astype(np.float32)
    border = np.concatenate((rgb[0], rgb[-1], rgb[:, 0], rgb[:, -1]))
    color = np.median(border, axis=0)
    if (np.linalg.norm(border - color, axis=-1) < 18).mean() <= .95:
        return image
    close = np.linalg.norm(rgb - color, axis=-1) < 18
    seed = np.zeros_like(close)
    seed[0], seed[-1], seed[:, 0], seed[:, -1] = close[0], close[-1], close[:, 0], close[:, -1]
    background = binary_propagation(seed, mask=close)
    if not background.all():
        rgba[..., 3][background] = 0
        return Image.fromarray(rgba)
    return image
