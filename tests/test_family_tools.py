"""model_import_file and listings_export_catalog: the two tools other apps of the family call through the hub."""

import pytest

from fixtures import cube, sphere
from vulcan import family_tools
from vulcan.agent_tools import call_tool


@pytest.fixture
def events(monkeypatch):
    sent, links = [], []
    monkeypatch.setattr(family_tools.family, "emit", lambda kind, data=None, **kw: sent.append((kind, data or {})) or True)
    monkeypatch.setattr(family_tools.fam_refs, "link", lambda *a, **kw: links.append((a, kw)) or {"ok": True})
    return sent, links


def export(tmp_path, name="relief.stl", mesh=None):
    folder = tmp_path / "exports"
    folder.mkdir(exist_ok=True)
    path = folder / name
    (mesh or cube((10, 20, 30))).export(path, file_type="stl")
    return path


def test_import_outside_any_root_makes_a_small_root_and_emits(services, tmp_path, events):
    sent, links = events
    path = export(tmp_path)
    result = call_tool(services, "model_import_file", {"path": str(path), "source_ref": "hoard://plato/export/7"})
    assert result["ok"] and result["created"] and result["format"] == "stl" and result["status"] == "ok"
    assert result["ref"] == f"hoard://vulcan/model/{result['id']}" and result["has_thumb"]
    assert [round(v) for v in result["bbox"]] == [10, 20, 30] and result["watertight"] is True
    root = services.roots.get(result["root_id"])
    assert root.imported and root.include == ["relief.stl"] and root.watch is False
    assert services.models.get(result["id"])["source_ref"] == "hoard://plato/export/7"
    assert sent == [("vulcan.model.added", {"model_id": result["id"], "ref": result["ref"], "name": result["name"], "format": "stl",
                                            "path": str(path.resolve()), "source_ref": "hoard://plato/export/7"})]
    import time
    for _ in range(50):
        if links:
            break
        time.sleep(0.05)
    assert links and links[0][0][:3] == (result["ref"], "hoard://plato/export/7", "source")


def test_import_twice_changes_nothing_and_emits_once(services, tmp_path, events):
    sent, _ = events
    path = export(tmp_path)
    first = call_tool(services, "model_import_file", {"path": str(path)})
    second = call_tool(services, "model_import_file", {"path": str(path)})
    assert (second["id"], second["created"], second["changed"]) == (first["id"], False, False)
    assert len(sent) == 1
    assert services.stats.counts()["models"] == 1


def test_second_file_of_the_same_folder_extends_the_import_root(services, tmp_path, events):
    a = call_tool(services, "model_import_file", {"path": str(export(tmp_path, "a.stl"))})
    b = call_tool(services, "model_import_file", {"path": str(export(tmp_path, "b.stl", sphere()))})
    assert a["root_id"] == b["root_id"] and a["id"] != b["id"]
    assert sorted(services.roots.get(a["root_id"]).include) == ["a.stl", "b.stl"]
    # an unrelated file in the same folder is not listed by a rescan of that root
    export(tmp_path, "other.stl")
    assert services.rescan(a["root_id"]) and services.worker.wait_idle(120)
    assert services.stats.counts()["models"] == 2


def test_file_inside_a_scanned_root_uses_it(scanned, library, events):
    services, root = scanned
    before = services.stats.counts()["models"]
    path = library / "nuevo_relieve.stl"
    cube((5, 5, 5)).export(path, file_type="stl")
    result = call_tool(services, "model_import_file", {"path": str(path)})
    assert result["root_id"] == root.id and result["rel_path"] == "nuevo_relieve.stl" and result["created"]
    assert services.stats.counts()["models"] == before + 1
    assert [r for r in services.roots.list() if r.imported] == []
    # a later scan sees the same row, it does not duplicate it
    assert services.rescan(root.id) and services.worker.wait_idle(120)
    assert services.stats.counts()["models"] == before + 1


def test_duplicate_of_a_known_model_is_reported(scanned, library, events):
    services, _ = scanned
    copy = library.parent / "fuera.stl"
    copy.write_bytes((library / "dragones" / "dragon_cubo_v2.stl").read_bytes())
    result = call_tool(services, "model_import_file", {"path": str(copy)})
    assert result["duplicate_of"] is not None or services.models.get(result["id"])["dupe_of"] is not None
    original = services.models.by_path("dragones/dragon_cubo_v2.stl")
    assert services.models.get(result["id"])["dupe_of"] in (None, original["id"])


@pytest.mark.parametrize("make,message", [
    (lambda tmp: "", "absolute path"),
    (lambda tmp: "relative.stl", "absolute"),
    (lambda tmp: str(tmp / "missing.stl"), "does not exist"),
])
def test_import_rejects_bad_paths(services, tmp_path, make, message):
    with pytest.raises(ValueError, match=message):
        call_tool(services, "model_import_file", {"path": make(tmp_path) or " "})


def test_import_rejects_formats_refs_and_unreadable_files(services, tmp_path, events):
    text = tmp_path / "notes.txt"
    text.write_text("x")
    with pytest.raises(ValueError, match="Unsupported format"):
        call_tool(services, "model_import_file", {"path": str(text)})
    path = export(tmp_path)
    with pytest.raises(ValueError, match="hoard://"):
        call_tool(services, "model_import_file", {"path": str(path), "source_ref": "https://example.com/x"})
    broken = tmp_path / "exports" / "roto.stl"
    broken.write_bytes(b"not a mesh\n" * 10)
    with pytest.raises(ValueError, match="Could not read"):
        call_tool(services, "model_import_file", {"path": str(broken)})


def test_file_left_out_by_root_rules_is_refused(services, tmp_path):
    folder = tmp_path / "rules"
    folder.mkdir()
    services.roots.add("Rules", str(folder), None, ["**/skip_*"], False)
    path = folder / "skip_me.stl"
    cube().export(path, file_type="stl")
    with pytest.raises(ValueError, match="leave it out"):
        call_tool(services, "model_import_file", {"path": str(path)})


def test_migration_adds_source_ref_and_imported(services):
    with services.db.lock:
        assert "source_ref" in {r["name"] for r in services.db.conn.execute("PRAGMA table_info(models)")}
        assert "imported" in {r["name"] for r in services.db.conn.execute("PRAGMA table_info(roots)")}


# ---------- listings_export_catalog ----------
def test_catalog_export_shape_and_filter(scanned, library):
    services, root = scanned
    dragon_a = services.models.by_path("dragones/dragon_cubo_v2.stl")
    sphere_model = services.models.by_path("esfera_lisa.obj")
    services.listings.set(dragon_a["id"], {"title": "Cubo dragón", "description": "Un cubo.", "tags": ["cubo"], "price_hint": "4-6"}, source="assistant")
    tags = [f"tag{i}" for i in range(20)]
    services.folder_listings.set(root.id, "dragones", {"title": "Dragon pack", "description": "x" * 220, "tags": tags}, status="approved")
    result = call_tool(services, "listings_export_catalog", {})
    assert result["ok"] and result["count"] == len(result["listings"]) == 2 and result["note"] is None
    by_kind = {e["kind"]: e for e in result["listings"]}
    folder_entry, model_entry = by_kind["folder"], by_kind["model"]
    assert folder_entry["ref"].startswith("hoard://vulcan/folder/") and folder_entry["title"] == "Dragon pack"
    assert folder_entry["tags"] == tags and folder_entry["status"] == "approved" and folder_entry["price"] is None
    assert folder_entry["folder"] == str(library / "dragones") and dragon_a["id"] in folder_entry["model_ids"]
    assert all(isinstance(i, int) for i in folder_entry["model_ids"]) and len(folder_entry["model_ids"]) == 3
    assert model_entry["ref"] == f"hoard://vulcan/model/{dragon_a['id']}" and model_entry["price"] == "4-6"
    assert model_entry["model_ids"] == [dragon_a["id"]] and model_entry["updated_at"].endswith("+00:00")
    # folder filter: absolute and relative, with sub-folders included
    assert call_tool(services, "listings_export_catalog", {"folder": str(library / "dragones")})["count"] == 2
    assert call_tool(services, "listings_export_catalog", {"folder": "dragones"})["count"] == 2
    assert call_tool(services, "listings_export_catalog", {"folder": "soportes"})["count"] == 0
    services.listings.set(sphere_model["id"], {"title": "Esfera"})
    assert call_tool(services, "listings_export_catalog", {})["count"] == 3
    assert call_tool(services, "listings_export_catalog", {"folder": "dragon"})["count"] == 0  # a folder name prefix is not a folder


def test_catalog_export_empty_says_so(scanned):
    services, _ = scanned
    result = call_tool(services, "listings_export_catalog", {})
    assert result["listings"] == [] and "No listings yet" in result["note"]


def test_catalog_is_what_mercator_imports(scanned):
    """The entry keys are the ones Mercator's catalogue upsert reads."""
    services, root = scanned
    cube_model = services.models.by_path("dragones/dragon_cubo_v2.stl")
    services.listings.set(cube_model["id"], {"title": "Cubo", "tags": ["a", "b"]})
    entry = call_tool(services, "listings_export_catalog", {})["listings"][0]
    assert set(entry) >= {"ref", "kind", "title", "description", "tags", "price", "folder", "model_ids", "status", "updated_at"}


def test_tools_are_registered_with_the_right_hints():
    from vulcan.agent_tools import TOOLS_BY_NAME

    assert TOOLS_BY_NAME["listings_export_catalog"].annotations["readOnlyHint"] is True
    assert TOOLS_BY_NAME["model_import_file"].annotations["readOnlyHint"] is False
