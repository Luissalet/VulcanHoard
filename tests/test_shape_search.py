"""Shape retrieval finds a remeshed piece that triangle-count dupes miss."""

import numpy as np
import trimesh

from fixtures import cube
from vulcan.agent_tools import call_tool


def test_remeshed_rotated_piece_is_ranked_as_similar(scanned, library):
    services, root = scanned
    source = services.models.by_path("dragones/dragon_cubo_v2.stl")
    remesh = cube().subdivide()
    remesh.apply_transform(trimesh.transformations.rotation_matrix(np.pi / 2, [0, 0, 1]))
    remesh.export(library / "soportes" / "cubo_remallado.stl")
    assert services.rescan(root.id) and services.worker.wait_idle(120)
    candidate = services.models.by_path("soportes/cubo_remallado.stl")
    assert candidate["triangles"] != source["triangles"]
    assert candidate["id"] not in {m["id"] for m in services.dupes.for_model(source)["near"]}

    result = call_tool(services, "models_similar", {"id": source["id"]})
    match = next(row for row in result["matches"] if row["model"]["id"] == candidate["id"])
    assert match["score"] >= 0.8
    assert all(row["model"]["id"] != source["id"] for row in result["matches"])


def test_open_mesh_has_no_filled_shape_matches(scanned):
    services, _ = scanned
    open_model = services.models.by_path("soportes/caja_abierta.stl")
    result = call_tool(services, "models_similar", {"id": open_model["id"]})
    assert result["matches"] == []


def test_similar_shape_http_route_returns_ranked_models(client, library):
    created = client.post("/api/roots", json={"path": str(library), "name": "Pruebas"})
    assert created.status_code == 201
    assert client.services.worker.wait_idle(120)
    source = client.services.models.by_path("dragones/dragon_cubo_v2.stl")
    response = client.get(f"/api/models/{source['id']}/similar")
    assert response.status_code == 200
    body = response.json()
    assert body["count"] >= 1
    assert body["matches"][0]["score"] >= 0.75
    assert client.get("/api/models/999999/similar").status_code == 404
