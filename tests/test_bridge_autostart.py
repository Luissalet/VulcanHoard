"""The MCP bridge (the shared `hoard_link.bridge.CatalogBridge`) starts the app when nothing answers, so a workspace that
starts first still gets the tools."""
import importlib.util
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def load(monkeypatch, autostart: str):
    monkeypatch.setenv("VULCAN_URL", "http://127.0.0.1:1")
    monkeypatch.setenv("VULCAN_BRIDGE_AUTOSTART", autostart)
    spec = importlib.util.spec_from_file_location("vulcan_bridge_under_test", ROOT / "mcp_server.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.build()


def test_nothing_answers_and_autostart_is_off(monkeypatch):
    bridge = load(monkeypatch, "0")
    assert bridge.healthy() is False
    assert bridge.start_app(wait_s=1) is False


def test_the_bridge_is_configured_for_this_app(monkeypatch):
    bridge = load(monkeypatch, "1")
    assert (bridge.service, bridge.package, bridge.default_port) == ("vulcan-hoard", "vulcan", 5186)
    assert bridge.base_url == "http://127.0.0.1:1" and bridge.prefix == "VULCAN"


def test_nothing_answers_so_the_bridge_starts_the_app(monkeypatch, tmp_path):
    from vulcan.hoard_link import bridge as lib

    bridge = load(monkeypatch, "1")
    monkeypatch.setenv("VULCAN_DATA_DIR", str(tmp_path))
    started = []

    answers = iter([False, False, True])
    monkeypatch.setattr(lib, "_healthy", lambda port, service, host="127.0.0.1": next(answers, True))
    # The bridge starts the app as an orphan (so it outlives the MCP host): record the spawn instead of making one.
    monkeypatch.setattr(lib.launch, "spawn_orphan", lambda argv, cwd, env, log_path: started.append((argv, env)) or 4242)
    monkeypatch.setattr(lib.launch, "process_created", lambda pid: 1.0)
    monkeypatch.setattr(lib.launch, "process_alive", lambda pid, created: True)
    lib._children.clear()
    assert bridge.start_app(wait_s=5) is True
    argv, env = started[0]
    assert argv == [sys.executable, "-m", "vulcan"]
    assert env["VULCAN_PORT"] == "1" and env["PORT_STRICT"] == "1" and env["HOARD_NO_BROWSER"] == "1"
    assert (tmp_path / "logs").is_dir()
