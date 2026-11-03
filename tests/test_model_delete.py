"""model_delete: the library entry always goes; Vulcan's own files go with it; a user's file is never deleted."""

from pathlib import Path

from vulcan import cad, cad_service
from vulcan.agent_tools import call_tool
from vulcan.search import Filters


def test_user_file_keeps_file_and_shared_thumbnail(scanned, library):
    services, root = scanned
    cube = services.models.by_path("dragones/dragon_cubo_v2.stl")
    services.listings.set(cube["id"], {"title": "Cubo"})
    thumb = Path(cube["thumb_path"])
    preview = call_tool(services, "model_delete", {"id": cube["id"], "dry_run": True})
    assert preview["dry_run"] and preview["file_kept"] and preview["files"] == [] and preview["thumbnail"] is None  # the exact copy shares it
    assert services.models.get(cube["id"]) is not None
    result = call_tool(services, "model_delete", {"id": cube["id"]})
    assert result["ok"] and result["file_kept"] and "never deletes your files" in result["note"] and "next scan" in result["note"]
    assert services.models.get(cube["id"]) is None and services.listings.get(cube["id"]) is None
    assert (library / "dragones" / "dragon_cubo_v2.stl").is_file() and thumb.is_file()
    assert services.models.by_path("soportes/copia_del_cubo.stl")["dupe_of"] is None  # duplicates recomputed
    # without exclude, the next scan lists it again
    assert services.rescan(root.id) and services.worker.wait_idle(120)
    again = services.models.by_path("dragones/dragon_cubo_v2.stl")
    assert again is not None and again["id"] != cube["id"]


def test_exclude_keeps_a_user_file_out_of_later_scans(scanned, library):
    services, root = scanned
    sphere = services.models.by_path("esfera_lisa.obj")
    thumb = Path(sphere["thumb_path"])
    result = call_tool(services, "model_delete", {"id": sphere["id"], "exclude": True})
    assert result["root_change"] == "excluded" and result["thumbnail"] == str(thumb) and not thumb.exists()
    assert "esfera_lisa.obj" in services.roots.get(root.id).exclude and (library / "esfera_lisa.obj").is_file()
    assert services.rescan(root.id) and services.worker.wait_idle(120)
    assert services.models.by_path("esfera_lisa.obj") is None
    assert services.search.query(Filters(q="esfera lisa"))["total"] == 0


def test_cad_part_files_and_import_root_are_removed(services):
    made = cad_service.get(services).from_text(spec=cad.EXAMPLE_SPEC, name="plate-hole-verify")
    model = services.models.get(made["id"])
    stl, spec, png = Path(made["stl_path"]), Path(made["spec_path"]), Path(made["check_png_path"])
    thumb = Path(model["thumb_path"])
    assert stl.is_file() and spec.is_file() and png.is_file() and thumb.is_file()
    import_root = services.roots.get(model["root_id"])
    assert import_root.imported
    result = call_tool(services, "model_delete", {"id": made["id"]})
    assert result["file_kept"] is False and sorted(result["files"]) == sorted(map(str, (stl, spec, png)))
    assert result["thumbnail"] == str(thumb) and result["root_change"] == "root_removed"
    assert not stl.exists() and not spec.exists() and not png.exists() and not thumb.exists()
    assert services.models.get(made["id"]) is None and services.roots.get(import_root.id) is None


def test_second_import_keeps_root_and_drops_only_its_entry(services):
    service = cad_service.get(services)
    first = service.from_text(spec=cad.EXAMPLE_SPEC, name="part one")
    second = service.from_text(spec=cad.EXAMPLE_SPEC, name="part two")
    root_id = services.models.get(first["id"])["root_id"]
    assert services.models.get(second["id"])["root_id"] == root_id
    result = call_tool(services, "model_delete", {"id": first["id"]})
    assert result["root_change"] == "import_entry_removed"
    root = services.roots.get(root_id)
    assert Path(first["stl_path"]).name not in root.include and Path(second["stl_path"]).name in root.include
    assert services.models.get(second["id"]) is not None and Path(second["stl_path"]).is_file()


def test_rest_delete_and_missing(client, library):
    root = client.post("/api/roots", json={"name": "Pruebas", "path": str(library)}).json()
    assert client.services.worker.wait_idle(120)
    model = client.services.models.by_path("esfera_lisa.obj")
    preview = client.delete(f"/api/models/{model['id']}", params={"dry_run": "true"}).json()
    assert preview["dry_run"] is True and preview["file_kept"] is True and client.get(f"/api/models/{model['id']}").status_code == 200
    done = client.delete(f"/api/models/{model['id']}").json()
    assert done["ok"] and done["root_id"] == root["id"]
    assert client.get(f"/api/models/{model['id']}").status_code == 404
    assert client.delete(f"/api/models/{model['id']}").status_code == 404
