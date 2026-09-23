"""Software renderer: non-blank, deterministic size, transparent background, handles big and degenerate input."""

import numpy as np
from fixtures import cube, multi_body, open_box, sphere
from PIL import Image

from vulcan.thumbnail import camera_basis, project, render, render_to_file


def alpha_coverage(image: Image.Image) -> float:
    return float((np.asarray(image)[..., 3] > 0).mean())


def test_render_cube_is_square_and_not_blank():
    mesh = cube()
    image = render(mesh.vertices, mesh.faces, size=256)
    assert image.size == (256, 256) and image.mode == "RGBA"
    coverage = alpha_coverage(image)
    assert 0.15 < coverage < 0.8  # something drawn, background still visible
    pixels = np.asarray(image)
    corner = pixels[2, 2]
    assert corner[3] == 0  # transparent corner
    opaque = pixels[pixels[..., 3] > 0][:, :3]
    assert len(np.unique(opaque.reshape(-1, 3), axis=0)) >= 3  # three visible faces, three shades


def test_render_is_deterministic():
    mesh = sphere()
    a = np.asarray(render(mesh.vertices, mesh.faces, size=128))
    b = np.asarray(render(mesh.vertices, mesh.faces, size=128))
    assert np.array_equal(a, b)


def test_render_to_webp_file(tmp_path):
    mesh = multi_body()
    path = render_to_file(mesh.vertices, mesh.faces, tmp_path / "thumbs" / "abc.webp", size=200)
    assert path.is_file() and path.suffix == ".webp"
    with Image.open(path) as image:
        assert image.size == (200, 200)
        assert alpha_coverage(image.convert("RGBA")) > 0.05


def test_open_mesh_and_background_render():
    mesh = open_box()
    image = render(mesh.vertices, mesh.faces, size=96, background=(251, 249, 246))
    pixels = np.asarray(image)
    assert pixels[..., 3].min() == 255  # paper background is opaque
    assert tuple(pixels[1, 1, :3]) == (251, 249, 246)


def test_degenerate_input_does_not_crash():
    empty = render(np.zeros((0, 3)), np.zeros((0, 3), dtype=int), size=64)
    assert alpha_coverage(empty) == 0.0
    flat = np.array([[0, 0, 0], [1, 0, 0], [2, 0, 0]], dtype=float)  # collinear: zero area
    image = render(flat, np.array([[0, 1, 2]]), size=64)
    assert image.size == (64, 64)


def test_camera_basis_is_orthonormal_and_z_up_projects_up():
    basis = camera_basis()
    assert np.allclose(basis @ basis.T, np.eye(3), atol=1e-9)
    screen = project(np.array([[0.0, 0.0, 0.0], [0.0, 0.0, 10.0]]), 100)
    assert screen[1, 1] < screen[0, 1]  # higher Z → smaller image row (up on screen)


def test_large_mesh_renders_in_reasonable_time():
    import time

    mesh = sphere(radius=10, subdivisions=6)  # 81 920 triangles
    started = time.time()
    image = render(mesh.vertices, mesh.faces, size=512)
    assert time.time() - started < 20
    assert alpha_coverage(image) > 0.3
