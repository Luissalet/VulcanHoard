"""HTTP API through TestClient: roots, models, thumb, file with Range, listing, search, dupes, collections, stats, agent auth."""

import time

from fixtures import EXPECTED_MODELS


def scan(client, library, name="Pruebas"):
    created = client.post("/api/roots", json={"path": str(library), "name": name})
    assert created.status_code == 201, created.text
    assert client.services.worker.wait_idle(120)
    return created.json()


def test_health_and_status(client):
    health = client.get("/api/health").json()
    assert health["service"] == "vulcan-hoard" and health["dataDirConfigured"] is True and health["version"]
    status = client.get("/api/status").json()
    assert status["counts"]["models"] == 0 and status["worker"]["running"] is True and status["thumbnails"] is True
    assert client.get("/api/nothing").status_code == 404
    assert client.get("/").status_code in (200, 503)


def test_roots_crud_and_validation(client, library):
    assert client.post("/api/roots", json={"path": "/no/such/folder"}).status_code == 400
    assert client.post("/api/roots", json={}).status_code == 400
    root = scan(client, library)
    assert root["name"] == "Pruebas" and root["include"] == ["**/*.stl", "**/*.3mf", "**/*.obj"]
    again = client.post("/api/roots", json={"path": str(library)})
    assert again.status_code == 201 and again.json()["id"] == root["id"]  # idempotent by path
    listed = client.get("/api/roots").json()["roots"]
    assert len(listed) == 1 and listed[0]["progress"]["phase"] == "done" and listed[0]["progress"]["files_total"] == EXPECTED_MODELS
    patched = client.patch(f"/api/roots/{root['id']}", json={"name": "Modelos", "watch": False}).json()
    assert patched["name"] == "Modelos"
    assert client.get(f"/api/roots/{root['id']}/progress").json()["progress"]["phase"] == "done"
    rescanned = client.post(f"/api/roots/{root['id']}/rescan").json()
    assert rescanned["ok"] is True
    assert client.services.worker.wait_idle(120)
    assert client.get("/api/roots/999").status_code == 404
    assert client.delete(f"/api/roots/{root['id']}").json()["ok"] is True
    assert client.get("/api/status").json()["counts"]["models"] == 0


def test_models_list_filters_and_detail(client, library):
    scan(client, library)
    page = client.get("/api/models", params={"limit": 4, "sort": "-triangles"}).json()
    assert page["total"] == EXPECTED_MODELS and len(page["models"]) == 4
    assert page["models"][0]["triangles"] >= page["models"][1]["triangles"]
    first = page["models"][0]
    assert {"id", "name", "format", "bbox", "triangles", "has_listing", "has_thumb", "tags", "collection"} <= set(first)
    assert client.get("/api/models", params={"sort": "sideways"}).status_code == 400
    assert client.get("/api/models", params={"format": "obj"}).json()["total"] == 1
    assert client.get("/api/models", params={"watertight": "false"}).json()["total"] == 1
    assert client.get("/api/models", params={"dupes": "true"}).json()["total"] == 2
    assert client.get("/api/models", params={"status": "error"}).json()["total"] == 1
    assert client.get("/api/search", params={"q": "cubo"}).json()["sort"] == "relevance"
    assert client.get("/api/models", params={"q": "cubo", "collection": "soportes"}).json()["total"] == 1
    facets = client.get("/api/models/facets").json()
    assert {f["format"] for f in facets["formats"]} == {"stl", "obj", "3mf"}
    detail = client.get(f"/api/models/{first['id']}").json()
    assert detail["listing"] is None and "dupes" in detail and detail["albums"] == [] and detail["sha256"]
    assert client.get("/api/models/999999").status_code == 404


def test_model_patch_listing_and_search_roundtrip(client, library):
    scan(client, library)
    model = client.get("/api/models", params={"q": "pulgadas"}).json()["models"][0]
    assert model["units_guess"] == "inches"
    patched = client.patch(f"/api/models/{model['id']}", json={"name": "Pieza pequeña", "tags": ["Prueba", "prueba"], "notes": "medir antes de imprimir"}).json()
    assert patched["name"] == "Pieza pequeña" and patched["tags"] == ["prueba"]
    assert client.patch(f"/api/models/{model['id']}", json={"name": ""}).status_code == 400
    assert client.get("/api/models", params={"tag": "prueba"}).json()["total"] == 1
    assert client.get(f"/api/models/{model['id']}/listing").json()["listing"] is None
    saved = client.put(f"/api/models/{model['id']}/listing", json={"title": "Pieza", "description": "Texto **markdown**", "tags": ["Taller"], "category": "Herramientas", "source": "assistant"}).json()["listing"]
    assert saved["listing_source"] == "assistant" and saved["tags"] == ["taller"]
    assert client.put(f"/api/models/{model['id']}/listing", json={"source": "robot"}).status_code == 400
    assert client.get("/api/models", params={"q": "markdown"}).json()["total"] == 1
    assert client.get("/api/models", params={"has_listing": "true"}).json()["total"] == 1
    assert client.get(f"/api/models/{model['id']}").json()["listing"]["title"] == "Pieza"
    assert client.delete(f"/api/models/{model['id']}/listing").json()["ok"] is True
    assert client.get("/api/models", params={"has_listing": "true"}).json()["total"] == 0
    assert client.put("/api/models/999999/listing", json={"title": "x"}).status_code == 404


def test_thumb_and_file_with_range(client, library):
    scan(client, library)
    ok = client.get("/api/models", params={"status": "ok", "format": "stl", "limit": 1}).json()["models"][0]
    thumb = client.get(f"/api/models/{ok['id']}/thumb")
    assert thumb.status_code == 200 and thumb.headers["content-type"] == "image/webp" and thumb.content[:4] == b"RIFF"
    broken = client.get("/api/models", params={"status": "error"}).json()["models"][0]
    assert client.get(f"/api/models/{broken['id']}/thumb").status_code == 204
    full = client.get(f"/api/models/{ok['id']}/file")
    assert full.status_code == 200 and len(full.content) == ok["size_bytes"] and full.headers["content-type"].startswith("model/stl")
    assert full.headers.get("accept-ranges") == "bytes" and "inline" in full.headers["content-disposition"]
    part = client.get(f"/api/models/{ok['id']}/file", headers={"Range": "bytes=0-79"})
    assert part.status_code == 206 and len(part.content) == 80 and part.headers["content-range"].startswith("bytes 0-79/")
    assert part.content == full.content[:80]
    (library / "dragones" / "dragon_cubo_v2.stl").unlink()
    gone = client.get("/api/models", params={"q": "v2"}).json()["models"][0]
    assert client.get(f"/api/models/{gone['id']}/file").status_code == 404


def test_dupes_collections_and_stats(client, library):
    scan(client, library)
    exact = client.get("/api/dupes", params={"kind": "exact"}).json()
    assert exact["count"] == 1 and len(exact["groups"][0]["models"]) == 2
    near = client.get("/api/dupes", params={"kind": "near"}).json()
    assert near["count"] >= 2
    assert client.get("/api/dupes", params={"kind": "fuzzy"}).status_code == 400
    collections = client.get("/api/collections").json()
    assert {f["name"] for f in collections["folders"]} == {"dragones", "soportes", "Pruebas"} and collections["albums"] == []
    ids = [m["id"] for m in client.get("/api/models", params={"collection": "dragones"}).json()["models"]]
    album = client.post("/api/collections", json={"name": "Dragones seleccionados", "model_ids": ids[:2]})
    assert album.status_code == 201 and album.json()["count"] == 2
    album_id = album.json()["id"]
    assert client.get("/api/models", params={"album": album_id}).json()["total"] == 2
    patched = client.patch(f"/api/collections/{album_id}", json={"add": [ids[2]], "remove": [ids[0]], "name": "Dragones"}).json()
    assert patched["count"] == 2 and patched["name"] == "Dragones"
    assert client.patch(f"/api/collections/{album_id}", json={"add": [999999]}).status_code == 404
    assert client.get(f"/api/models/{ids[1]}").json()["albums"] == [{"id": album_id, "name": "Dragones"}]
    assert client.delete(f"/api/collections/{album_id}").json()["ok"] is True
    assert client.get(f"/api/collections/{album_id}").status_code == 404
    stats = client.get("/api/stats").json()
    assert stats["models"] == EXPECTED_MODELS and stats["duplicates"] == 1 and stats["errors"] == 1 and stats["odd_units"] == 1
    assert {f["format"] for f in stats["by_format"]} == {"stl", "obj", "3mf"} and stats["by_root"][0]["models"] == EXPECTED_MODELS
    assert stats["triangles"] > 0 and stats["db_bytes"] > 0 and stats["thumbs_bytes"] > 0
    assert client.post("/api/maintenance/rebuild-fts").json()["models"] == EXPECTED_MODELS
    assert client.post("/api/maintenance/refresh-dupes").json()["duplicates"] == 1
    assert client.post("/api/maintenance/rescan-all").json()["queued"] == [stats["by_root"][0]["id"]]
    assert client.post("/api/maintenance/explode").status_code == 404
    assert client.services.worker.wait_idle(120)


def test_agent_endpoints_and_auth(client, library):
    scan(client, library)
    catalog = client.get("/api/agent/tools").json()
    assert [t["name"] for t in catalog["tools"]][:2] == ["models_search", "model_info"]
    assert "never invent" in catalog["instructions"].lower() or "Never invent" in catalog["instructions"]
    assert catalog["tools"][0]["inputSchema"]["type"] == "object"
    assert client.post("/api/agent/call", json={"name": "models_stats"}).status_code == 401
    assert client.post("/api/agent/call", json={"name": "models_stats"}, headers={"Authorization": "Bearer nope"}).status_code == 401
    auth = {"Authorization": f"Bearer {client.services.token}"}
    stats = client.post("/api/agent/call", json={"name": "models_stats"}, headers=auth).json()
    assert stats["counts"]["models"] == EXPECTED_MODELS
    assert client.post("/api/agent/call", json={"name": "unknown_tool"}, headers=auth).status_code == 404
    assert client.post("/api/agent/call", json={"name": "model_info", "arguments": {"id": "abc"}}, headers=auth).status_code == 400
    assert client.post("/api/agent/call", json={"name": "model_info", "arguments": {"id": 999999}}, headers=auth).status_code == 404
    assert client.post("/api/agent/call", json={"name": "models_add_root", "arguments": {"path": "/nope"}}, headers=auth).status_code == 400
    hit = client.post("/api/agent/call", json={"name": "models_search", "arguments": {"q": "esfera", "limit": 1}}, headers=auth).json()["hits"][0]
    listing = client.post("/api/agent/call", json={"name": "model_listing_set", "arguments": {"id": hit["id"], "title": "Esfera lisa", "tags": ["esfera"]}}, headers=auth).json()
    assert listing["listing"]["listing_source"] == "assistant"
    assert client.get(f"/api/models/{hit['id']}").json()["listing"]["title"] == "Esfera lisa"


def test_token_file_written_at_startup(client):
    token_path = client.services.config.token_path
    assert token_path.is_file() and token_path.read_text().strip() == client.services.token and len(client.services.token) == 64
    assert time.time() - token_path.stat().st_mtime < 600
