"""Folder watching: a change in a watched root queues a rescan after the debounce."""

import time

import pytest
from conftest import make_config
from fixtures import EXPECTED_MODELS, cube

from vulcan.services import Services


@pytest.fixture
def watched(tmp_path, library):
    services = Services(make_config(tmp_path, watch=True))
    services.worker.start()
    try:
        root = services.add_root("Vigilada", str(library), None, None, True)
        assert services.worker.wait_idle(120)
        yield services, root
    finally:
        services.stop()


def test_change_in_watched_folder_triggers_rescan(watched, library):
    services, root = watched
    if not services.watcher.available:
        pytest.skip(f"watchdog unavailable here: {services.watcher.error}")
    assert services.watcher.watching() == [root.id]
    assert services.stats.counts()["models"] == EXPECTED_MODELS
    cube((3.0, 4.0, 5.0)).export(library / "recien_llegado.stl", file_type="stl")
    deadline = time.time() + 30
    while time.time() < deadline:
        if services.stats.counts()["models"] == EXPECTED_MODELS + 1 and not services.worker.status()["busy"]:
            break
        time.sleep(0.2)
    assert services.models.by_path("recien_llegado.stl")["name"] == "Recien llegado"


def test_unwatch_when_disabled(watched):
    services, root = watched
    if not services.watcher.available:
        pytest.skip("watchdog unavailable here")
    services.update_root(root.id, {"watch": False})
    assert services.watcher.watching() == []
    services.update_root(root.id, {"watch": True})
    assert services.watcher.watching() == [root.id]
    services.update_root(root.id, {"enabled": False})
    assert services.watcher.watching() == []
