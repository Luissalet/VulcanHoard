"""The family assistant panel: relay routes, launcher injection, manifest entry, the client contract and the partial saves it relies on."""

import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def test_copilot_info_answers_and_names_the_app(client):
    response = client.get("/api/copilot/info")
    assert response.status_code in (200, 503)  # 503 while the assistant service cannot be reached
    if response.status_code == 503:
        return
    body = response.json()
    assert body["app_id"] == "vulcan"
    assert body["app_name"]
    assert body["panel_url"] == "/api/copilot/panel.js"
    assert body["data_space_id"]


def test_html_carries_the_launcher_and_the_routes_exist(client):
    page = client.get("/")
    if page.status_code == 200:  # 503 only when the client bundle has not been built
        assert '<script type="module" src="/api/copilot/launcher.js">' in page.text
    launcher = client.get("/api/copilot/launcher.js")
    assert launcher.status_code == 200 and "hoardCopilot" in launcher.text
    panel = client.get("/api/copilot/panel.js")
    # the script comes from the assistant service: served when it is up, a clean 5xx (never a 404) when it is not
    assert panel.status_code in (200, 502, 503)
    if panel.status_code == 200:
        assert "javascript" in panel.headers["content-type"]


def test_manifest_declares_the_copilot_contract_and_keeps_the_family_flags():
    manifest = json.loads((ROOT / "faustus-plugin.json").read_text(encoding="utf-8"))
    family = manifest["x-family"]
    assert family["agent_contract"] is True and family["events"] is True and family["stack"] == "python"
    copilot = family["copilot"]
    assert copilot["provider"] == "faustus" and copilot["contract"] == "1.0"
    assert copilot["panel"] == "/api/copilot/panel.js"
    assert {"model", "listing", "folder_listing", "root", "album", "organize_plan", "editor"} <= set(copilot["context"])


def test_client_registers_the_contract_once_from_the_entry():
    main = (ROOT / "client" / "src" / "main.jsx").read_text(encoding="utf-8")
    assert "installCopilot()" in main
    module = (ROOT / "client" / "src" / "copilot-context.js").read_text(encoding="utf-8")
    assert re.search(r"hoardCopilot\s*=\s*\{\s*context,\s*onTurnEnd\s*\}", module)
    assert "hoard:copilot-turn-end" in module


def _scan(client, library):
    created = client.post("/api/roots", json={"path": str(library), "name": "Pruebas"})
    assert created.status_code == 201, created.text
    assert client.services.worker.wait_idle(120)
    return created.json()


def test_model_listing_edits_are_patches(client, library):
    """The model page sends only the fields the user changed, so the listing endpoint must keep merging the omitted ones."""
    _scan(client, library)
    model_id = client.get("/api/models").json()["models"][0]["id"]
    first = client.put(f"/api/models/{model_id}/listing", json={"title": "Dragon", "description": "A dragon.", "category": "figures", "source": "assistant"})
    assert first.status_code == 200
    second = client.put(f"/api/models/{model_id}/listing", json={"price_hint": "3-5", "source": "manual"}).json()["listing"]
    assert second["title"] == "Dragon" and second["description"] == "A dragon." and second["category"] == "figures"
    assert second["price_hint"] == "3-5"
    cleared = client.put(f"/api/models/{model_id}/listing", json={"category": "", "source": "manual"}).json()["listing"]
    assert cleared["category"] == "" and cleared["title"] == "Dragon"  # an empty string clears one field, nothing else moves


def test_folder_listing_edits_are_patches(client, library):
    """The listings page sends only the changed fields plus the status the button asks for."""
    root = _scan(client, library)
    path = f"/api/folder-listings/{root['id']}/dragones"
    full = client.put(path, json={"title": "Dragon pack", "description": "x" * 250, "tags": ["dragon", "stl"], "status": "draft"})
    assert full.status_code == 200, full.text
    only_status = client.put(path, json={"status": "checked"}).json()
    assert only_status["title"] == "Dragon pack" and len(only_status["description"]) == 250 and only_status["tags"] == ["dragon", "stl"]
    only_title = client.put(path, json={"title": "Dragon pack II", "status": "draft"}).json()
    assert only_title["title"] == "Dragon pack II" and len(only_title["description"]) == 250 and only_title["tags"] == ["dragon", "stl"]


def test_album_rename_is_a_patch(client, library):
    _scan(client, library)
    model_ids = [m["id"] for m in client.get("/api/models").json()["models"][:2]]
    album = client.post("/api/collections", json={"name": "Spring", "model_ids": model_ids}).json()
    renamed = client.patch(f"/api/collections/{album['id']}", json={"name": "Summer"})
    assert renamed.status_code == 200
    albums = {a["id"]: a for a in client.get("/api/collections").json()["albums"]}
    assert albums[album["id"]]["name"] == "Summer" and albums[album["id"]]["count"] == len(model_ids)


def test_docs_list_the_copilot_routes():
    for name in ("README.md", "README.es.md"):
        text = (ROOT / name).read_text(encoding="utf-8")
        for route in ("/api/copilot/info", "/api/copilot/panel.js", "/api/copilot/proxy", "/api/copilot/launcher.js", "window.hoardCopilot.context()", "HOARD_COPILOT=0"):
            assert route in text, (name, route)
