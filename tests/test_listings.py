"""Listings (set/get/merge/idempotent), tags, notes, albums and dupe bookkeeping through the stores and the agent tools."""

import pytest

from vulcan.agent_tools import TOOLS, call_tool
from vulcan.store import normalise_tags


def test_normalise_tags():
    assert normalise_tags([" Dragón ", "dragón", "DRAGÓN", "", "  ", "a  b"]) == ["dragón", "a b"]


def test_listing_set_get_merge_and_source(scanned):
    services, _ = scanned
    cube = services.models.by_path("dragones/dragon_cubo_v2.stl")
    assert services.listings.get(cube["id"]) is None
    first = services.listings.set(cube["id"], {"title": " Cubo dragón ", "description": "Un cubo.", "tags": ["Cubo", "cubo", "dragón"]}, source="assistant")
    assert first["title"] == "Cubo dragón" and first["tags"] == ["cubo", "dragón"] and first["listing_source"] == "assistant"
    assert first["language"] == "es" and first["listing_updated_at"] > 0
    second = services.listings.set(cube["id"], {"category": "Juguetes"})  # partial update keeps the rest
    assert second["title"] == "Cubo dragón" and second["category"] == "Juguetes" and second["listing_source"] == "manual"
    again = services.listings.set(cube["id"], {"category": "Juguetes"})
    assert {k: v for k, v in again.items() if k != "listing_updated_at"} == {k: v for k, v in second.items() if k != "listing_updated_at"}
    assert services.models.get(cube["id"])["has_listing"] is True
    assert services.listings.count() == 1
    with pytest.raises(LookupError):
        services.listings.set(999999, {"title": "x"})
    assert services.listings.remove(cube["id"]) and services.listings.get(cube["id"]) is None


def test_listing_survives_rescan_and_is_deleted_with_model(scanned, library):
    services, root = scanned
    sphere = services.models.by_path("esfera_lisa.obj")
    services.listings.set(sphere["id"], {"title": "Esfera"})
    (library / "esfera_lisa.obj").unlink()
    assert services.rescan(root.id) and services.worker.wait_idle(120)
    with services.db.lock:
        assert services.db.conn.execute("SELECT COUNT(*) FROM listings").fetchone()[0] == 0


def test_tags_and_notes(scanned):
    services, _ = scanned
    cube = services.models.by_path("dragones/dragon_cubo_v2.stl")
    assert services.models.tag(cube["id"], ["Dragón", "fantasía"], [])["tags"] == ["dragón", "fantasía"]
    assert services.models.tag(cube["id"], ["dragón"], ["Fantasía"])["tags"] == ["dragón"]
    assert services.models.patch(cube["id"], {"notes": "soportes en árbol"})["notes"] == "soportes en árbol"
    assert services.models.patch(cube["id"], {"name": "  Dragón cúbico "})["name"] == "Dragón cúbico"
    assert services.models.patch(cube["id"], {"collection": "Mascotas"})["collection"] == "Mascotas"
    assert services.models.patch(999999, {"notes": "x"}) is None
    assert services.models.tag(999999, ["a"], []) is None


def test_albums(scanned):
    services, _ = scanned
    a = services.models.by_path("dragones/dragon_cubo_v2.stl")["id"]
    b = services.models.by_path("esfera_lisa.obj")["id"]
    album = services.albums.create("Campaña primavera", [a, b, a])
    assert album["model_ids"] == [a, b] and album["count"] == 2
    same = services.albums.create("campaña PRIMAVERA", [])  # idempotent by name (case-insensitive)
    assert same["id"] == album["id"]
    updated = services.albums.update(album["id"], "Primavera", None, [a])
    assert updated["name"] == "Primavera" and updated["model_ids"] == [b]
    assert services.albums.for_model(b) == [{"id": album["id"], "name": "Primavera"}]
    with pytest.raises(LookupError):
        services.albums.update(album["id"], None, [999999], None)
    with pytest.raises(ValueError):
        services.albums.create("   ", [])
    assert services.albums.remove(album["id"]) and services.albums.list() == []
    assert services.albums.update(album["id"], "x", None, None) is None


def test_agent_tools_catalog_and_calls(scanned):
    services, root = scanned
    names = [t.name for t in TOOLS]
    assert names == ["models_search", "model_info", "model_listing_get", "model_listing_set", "model_tag", "model_note", "models_stats", "models_dupes", "models_add_root", "models_rescan", "models_recent",
                      "folder_listings", "folder_listing_get", "folder_listing_set", "folder_listing_check", "folder_listing_draft", "folder_listings_export"]
    for tool in TOOLS:
        assert "Sinónimos:" in tool.description and set(tool.annotations) == {"readOnlyHint", "destructiveHint", "idempotentHint", "openWorldHint"}
    hits = call_tool(services, "models_search", {"q": "cubo", "format": "stl"})
    assert hits["count"] >= 3 and hits["hits"][0]["thumb_url"].startswith("/api/models/") and "bbox" in hits["hits"][0]
    cube_id = next(h["id"] for h in hits["hits"] if h["rel_path"] == "dragones/dragon_cubo_v2.stl")
    info = call_tool(services, "model_info", {"id": cube_id})
    assert info["bbox"] == [20.0, 30.0, 40.0] and info["listing"] is None and info["root"]["id"] == root.id and len(info["dupes"]["exact"]) == 1
    assert call_tool(services, "model_info", {"path": "dragones/dragon_cubo_v2.stl"})["id"] == cube_id
    with pytest.raises(ValueError):
        call_tool(services, "model_info", {})
    saved = call_tool(services, "model_listing_set", {"id": cube_id, "title": "Cubo", "description": "Mide 20 × 30 × 40 mm.", "tags": ["Cubo", "cubo"]})
    assert saved["listing"]["listing_source"] == "assistant" and saved["listing"]["tags"] == ["cubo"]
    assert call_tool(services, "model_listing_get", {"id": cube_id})["listing"]["title"] == "Cubo"
    assert call_tool(services, "model_tag", {"id": cube_id, "add": ["Geometría"]})["tags"] == ["geometría"]
    assert call_tool(services, "model_note", {"id": cube_id, "notes": "primera"})["notes"] == "primera"
    assert call_tool(services, "model_note", {"id": cube_id, "notes": "segunda", "append": True})["notes"] == "primera\nsegunda"
    stats = call_tool(services, "models_stats", {})
    assert stats["counts"]["models"] == 9 and stats["counts"]["listings"] == 1 and stats["scanning"] is False
    assert call_tool(services, "models_dupes", {"kind": "exact"})["count"] == 1
    assert call_tool(services, "models_dupes", {"kind": "near"})["count"] >= 2
    recent = call_tool(services, "models_recent", {"n": 3})
    assert recent["count"] == 3 and all("file_modified_at" in h for h in recent["hits"])
    assert call_tool(services, "models_rescan", {"root_id": root.id})["ok"] is True
    assert services.worker.wait_idle(120)
    assert call_tool(services, "models_rescan", {})["queued"] == [root.id]
    assert services.worker.wait_idle(120)
    same_root = call_tool(services, "models_add_root", {"path": str(root.path)})
    assert same_root["root"]["id"] == root.id
    with pytest.raises(ValueError):
        call_tool(services, "models_add_root", {"path": "/definitely/not/here"})
    with pytest.raises(LookupError):
        call_tool(services, "model_listing_get", {"id": 999999})
    with pytest.raises(KeyError):
        call_tool(services, "nope", {})
