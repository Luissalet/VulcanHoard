"""The domain code that now comes from the Hoard Link commons: folder policy, atomic writes, ids, search words."""

import json
import os
import re
from pathlib import Path

import pytest

from vulcan.folder_listings import FolderListingStore
from vulcan.hoard_link import ids
from vulcan.organize import Organizer, load_reference_text
from vulcan.search import Filters, fts_query
from vulcan.services import Services

from conftest import make_config

SYSTEM_FOLDERS = [Path.home().anchor, os.environ.get("SystemRoot", r"C:\Windows"), os.environ.get("ProgramFiles", r"C:\Program Files")] if os.name == "nt" else ["/", "/etc", "/usr/lib"]

LIST = "1 Bulbasaur\n2 Ivysaur\n"


# ---------------- add_root: the shared folder policy ----------------

def test_add_root_accepts_a_quoted_pasted_path(services, library):
    root = services.add_root("Quoted", f'  "{library}"  ', None, None, False)
    assert root.path == str(library.resolve())
    assert services.add_root("Again", f"'{library}'", None, None, False).id == root.id  # idempotent through the quotes


@pytest.mark.parametrize("bad", SYSTEM_FOLDERS)
def test_add_root_refuses_broad_and_system_folders(services, bad):
    with pytest.raises(ValueError, match="cannot be indexed"):
        services.add_root("Nope", bad, None, None, False)


def test_add_root_refuses_secret_folders_and_the_data_folder(services, tmp_path):
    secret = tmp_path / "me" / ".ssh"
    secret.mkdir(parents=True)
    with pytest.raises(ValueError, match="cannot be indexed"):
        services.add_root("Keys", str(secret), None, None, False)
    with pytest.raises(ValueError, match="own data folder"):
        services.add_root("Self", str(services.config.data_dir), None, None, False)
    with pytest.raises(ValueError, match="contains the app's own data folder"):
        services.add_root("Parent", str(services.config.data_dir.parent), None, None, False)


def test_add_root_missing_folder_keeps_its_message(services, tmp_path):
    with pytest.raises(ValueError, match="The folder does not exist"):
        services.add_root("Gone", str(tmp_path / "nope"), None, None, False)


def test_add_root_policy_over_http(client, tmp_path):
    refused = client.post("/api/roots", json={"path": SYSTEM_FOLDERS[1]})
    assert refused.status_code == 400 and "cannot be indexed" in refused.text
    assert client.post("/api/roots", json={"path": str(client.services.config.data_dir)}).status_code == 400


# ---------------- ids ----------------

def test_new_plan_and_apply_ids_are_ulids_and_old_ids_still_load(tmp_path):
    loose = tmp_path / "loose"
    loose.mkdir()
    (loose / "Bulbasaur.stl").write_text("x")
    org = Organizer(tmp_path / "organize")
    plan = org.plan(str(loose), LIST)
    prefix, ulid = plan["id"].split("_", 1)
    assert prefix == "plan" and ids.is_ulid(ulid)
    journal = org.apply(plan["id"])
    assert journal["id"].startswith("apply_") and ids.is_ulid(journal["id"].split("_", 1)[1])
    assert org.get_apply(journal["id"])["plan_id"] == plan["id"]

    old = dict(plan, id="plan-20240101-120000-a1b2c3", created_at=1.0)  # written by an earlier version
    (tmp_path / "organize" / "plans" / "plan-20240101-120000-a1b2c3.json").write_text(json.dumps(old))
    assert org.get_plan("plan-20240101-120000-a1b2c3")["id"] == "plan-20240101-120000-a1b2c3"
    listed = [p["id"] for p in org.list_plans()]
    assert plan["id"] == listed[0] and "plan-20240101-120000-a1b2c3" in listed  # newest first, both generations present


# ---------------- atomic writes ----------------

def test_plan_and_journal_files_are_written_atomically(tmp_path):
    loose = tmp_path / "loose"
    loose.mkdir()
    (loose / "Bulbasaur.stl").write_text("x")
    org = Organizer(tmp_path / "organize")
    plan = org.plan(str(loose), LIST)
    org.apply(plan["id"])
    for kind in ("plans", "journals"):
        files = list((tmp_path / "organize" / kind).iterdir())
        assert files and all(f.suffix == ".json" for f in files), files  # no temp files left behind
        assert all(isinstance(json.loads(f.read_text(encoding="utf-8")), dict) for f in files)


def test_listing_file_write_is_atomic_and_keeps_the_one_time_backup(scanned, library):
    services, root = scanned
    folder = library
    path = folder / "cults3d.json"
    path.write_text(json.dumps({"title": "Mine"}), encoding="utf-8")
    FolderListingStore._write_file(root, "", {"title": "A", "description": "d", "tags": ["x"]})
    FolderListingStore._write_file(root, "", {"title": "B", "description": "d", "tags": ["y"]})
    assert json.loads(path.read_text(encoding="utf-8"))["title"] == "B"
    assert json.loads((folder / "cults3d.json.bak").read_text(encoding="utf-8")) == {"title": "Mine"}
    assert path.read_text(encoding="utf-8").endswith("}\n")
    assert not [p for p in folder.iterdir() if p.name.endswith(".tmp")]


def test_thumbnail_render_leaves_no_temp_file(tmp_path):
    import numpy as np
    from vulcan.thumbnail import render_to_file

    vertices = np.array([[0, 0, 0], [1, 0, 0], [0, 1, 0], [0, 0, 1]], dtype=float)
    faces = np.array([[0, 1, 2], [0, 1, 3], [0, 2, 3], [1, 2, 3]])
    out = tmp_path / "thumbs" / "abc.webp"
    render_to_file(vertices, faces, out, size=64)
    render_to_file(vertices, faces, out, size=64)
    assert out.is_file() and [p.name for p in out.parent.iterdir()] == ["abc.webp"]


# ---------------- reference file paths ----------------

def test_reference_file_path_may_come_quoted(tmp_path):
    ref = tmp_path / "list.txt"
    ref.write_text("1 Bulbasaur\n", encoding="utf-8")
    text, source = load_reference_text(f'"{ref}"')
    assert source == str(ref) and "Bulbasaur" in text
    assert load_reference_text(f"'{ref}'")[1] == str(ref)


# ---------------- search words ----------------

def test_search_drops_glue_words_and_stems(scanned):
    services, _ = scanned
    assert fts_query("the dragons") == '"dragon"*'
    assert services.search.query(Filters(q="the dragons"))["total"] == services.search.query(Filters(q="dragon"))["total"] > 0
    assert services.search.query(Filters(q='dragon "'))["total"] > 0  # a stray quote is harmless
