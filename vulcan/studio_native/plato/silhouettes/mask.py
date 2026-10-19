"""Binary mask convention: 255=foreground, 0=background."""
import math
import numpy as np
from PIL import Image


def prepare_mask(image, alpha_threshold=128):
    if not isinstance(alpha_threshold, (int, np.integer)) or not 0 <= alpha_threshold <= 255:
        raise ValueError("Alpha threshold must be an integer in 0..255")
    img = image.convert("RGBA")
    rgba = np.asarray(img)
    alpha = rgba[:, :, 3]
    if alpha.min() < 250:
        foreground = alpha > alpha_threshold
    else:
        rgb = rgba[:, :, :3].astype(np.float64)
        luminance = rgb @ np.array([0.299, 0.587, 0.114])
        foreground = luminance < 128
    return foreground.astype(np.uint8) * 255


def remove_small_components(mask, min_area=0):
    """Remove foreground components smaller than min_area PIXELS, not holes.

    Connectivity is explicitly 4-neighbour. No blur, closing or hole filling.
    """
    if not math.isfinite(min_area) or min_area < 0:
        raise ValueError("Speckle area must be nonnegative and finite")
    if min_area <= 1:
        return np.asarray(mask, dtype=np.uint8).copy()
    from scipy import ndimage
    labels, _ = ndimage.label(mask != 0, structure=ndimage.generate_binary_structure(2, 1))
    sizes = np.bincount(labels.ravel())
    keep = sizes >= min_area
    keep[0] = False
    return keep[labels].astype(np.uint8) * 255


def mask_to_silhouette(mask):
    mask = np.asarray(mask, dtype=np.uint8)
    silhouette = Image.new("RGBA", (mask.shape[1], mask.shape[0]), (0, 0, 0, 0))
    silhouette.putalpha(Image.fromarray(mask))
    return silhouette
