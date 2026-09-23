"""Incremental scanning, dedupe, near-dupes, collections and thumbnails on disk."""

import os
import shutil
import time
from pathlib import Path

from fixtures import EXPECTED_MODELS, cube

from vulcan.scanner import Matcher, collection_for, glob_to_regex, walk
from vulcan.search import Filters


def test_glob_matcher():
    assert glob_to_regex("**/*.stl").match("a/b/c.STL")
    assert glob_to_regex("*.obj").match("x.obj") and not glob_to_regex("*.obj").match("dir/x.obj")
    m = Matcher(["**/*.stl"], ["**/.*", "**/old/**"])
    assert m.accepts("dragones/a.stl") and not m.accepts("dragones/a.obj")
    assert not m.accepts("old/a.stl") and not m.accepts(".hidden.stl")
    assert m.excludes_dir("old") and m.excludes_dir(".git") and m.excludes_dir(".oculta") and not m.excludes_dir("dragones")


def test_walk_and_collections(scanned, library):
    services, root = scanned
    found = walk(root)
    assert len(found) == EXPECTED_MODELS
    assert all(not rel.startswith(".oculta") for rel, _ in found)
    assert collection_for(root, "dragones/dragon_cubo_v2.stl") == "dragones"
    assert collection_for(root, "esfera_lisa.obj") == "Pruebas"


def test_first_scan_records_metrics_errors_and_thumbs(scanned):
    services, root = scanned
    progress = services.worker.progress(root.id)
    assert progress["phase"] == "done"
    assert progress["files_total"] == EXPECTED_MODELS and progress["files_done"] == EXPECTED_MODELS
    assert progress["error_count"] == 1 and progress["errors"][0]["path"] == "roto.stl"
    counts = services.stats.counts()
    assert counts["models"] == EXPECTED_MODELS and counts["errors"] == 1
    assert counts["thumbs"] == EXPECTED_MODELS - 1  # every parsed file has a thumbnail
    broken = services.models.by_path("roto.stl")
    assert broken["status"] == "error" and broken["triangles"] is None and broken["error"]
    cube_row = services.models.by_path("dragones/dragon_cubo_v2.stl")
    assert cube_row["name"] == "Dragon cubo v2" and cube_row["collection"] == "dragones"
    assert cube_row["bbox"] == [20.0, 30.0, 40.0] and cube_row["watertight"] is True
    assert Path(cube_row["thumb_path"]).is_file() and cube_row["thumb_path"].endswith(f"{cube_row['sha256']}.webp")
    assert cube_row["sha256"] and len(cube_row["sha256"]) == 64


def test_exact_and_near_duplicates(scanned):
    services, root = scanned
    exact = services.dupes.exact()
    assert len(exact) == 1
    names = sorted(m["rel_path"] for m in exact[0]["models"])
    assert names == ["dragones/dragon_cubo_v2.stl", "soportes/copia_del_cubo.stl"]
    copy = services.models.by_path("soportes/copia_del_cubo.stl")
    original = services.models.by_path("dragones/dragon_cubo_v2.stl")
    assert copy["dupe_of"] == original["id"] and original["dupe_of"] is None
    near = services.dupes.near()
    near_paths = {m["rel_path"] for g in near for m in g["models"]}
    assert {"esfera_lisa.obj", "soportes/esfera_casi_igual.stl"} <= near_paths
    # the ascii and binary cubes are the same geometry with different bytes: near, not exact
    assert {"dragones/dragon_cubo_v2.stl", "dragones/dragon_cubo_ascii.stl"} <= near_paths
    per_model = services.dupes.for_model(original)
    assert [m["id"] for m in per_model["exact"]] == [copy["id"]]
    assert "dragones/dragon_cubo_ascii.stl" in {m["rel_path"] for m in per_model["near"]}
    assert copy["id"] not in {m["id"] for m in per_model["near"]}
    dupes_only = services.search.query(Filters(dupes_only=True))
    assert dupes_only["total"] == 2


def test_rescan_is_incremental_and_keeps_user_edits(scanned, library):
    services, root = scanned
    cube_row = services.models.by_path("dragones/dragon_cubo_v2.stl")
    services.models.patch(cube_row["id"], {"tags": ["Dragón", "dragón"], "notes": "imprimir a 0.2"})
    services.listings.set(cube_row["id"], {"title": "Cubo dragón"})
    # touch one file (same bytes), change another, add one, remove one
    path = library / "dragones" / "dragon_cubo_v2.stl"
    os.utime(path, (time.time() + 5, time.time() + 5))
    cube((10.0, 10.0, 10.0)).export(library / "soportes" / "caja_abierta.stl", file_type="stl")
    cube((5.0, 6.0, 7.0)).export(library / "nuevo.stl", file_type="stl")
    (library / "esfera_lisa.obj").unlink()
    assert services.rescan(root.id) and services.worker.wait_idle(120)
    progress = services.worker.progress(root.id)
    assert progress["files_changed"] == 2 and progress["files_removed"] == 1  # changed box + new file; the touched cube was not re-read
    same = services.models.by_path("dragones/dragon_cubo_v2.stl")
    assert same["id"] == cube_row["id"] and same["tags"] == ["dragón"] and same["notes"] == "imprimir a 0.2"
    assert services.listings.get(same["id"])["title"] == "Cubo dragón"
    changed = services.models.by_path("soportes/caja_abierta.stl")
    assert changed["watertight"] is True and changed["bbox"] == [10.0, 10.0, 10.0]
    assert services.models.by_path("esfera_lisa.obj") is None
    assert services.models.by_path("nuevo.stl")["name"] == "Nuevo"
    assert services.stats.counts()["models"] == EXPECTED_MODELS


def test_missing_thumbs_are_re_rendered(scanned):
    services, root = scanned
    shutil.rmtree(services.config.thumbs_dir)
    services.config.thumbs_dir.mkdir()
    assert services.rescan(root.id) and services.worker.wait_idle(120)
    progress = services.worker.progress(root.id)
    assert progress["files_changed"] == 0 and progress["thumbs_rendered"] == EXPECTED_MODELS - 2  # minus the broken file and the exact copy (shares its thumb)
    assert all(Path(m["thumb_path"]).is_file() for m in services.search.query(Filters(status="ok", limit=100))["models"])


def test_too_large_files_are_listed_but_skipped(tmp_path, library):
    from conftest import make_config

    from vulcan.services import Services

    svc = Services(make_config(tmp_path, max_file_mb=1))
    svc.worker.start()
    try:
        big = library / "grande.stl"
        with big.open("wb") as handle:
            handle.write(b"\0" * (1024 * 1024 + 10))
        root = svc.add_root("Pruebas", str(library), None, None, False)
        assert svc.worker.wait_idle(120)
        row = svc.models.by_path("grande.stl")
        assert row["status"] == "skipped" and "too large" in row["error"] and row["triangles"] is None
        assert svc.worker.progress(root.id)["files_skipped"] == 1
    finally:
        svc.stop()


def test_remove_root_purges_models_and_fts(scanned):
    services, root = scanned
    assert services.search.query(Filters(q="cubo"))["total"] >= 2
    assert services.remove_root(root.id)
    assert services.stats.counts()["models"] == 0
    assert services.search.query(Filters(q="cubo"))["total"] == 0
    with services.db.lock:
        assert services.db.conn.execute("SELECT COUNT(*) FROM models_fts").fetchone()[0] == 0
