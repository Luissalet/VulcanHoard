"""Mesh metrics on generated meshes, in every format the scanner reads."""

import pytest
from fixtures import cube, multi_body, open_box, sphere

from vulcan.geometry import format_for, guess_units, inspect_file, inspect_mesh, prettify_name


def test_cube_metrics_from_binary_and_ascii_stl(files):
    for key in ("cube_bin", "cube_ascii"):
        info = inspect_file(files[key])
        assert info.triangles == 12 and info.vertices == 8
        assert info.bbox == (20.0, 30.0, 40.0)
        assert info.volume_cm3 == pytest.approx(24.0)
        assert info.surface_cm2 == pytest.approx(52.0)
        assert info.watertight is True and info.bodies == 1 and info.units_guess == "mm"


def test_sphere_from_obj(files):
    info = inspect_file(files["sphere_obj"])
    assert info.triangles == 1280
    assert info.watertight is True
    assert info.bbox == (20.0, 20.0, 20.0)
    assert info.volume_cm3 == pytest.approx(4.19, abs=0.1)  # icosphere slightly under 4/3·π·r³


def test_open_box_is_not_watertight(files):
    info = inspect_file(files["open_box"])
    assert info.watertight is False
    assert info.triangles == 10
    assert info.bodies == 1


def test_multi_body_3mf(files):
    info = inspect_file(files["multi_3mf"])
    assert info.bodies == 2
    assert info.watertight is True
    assert info.bbox[0] == pytest.approx(76.0, abs=0.01)  # cube 20 at origin + ball radius 6 at x=60


def test_units_guess_flags_inches_and_meters(files):
    assert inspect_file(files["tiny_inch"]).units_guess == "inches"
    import numpy as np

    assert guess_units(np.array([0.12, 0.05, 0.02])) == "meters"
    assert guess_units(np.array([120.0, 50.0, 20.0])) == "mm"
    assert guess_units(np.array([5000.0, 50.0, 20.0])) == "large"


def test_broken_file_raises(files):
    with pytest.raises(Exception):
        inspect_file(files["broken"])


def test_inspect_mesh_directly():
    assert inspect_mesh(cube()).triangles == 12
    assert inspect_mesh(sphere()).watertight
    assert inspect_mesh(open_box()).watertight is False
    assert inspect_mesh(multi_body()).bodies == 2


def test_format_and_names(tmp_path):
    assert format_for(tmp_path / "a.STL") == "stl" and format_for(tmp_path / "b.3MF") == "3mf" and format_for(tmp_path / "c.obj") == "obj"
    assert format_for(tmp_path / "d.step") is None
    assert prettify_name("dragon_bust-v2_supported") == "Dragon bust v2 supported"
    assert prettify_name("phoneStandTall") == "Phone Stand Tall"
    assert prettify_name("  ya__limpio ") == "Ya limpio"
