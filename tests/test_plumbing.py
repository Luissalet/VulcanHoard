"""What the shared plumbing fixes in this app: a stable token, a second start that does not disturb the first, a capped
agent answer, a no-cache single-page app and a transaction that cannot leak the lock."""
import os
import subprocess
import sys
import threading
import time
from pathlib import Path

import httpx
import pytest
from conftest import make_config

from vulcan.db import Database
from vulcan.services import Services

ROOT = Path(__file__).resolve().parent.parent


def test_token_survives_a_restart(tmp_path):
    first = Services(make_config(tmp_path))
    token = first.token
    first.stop()
    second = Services(make_config(tmp_path))
    try:
        assert second.token == token and (tmp_path / "data" / "mcp-token").read_text().strip() == token
    finally:
        second.stop()


def test_a_corrupt_token_file_is_replaced(tmp_path):
    (tmp_path / "data").mkdir()
    (tmp_path / "data" / "mcp-token").write_text("x", encoding="utf-8")
    svc = Services(make_config(tmp_path))
    try:
        assert len(svc.token) >= 32
    finally:
        svc.stop()


def test_a_second_start_reports_the_running_app_and_leaves_the_token_alone(tmp_path):
    from vulcan.hoard_link.net import free_port

    port = free_port()
    env = {**os.environ, "VULCAN_DATA_DIR": str(tmp_path / "data"), "VULCAN_PORT": str(port), "VULCAN_BACKEND": "fake", "PYTHONUNBUFFERED": "1"}
    first = subprocess.Popen([sys.executable, "-m", "vulcan"], cwd=ROOT, env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    try:
        deadline = time.time() + 60
        while time.time() < deadline:
            try:
                if httpx.get(f"http://127.0.0.1:{port}/api/health", timeout=1).status_code == 200:
                    break
            except httpx.HTTPError:
                time.sleep(0.2)
        token_file = tmp_path / "data" / "mcp-token"
        before = token_file.read_text()
        second = subprocess.run([sys.executable, "-m", "vulcan"], cwd=ROOT, env=env, capture_output=True, text=True, timeout=60)
        assert second.returncode == 0 and "already running" in second.stdout
        assert token_file.read_text() == before
        auth = {"Authorization": f"Bearer {before.strip()}"}
        assert httpx.post(f"http://127.0.0.1:{port}/api/agent/call", json={"name": "models_stats"}, headers=auth).status_code == 200
    finally:
        first.terminate()
        try:
            first.wait(10)
        except subprocess.TimeoutExpired:
            first.kill()


def test_errors_keep_their_statuses_and_gain_a_code(client):
    auth = {"Authorization": f"Bearer {client.services.token}"}
    missing = client.post("/api/agent/call", json={"name": "model_info", "arguments": {"id": 4242}}, headers=auth)
    assert missing.status_code == 404 and missing.json()["code"] == "not_found"
    bad = client.post("/api/agent/call", json={"name": "models_search", "arguments": {"limit": "many"}}, headers=auth)
    assert bad.status_code == 400 and bad.json()["code"] == "invalid_arguments" and bad.json()["issues"]
    unknown = client.post("/api/agent/call", json={"name": "nope"}, headers=auth)
    assert unknown.status_code == 404 and unknown.json()["code"] == "unknown_tool"
    assert client.post("/api/agent/call", json={"name": "models_stats"}, headers={"Authorization": "bearer " + client.services.token}).status_code == 200
    assert client.post("/api/agent/call", json={"name": "models_stats"}, headers={"Authorization": "Bearer wrong"}).status_code == 401


def test_the_built_client_is_never_served_stale(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient

    import vulcan.main as main

    static = tmp_path / "static"
    (static / "assets").mkdir(parents=True)
    (static / "index.html").write_text("<!doctype html><script src='/assets/app-abcdef12.js'></script>", encoding="utf-8")
    (static / "assets" / "app-abcdef12.js").write_text("export {}", encoding="utf-8")
    monkeypatch.setattr(main, "STATIC_DIR", static)
    with TestClient(main.create_app(make_config(tmp_path)), base_url="http://127.0.0.1") as client:
        index = client.get("/")
        assert index.status_code == 200 and index.headers["cache-control"] == "no-cache"
        asset = client.get("/assets/app-abcdef12.js")
        assert "immutable" in asset.headers["cache-control"] and asset.headers["content-type"].startswith("text/javascript")
        assert client.get("/assets/old-deadbeef.js").status_code == 404          # not an HTML page served as a script
        assert client.get("/api/nothing").json()["code"] == "not_found"
        assert client.get("/library/42").status_code == 200                         # client-side route: index.html


def test_a_failed_begin_does_not_leave_the_lock_held(tmp_path):
    """The old transaction took the lock and then ran BEGIN: one `database is locked` hung every other thread."""
    import sqlite3

    db = Database(tmp_path / "x.db", busy_timeout_ms=50)
    other = sqlite3.connect(str(tmp_path / "x.db"), isolation_level=None)
    other.execute("BEGIN IMMEDIATE")
    with pytest.raises(sqlite3.OperationalError):
        with db.transaction():
            pass
    other.execute("ROLLBACK")
    other.close()
    done = []
    worker = threading.Thread(target=lambda: done.append(db.query("SELECT 1")))
    worker.start()
    worker.join(5)
    assert done and not worker.is_alive()
    db.close()
