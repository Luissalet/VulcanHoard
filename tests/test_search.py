"""FTS5 search over name, tags, notes, collection and listing text; filters; sorting; facets."""

from fixtures import EXPECTED_MODELS

from vulcan.search import Filters, fts_query


def test_fts_query_builder():
    assert fts_query("dragón cubo") == '"dragón"* AND "cubo"*'
    assert fts_query('  "or" (x) ') == '"or"* AND "x"*'
    assert fts_query("") == "" and fts_query("--") == ""


def ids(result):
    return [m["rel_path"] for m in result["models"]]


def test_search_by_name_prefix_and_accents(scanned):
    services, _ = scanned
    assert services.search.query(Filters(q="drag"))["total"] == 3  # dragon_cubo_v2, dragon_cubo_ascii, dos_cuerpos (folder dragones)
    assert services.search.query(Filters(q="esfera"))["total"] == 2
    assert ids(services.search.query(Filters(q="pulgadas"))) == ["pieza_en_pulgadas.stl"]
    assert services.search.query(Filters(q="dragón"))["total"] >= 2  # remove_diacritics


def test_search_over_tags_notes_collection_and_listing(scanned):
    services, _ = scanned
    box = services.models.by_path("soportes/caja_abierta.stl")
    services.models.patch(box["id"], {"tags": ["organizador"], "notes": "va con tapa imantada"})
    services.listings.set(box["id"], {"title": "Caja para tornillos", "description": "Cabe en un cajón estándar", "tags": ["taller"]})
    for q in ("organizador", "imantada", "tornillos", "cajón", "taller"):
        assert ids(services.search.query(Filters(q=q))) == ["soportes/caja_abierta.stl"], q
    assert services.search.query(Filters(q="soportes"))["total"] == 3  # collection name
    services.listings.remove(box["id"])
    assert services.search.query(Filters(q="tornillos"))["total"] == 0


def test_filters(scanned):
    services, _ = scanned
    assert services.search.query(Filters(format="obj"))["total"] == 1
    assert services.search.query(Filters(format="stl,3mf"))["total"] == EXPECTED_MODELS - 1
    assert services.search.query(Filters(watertight=False))["total"] == 1
    assert services.search.query(Filters(watertight=True))["total"] == EXPECTED_MODELS - 2  # minus open box and broken
    assert services.search.query(Filters(collection="dragones"))["total"] == 3
    assert services.search.query(Filters(status="error"))["total"] == 1
    assert services.search.query(Filters(bbox_min=50))["total"] == 1  # the two-body 3MF (76 mm)
    assert services.search.query(Filters(bbox_max=3))["total"] == 1  # the inch piece
    assert services.search.query(Filters(triangles_min=1000))["total"] == 2  # the two spheres
    assert services.search.query(Filters(size_min=10_000))["total"] >= 2
    assert services.search.query(Filters(has_listing=True))["total"] == 0
    cube = services.models.by_path("dragones/dragon_cubo_v2.stl")
    services.listings.set(cube["id"], {"title": "x"})
    assert ids(services.search.query(Filters(has_listing=True))) == ["dragones/dragon_cubo_v2.stl"]
    assert services.search.query(Filters(has_listing=False))["total"] == EXPECTED_MODELS - 1
    services.models.patch(cube["id"], {"tags": ["a", "b"]})
    assert services.search.query(Filters(tag="a,b"))["total"] == 1 and services.search.query(Filters(tag="a,zz"))["total"] == 0


def test_sorting_and_pagination(scanned):
    services, _ = scanned
    by_name = ids(services.search.query(Filters(sort="name", limit=100)))
    assert by_name == sorted(by_name, key=lambda p: services.models.by_path(p)["name"].lower())
    by_tri = [m["triangles"] or -1 for m in services.search.query(Filters(sort="-triangles", limit=100))["models"]]
    assert by_tri == sorted(by_tri, reverse=True)
    by_size = [m["size_bytes"] for m in services.search.query(Filters(sort="size", limit=100))["models"]]
    assert by_size == sorted(by_size)
    page1 = services.search.query(Filters(sort="name", limit=4, offset=0))
    page2 = services.search.query(Filters(sort="name", limit=4, offset=4))
    assert page1["total"] == EXPECTED_MODELS and len(page1["models"]) == 4 and len(page2["models"]) == 4
    assert not set(ids(page1)) & set(ids(page2))
    relevance = services.search.query(Filters(q="cubo", sort="relevance"))
    assert relevance["sort"] == "relevance" and relevance["total"] >= 3
    assert services.search.query(Filters(sort="relevance"))["sort"] == "name"  # no query → falls back


def test_facets(scanned):
    services, _ = scanned
    cube = services.models.by_path("dragones/dragon_cubo_v2.stl")
    services.models.patch(cube["id"], {"tags": ["dragón"]})
    facets = services.search.facets()
    assert {f["format"] for f in facets["formats"]} == {"stl", "obj", "3mf"}
    assert facets["tags"] == [{"tag": "dragón", "n": 1}]
    assert {c["collection"] for c in facets["collections"]} == {"dragones", "soportes", "Pruebas"}
