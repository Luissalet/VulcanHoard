"""Ranking searches fold exact duplicates: 'the biggest models' are different models."""
from vulcan.agent_tools import call_tool


def _search(services, **args):
    out = call_tool(services, "models_search", args)
    return out.get("result", out) if isinstance(out, dict) else out


def test_ranking_sort_folds_identical_files(scanned):
    services, _root = scanned
    every = _search(services, sort="-size", limit=50, distinct=False)
    folded = _search(services, sort="-size", limit=50)
    assert folded["distinct"] is True
    assert len(folded["hits"]) == len(every["hits"]) - 1  # the fixture has one exact copy of the cube
    cube = [h for h in folded["hits"] if h["copies"] == 2]
    assert len(cube) == 1
    names = [h["name"] for h in folded["hits"]]
    assert len(names) == len(set(h["id"] for h in folded["hits"]))


def test_relevance_keeps_every_file(scanned):
    services, _root = scanned
    out = _search(services, q="cubo", limit=50)
    assert "distinct" not in out
    assert all("copies" not in h for h in out["hits"])


def test_distinct_pagination(scanned):
    services, _root = scanned
    first = _search(services, sort="-extent", limit=2)
    second = _search(services, sort="-extent", limit=2, offset=2)
    ids = [h["id"] for h in first["hits"] + second["hits"]]
    assert len(ids) == len(set(ids))
