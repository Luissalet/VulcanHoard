"""Per-folder marketplace listings (cults3d.json): validator, store round trip, drafting, export, tools, API."""

import json
from pathlib import Path

import pytest

from vulcan.agent_tools import TOOLS, call_tool
from vulcan.folder_listings import load_template, validate_listing

GOOD_TAGS = [f"tag{i}" for i in range(20)]
GOOD_DESCRIPTION = "A silhouette pack for 3D printing, ready for one-color plates. " * 4  # > 200 chars


class FakeChatResult:
    def __init__(self, text):
        self.text = text


class FakeSync:
    def __init__(self, text):
        self._text = text

    def chat(self, *args, **kwargs):
        return FakeChatResult(self._text)


class FakeLink:
    """Stands in for hoard_link.Link in tests: only `.sync.chat(...)` is used by draft_one."""

    def __init__(self, text):
        self.sync = FakeSync(text)


# ---------------- validator ----------------


def test_validate_listing_format_and_lengths():
    assert validate_listing({"title": "T", "description": GOOD_DESCRIPTION, "tags": GOOD_TAGS}) == []
    assert any("Missing key" in i for i in validate_listing({"title": "T"}))
    assert any("Unexpected key" in i for i in validate_listing({"title": "T", "description": GOOD_DESCRIPTION, "tags": GOOD_TAGS, "extra": 1}))
    assert any("title is empty" in i for i in validate_listing({"title": "  ", "description": GOOD_DESCRIPTION, "tags": GOOD_TAGS}))
    assert any("longer than" in i for i in validate_listing({"title": "x" * 121, "description": GOOD_DESCRIPTION, "tags": GOOD_TAGS}))
    assert any("shorter than" in i for i in validate_listing({"title": "T", "description": "too short", "tags": GOOD_TAGS}))
    assert any("exactly 20" in i for i in validate_listing({"title": "T", "description": GOOD_DESCRIPTION, "tags": GOOD_TAGS[:5]}))
    dup_tags = GOOD_TAGS[:19] + [GOOD_TAGS[0]]
    assert any("exactly 20" in i for i in validate_listing({"title": "T", "description": GOOD_DESCRIPTION, "tags": dup_tags}))
    assert validate_listing("not a dict") == ["The listing must be a JSON object."]


def test_validate_listing_duplicate_titles_and_template():
    tags = GOOD_TAGS[:19] + ["pokemon"]
    data = {"title": "Pikachu pack", "description": GOOD_DESCRIPTION, "tags": tags}
    assert validate_listing(data, existing_titles={"pikachu pack": "other/folder"}, self_key="this/folder") != []
    assert validate_listing(data, existing_titles={"pikachu pack": "this/folder"}, self_key="this/folder") == []
    template = {"required_tags": ["pokemon"], "forbidden_words": ["banned"], "title_patterns": [r"^Pikachu"]}
    assert validate_listing(data, template=template) == []
    assert any("title_patterns" in i for i in validate_listing({**data, "title": "Something else"}, template=template))
    assert any("forbidden word" in i for i in validate_listing({**data, "description": GOOD_DESCRIPTION + " banned word here"}, template=template))
    assert any("required tag" in i for i in validate_listing({**data, "tags": GOOD_TAGS}, template=template))
    assert validate_listing(data, template={}) == []  # every template key optional


def test_load_template(tmp_path):
    assert load_template(str(tmp_path)) == {}
    (tmp_path / "cults3d_template.json").write_text(json.dumps({"required_tags": ["x"]}), encoding="utf-8")
    assert load_template(str(tmp_path)) == {"required_tags": ["x"]}


# ---------------- store: sync from scan, set/write-back, check ----------------


def test_sync_from_scan_creates_rows_for_every_folder_with_models(scanned):
    services, root = scanned
    rows = services.folder_listings.list(root_id=root.id)
    assert {r["rel_path"] for r in rows} == {"", "dragones", "soportes"}
    assert all(r["status"] == "none" for r in rows)


def test_set_writes_db_and_file_with_one_time_backup(scanned, library):
    services, root = scanned
    listing = services.folder_listings.set(root.id, "dragones", {"title": "Dragon pack", "description": GOOD_DESCRIPTION, "tags": GOOD_TAGS}, status="draft")
    assert listing["status"] == "draft" and listing["title"] == "Dragon pack" and listing["tags"] == GOOD_TAGS
    path = library / "dragones" / "cults3d.json"
    backup = library / "dragones" / "cults3d.json.bak"
    assert path.is_file()
    assert json.loads(path.read_text(encoding="utf-8"))["title"] == "Dragon pack"
    assert not backup.is_file()  # nothing to back up yet
    services.folder_listings.set(root.id, "dragones", {"title": "Dragon pack v2"}, status="draft")
    assert backup.is_file() and json.loads(backup.read_text(encoding="utf-8"))["title"] == "Dragon pack"
    services.folder_listings.set(root.id, "dragones", {"title": "Dragon pack v3"}, status="draft")
    assert json.loads(backup.read_text(encoding="utf-8"))["title"] == "Dragon pack"  # kept once, never overwritten again
    assert json.loads(path.read_text(encoding="utf-8"))["title"] == "Dragon pack v3"
    with pytest.raises(LookupError):
        services.folder_listings.set(999999, "x", {"title": "x"})


def test_check_marks_approved_or_checked_with_issues(scanned):
    services, root = scanned
    services.folder_listings.set(root.id, "soportes", {"title": "Support pack", "description": GOOD_DESCRIPTION, "tags": GOOD_TAGS}, status="draft")
    checked = services.folder_listings.check(root.id, "soportes")
    assert checked["status"] == "approved" and checked["issues"] == [] and checked["checked_at"]
    services.folder_listings.set(root.id, "soportes", {"description": "too short"}, status="draft")
    checked2 = services.folder_listings.check(root.id, "soportes")
    assert checked2["status"] == "checked" and checked2["issues"]
    assert services.folder_listings.check_all(root_id=root.id)
    with pytest.raises(LookupError):
        services.folder_listings.check(root.id, "does-not-exist")


def test_import_from_disk_on_rescan(scanned, library):
    services, root = scanned
    (library / "soportes" / "cults3d.json").write_text(json.dumps({"title": "Imported", "description": GOOD_DESCRIPTION, "tags": GOOD_TAGS}), encoding="utf-8")
    (library / "dragones" / "cults3d.json").write_text(json.dumps({"title": "Bad", "description": "short"}), encoding="utf-8")
    assert services.rescan(root.id) and services.worker.wait_idle(120)
    good = services.folder_listings.get(root.id, "soportes")
    bad = services.folder_listings.get(root.id, "dragones")
    assert good["status"] == "approved" and good["title"] == "Imported" and good["issues"] == []
    assert bad["status"] == "checked" and bad["issues"]


# ---------------- drafting ----------------


def test_draft_one_with_fake_link(scanned):
    services, root = scanned
    text = json.dumps({"title": "Cube pack", "description": GOOD_DESCRIPTION, "tags": GOOD_TAGS})
    result = services.folder_listings.draft_one(root.id, "dragones", FakeLink(text))
    assert result["ok"] is True and result["listing"]["status"] == "draft" and result["listing"]["title"] == "Cube pack"


def test_draft_one_without_backend_returns_material(scanned):
    services, root = scanned
    result = services.folder_listings.draft_one(root.id, "soportes", None)
    assert result["ok"] is False and result["material"]["part_count"] >= 1 and "material" in result


def test_draft_one_bad_json_never_raises(scanned):
    services, root = scanned
    result = services.folder_listings.draft_one(root.id, "dragones", FakeLink("not json at all"))
    assert result["ok"] is False and "material" in result


def test_draft_one_respects_overwrite(scanned):
    services, root = scanned
    services.folder_listings.set(root.id, "dragones", {"title": "Existing", "description": GOOD_DESCRIPTION, "tags": GOOD_TAGS}, status="approved")
    blocked = services.folder_listings.draft_one(root.id, "dragones", FakeLink("{}"))
    assert blocked["ok"] is False and "note" in blocked
    text = json.dumps({"title": "Redrafted", "description": GOOD_DESCRIPTION, "tags": GOOD_TAGS})
    redrafted = services.folder_listings.draft_one(root.id, "dragones", FakeLink(text), overwrite=True)
    assert redrafted["ok"] is True and redrafted["listing"]["title"] == "Redrafted"


def test_draft_worker_batch(scanned):
    services, root = scanned
    text = json.dumps({"title": "Batch pack", "description": GOOD_DESCRIPTION, "tags": GOOD_TAGS})
    services.link = FakeLink(text)
    targets = services.folder_listings.match_targets(root.id, "", 10, False)
    assert set(targets) == {(root.id, ""), (root.id, "dragones"), (root.id, "soportes")}
    services.draft_worker.start(targets, False)
    assert services.draft_worker.wait_idle(30)
    status = services.draft_worker.status()
    assert status["phase"] == "done" and status["drafted"] == 3 and status["errors"] == []


def test_draft_worker_rejects_overlapping_batches(scanned):
    services, root = scanned

    class SlowLink:
        class sync:
            @staticmethod
            def chat(*args, **kwargs):
                import time as _time

                _time.sleep(0.3)
                return FakeChatResult(json.dumps({"title": "Slow pack", "description": GOOD_DESCRIPTION, "tags": GOOD_TAGS}))

    services.link = SlowLink()
    targets = services.folder_listings.match_targets(root.id, "", 10, False)
    services.draft_worker.start(targets, False)
    with pytest.raises(RuntimeError):
        services.draft_worker.start(targets, False)
    assert services.draft_worker.wait_idle(30)


# ---------------- export ----------------


def test_export_csv_md_json(scanned):
    services, root = scanned
    services.folder_listings.set(root.id, "dragones", {"title": "Dragon pack", "description": GOOD_DESCRIPTION, "tags": GOOD_TAGS}, status="approved")
    csv_result = services.folder_listings.export(root.id, "csv")
    assert Path(csv_result["path"]).is_file() and csv_result["count"] == 3 and "Dragon pack" in csv_result["preview"]
    md_result = services.folder_listings.export(root.id, "md", status="approved")
    assert md_result["count"] == 1 and "Dragon pack" in md_result["preview"]
    json_result = services.folder_listings.export(root.id, "json")
    assert json.loads(Path(json_result["path"]).read_text(encoding="utf-8"))
    with pytest.raises(ValueError):
        services.folder_listings.export(root.id, "xml")
    with pytest.raises(LookupError):
        services.folder_listings.export(999999, "csv")


# ---------------- agent tools ----------------


def test_agent_tools_folder_listings(scanned):
    services, root = scanned
    names = [t.name for t in TOOLS]
    for name in ("folder_listings", "folder_listing_get", "folder_listing_set", "folder_listing_check", "folder_listing_draft", "folder_listings_export"):
        assert name in names
    for tool in TOOLS:
        if tool.name.startswith("folder_listing"):
            assert "Sinónimos:" in tool.description and len(tool.description.split("\n")[0]) <= 110

    listed = call_tool(services, "folder_listings", {"root_id": root.id})
    assert listed["count"] == 3 and listed["listings"][0]["status"] == "none"

    stub = call_tool(services, "folder_listing_get", {"root_id": root.id, "path": "dragones"})
    assert stub["listing"]["status"] == "none"

    with pytest.raises(LookupError):
        call_tool(services, "folder_listing_get", {"root_id": root.id, "path": "not-a-real-folder"})

    saved = call_tool(services, "folder_listing_set", {
        "root_id": root.id, "path": "dragones", "title": "Dragon pack", "description": GOOD_DESCRIPTION, "tags": GOOD_TAGS, "status": "draft",
    })
    assert saved["ok"] is True and saved["listing"]["status"] == "draft"

    fetched = call_tool(services, "folder_listing_get", {"root_id": root.id, "path": "dragones"})
    assert fetched["listing"]["title"] == "Dragon pack"

    checked = call_tool(services, "folder_listing_check", {"root_id": root.id, "path": "dragones"})
    assert checked["ok"] is True and checked["listing"]["status"] == "approved"

    checked_all = call_tool(services, "folder_listing_check", {"root_id": root.id})
    assert checked_all["checked"] >= 1

    services.link = FakeLink(json.dumps({"title": "Soportes pack", "description": GOOD_DESCRIPTION, "tags": GOOD_TAGS}))
    drafted = call_tool(services, "folder_listing_draft", {"root_id": root.id, "path": "soportes", "limit": 5})
    assert drafted["ok"] is True and drafted["queued"] == 1
    assert services.draft_worker.wait_idle(30)

    exported = call_tool(services, "folder_listings_export", {"root_id": root.id, "format": "csv"})
    assert exported["ok"] is True and Path(exported["path"]).is_file()

    absolute = call_tool(services, "folder_listing_get", {"path": str(Path(root.path) / "dragones")})
    assert absolute["listing"]["title"] == "Dragon pack"
    with pytest.raises(ValueError):
        call_tool(services, "folder_listing_get", {"path": "/definitely/not/under/any/root"})
    with pytest.raises(ValueError):
        call_tool(services, "folder_listing_get", {"path": ""})


# ---------------- REST API ----------------


def _scan(client, library):
    created = client.post("/api/roots", json={"path": str(library), "name": "Pruebas"})
    assert created.status_code == 201, created.text
    assert client.services.worker.wait_idle(120)
    return created.json()


def test_folder_listings_api(client, library):
    root = _scan(client, library)

    listed = client.get("/api/folder-listings", params={"root_id": root["id"]}).json()
    assert len(listed["listings"]) == 3

    put = client.put(f"/api/folder-listings/{root['id']}/dragones", json={
        "title": "Dragon pack", "description": GOOD_DESCRIPTION, "tags": GOOD_TAGS, "status": "draft",
    })
    assert put.status_code == 200 and put.json()["status"] == "draft"

    got = client.get(f"/api/folder-listings/{root['id']}/dragones").json()
    assert got["title"] == "Dragon pack"

    checked = client.post(f"/api/folder-listings/{root['id']}/dragones/check").json()
    assert checked["status"] == "approved"

    checked_all = client.post("/api/folder-listings/check-all", params={"root_id": root["id"]}).json()
    assert len(checked_all["listings"]) >= 1

    client.services.link = FakeLink(json.dumps({"title": "Soportes pack", "description": GOOD_DESCRIPTION, "tags": GOOD_TAGS}))
    draft = client.post("/api/folder-listings/draft", json={"root_id": root["id"], "path": "soportes", "limit": 5}).json()
    assert draft["ok"] is True and draft["queued"] == 1
    assert client.services.draft_worker.wait_idle(30)
    progress = client.get("/api/folder-listings/draft/progress").json()
    assert progress["phase"] == "done"

    exported = client.get("/api/folder-listings/export", params={"root_id": root["id"], "format": "csv"}).json()
    assert exported["count"] >= 1

    assert client.get(f"/api/folder-listings/{root['id']}/does-not-exist").status_code == 404
    assert client.put(f"/api/folder-listings/999999/dragones", json={"title": "x"}).status_code == 404
    assert client.post(f"/api/folder-listings/{root['id']}/does-not-exist/check").status_code == 404
    assert client.get("/api/folder-listings/export", params={"root_id": root["id"], "format": "xml"}).status_code == 400
