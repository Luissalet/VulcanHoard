"""sheets_batch: skeleton drafts for every folder or model of a root, without a language model."""

import json

import pytest

from vulcan.agent_tools import call_tool
from vulcan.sheets_batch import build_sheet, clean_name, run_sheets_batch

GOOD_TAGS = [f"tag{i}" for i in range(20)]
GOOD_DESCRIPTION = "A silhouette pack for 3D printing, ready for one-color plates. " * 4


class NoModel:
    """Any use of the model backend fails the test: the skeleton mode must never touch it."""

    def __getattr__(self, name):
        raise AssertionError(f"the model backend was used ({name})")


class FakeLink:
    class sync:
        @staticmethod
        def chat(*args, **kwargs):
            class Result:
                text = json.dumps({"title": "Model pack", "description": GOOD_DESCRIPTION, "tags": GOOD_TAGS})
            return Result()


def row(name, fmt="stl", bbox=(10.0, 20.0, 30.0), triangles=12, watertight=1):
    data = {"name": name, "format": fmt, "bbox_x": bbox[0] if bbox else None, "bbox_y": bbox[1] if bbox else None,
            "bbox_z": bbox[2] if bbox else None, "triangles": triangles, "watertight": watertight}
    return data


def test_clean_name_and_build_sheet_use_only_measured_facts():
    assert clean_name("025 pikachu_front") == "Pikachu Front" and clean_name("Mr. Mime") == "Mr. Mime" and clean_name("1-3") == "1-3"
    sheet = build_sheet("Dragon Pack", [row("Dragon body"), row("Dragon wing", "3mf", (5, 5, 40.6), 100, 0), row("Broken", bbox=None, triangles=None, watertight=None)])
    assert sheet["title"] == "Dragon Pack - 3D Print 3MF & STL Files"
    assert len(sheet["description"]) >= 200 and "3 printable files" in sheet["description"] and "40.6 mm" in sheet["description"]
    assert "1 of 2 measured meshes are watertight" in sheet["description"] and "- Dragon body (STL, 10 x 20 x 30 mm)" in sheet["description"]
    assert "- Broken (STL)" in sheet["description"]
    assert sheet["tags"][:3] == ["dragon", "pack", "dragon pack"] and len(set(sheet["tags"])) == len(sheet["tags"]) <= 20
    assert all(t == t.lower() for t in sheet["tags"])
    template = {"required_tags": ["Pokemon"], "base_tags": [f"base{i}" for i in range(30)], "forbidden_words": ["base1"]}
    shaped = build_sheet("Dragon Pack", [row("a")], template)
    assert len(shaped["tags"]) == 20 and "pokemon" in shaped["tags"] and not any("base1" in t for t in shaped["tags"])
    many = build_sheet("Big", [row(f"part{i}") for i in range(40)])
    assert "... and 25 more files." in many["description"] and len(many["title"]) <= 120
    spanish = build_sheet("Drag\u00f3n", [row("a")], language="es")
    assert "archivo imprimible" in spanish["description"] and "mide" in spanish["description"]


def test_skeleton_batch_creates_refreshes_and_protects(scanned, library):
    services, root = scanned
    services.link = NoModel()
    dry = run_sheets_batch(services, root.id, dry_run=True)
    assert dry["counts"] == {"created": 3, "refreshed": 0, "skipped": 0, "queued": 0} and dry["dry_run"] is True
    assert not (library / "dragones" / "cults3d.json").exists() and services.folder_listings.get(root.id, "dragones")["status"] == "none"

    first = run_sheets_batch(services, root.id)
    assert first["counts"]["created"] == 3 and set(first["created"]) == {"(root)", "dragones", "soportes"}
    listing = services.folder_listings.get(root.id, "dragones")
    assert listing["status"] == "draft" and listing["title"].startswith("Dragones - 3D Print") and len(listing["description"]) >= 200
    on_disk = json.loads((library / "dragones" / "cults3d.json").read_text(encoding="utf-8"))
    assert set(on_disk) == {"title", "description", "tags"} and on_disk["title"] == listing["title"]
    assert "dragon_cubo_v2" in listing["description"] or "Dragon cubo v2" in listing["description"]

    again = run_sheets_batch(services, root.id)
    assert again["counts"] == {"created": 0, "refreshed": 0, "skipped": 3, "queued": 0} and all("draft_exists" in s["reason"] for s in again["skipped"])
    refreshed = run_sheets_batch(services, root.id, refresh=True)
    assert refreshed["counts"]["refreshed"] == 3 and refreshed["counts"]["created"] == 0

    services.folder_listings.set(root.id, "soportes", {"title": "Hand written", "description": GOOD_DESCRIPTION, "tags": GOOD_TAGS}, status="approved")
    protected = run_sheets_batch(services, root.id, refresh=True)
    assert [s["path"] for s in protected["skipped"]] == ["soportes"] and "approved_protected" in protected["skipped"][0]["reason"]
    assert services.folder_listings.get(root.id, "soportes")["title"] == "Hand written"
    forced = run_sheets_batch(services, root.id, overwrite=True)
    assert forced["counts"]["refreshed"] == 3 and services.folder_listings.get(root.id, "soportes")["title"] != "Hand written"


def test_skeleton_with_template_passes_the_validator(scanned, library):
    services, root = scanned
    (library / "cults3d_template.json").write_text(json.dumps({"base_tags": [f"base{i}" for i in range(12)]}), encoding="utf-8")
    run_sheets_batch(services, root.id, path="dragones")
    checked = services.folder_listings.check(root.id, "dragones")
    assert checked["issues"] == [] and checked["status"] == "approved" and len(checked["tags"]) == 20


def test_path_filter_and_limit(scanned):
    services, root = scanned
    limited = run_sheets_batch(services, root.id, limit=2)
    assert limited["created"] == ["(root)", "dragones"] and limited["counts"]["skipped"] == 1 and "limit_reached" in limited["skipped"][0]["reason"]
    globbed = run_sheets_batch(services, root.id, path="sop*")
    assert globbed["created"] == ["soportes"] and globbed["counts"]["skipped"] == 0


def test_path_prefix_covers_subfolders_and_errors(scanned):
    services, root = scanned
    only = run_sheets_batch(services, root.id, path="dragones")
    assert only["created"] == ["dragones"] and services.folder_listings.get(root.id, "soportes")["status"] == "none"
    with pytest.raises(LookupError):
        run_sheets_batch(services, 999999)
    with pytest.raises(ValueError):
        run_sheets_batch(services, root.id, scope="everything")
    with pytest.raises(ValueError, match="only drafts folder sheets"):
        run_sheets_batch(services, root.id, scope="models", mode="model")
    services.worker.status = lambda: {"current": root.id, "queued": []}
    with pytest.raises(ValueError, match="scan"):
        run_sheets_batch(services, root.id)


def test_models_scope_never_replaces_manual_listings(scanned):
    services, root = scanned
    services.link = NoModel()
    with services.db.lock:
        ids = [r["id"] for r in services.db.conn.execute("SELECT id FROM models WHERE root_id = ? ORDER BY id", (root.id,))]
    services.listings.set(ids[0], {"title": "Mine", "description": "manual text", "tags": ["x"]}, source="manual")
    result = run_sheets_batch(services, root.id, scope="models", language="es")
    assert result["counts"]["created"] == len(ids) - 1 and result["counts"]["skipped"] == 1 and "manual_listing_protected" in result["skipped"][0]["reason"]
    assert services.listings.get(ids[0])["title"] == "Mine"
    made = services.listings.get(ids[1])
    assert made["listing_source"] == "assistant" and made["language"] == "es" and "Archivos" in made["title"]
    again = run_sheets_batch(services, root.id, scope="models")
    assert again["counts"]["created"] == 0 and again["counts"]["skipped"] == len(ids)
    assert run_sheets_batch(services, root.id, scope="models", refresh=True)["counts"]["refreshed"] == len(ids) - 1
    assert run_sheets_batch(services, root.id, scope="models", overwrite=True)["counts"]["refreshed"] == len(ids)


def test_model_mode_queues_the_background_drafting(scanned):
    services, root = scanned
    services.link = FakeLink()
    dry = run_sheets_batch(services, root.id, mode="model", dry_run=True)
    assert dry["queued"] == 3 and services.draft_worker.status()["phase"] == "idle"
    result = run_sheets_batch(services, root.id, mode="model")
    assert result["queued"] == 3 and result["counts"]["created"] == 0 and result["progress"]["total"] == 3
    assert services.draft_worker.wait_idle(30)
    assert services.draft_worker.status()["drafted"] == 3
    assert services.folder_listings.get(root.id, "dragones")["title"] == "Model pack"


def test_sheets_batch_tool_and_http(scanned, library):
    services, root = scanned
    by_path = call_tool(services, "sheets_batch", {"path": str(library / "dragones")})
    assert by_path["counts"]["created"] == 1 and by_path["created"] == ["dragones"]
    with pytest.raises(ValueError):
        call_tool(services, "sheets_batch", {})
    whole = call_tool(services, "sheets_batch", {"root_id": root.id, "dry_run": True})
    assert whole["counts"]["created"] == 2 and whole["counts"]["skipped"] == 1


def test_sheets_batch_http_endpoint(client, library):
    created = client.post("/api/roots", json={"path": str(library), "name": "Pruebas"}).json()
    assert client.services.worker.wait_idle(120)
    ok = client.post("/api/organize/sheets-batch", json={"root_id": created["id"]})
    assert ok.status_code == 200 and ok.json()["counts"]["created"] == 3
    assert client.post("/api/organize/sheets-batch", json={"root_id": 999999}).status_code == 404
    assert client.post("/api/organize/sheets-batch", json={}).status_code == 400
    assert client.post("/api/organize/sheets-batch", json={"root_id": created["id"], "scope": "x"}).status_code == 400