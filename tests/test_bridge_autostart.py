"""The MCP bridge starts the app when nothing answers, so a workspace that starts first still gets the tools."""
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
    return module


def test_nothing_answers_and_autostart_is_off(monkeypatch):
    bridge = load(monkeypatch, "0")
    assert bridge._healthy() is False
    assert bridge.ensure_running(timeout_s=1) is False


def test_nothing_answers_so_the_bridge_starts_the_app(monkeypatch, tmp_path):
    bridge = load(monkeypatch, "1")
    monkeypatch.setenv("VULCAN_DATA_DIR", str(tmp_path))
    started = []
    answers = iter([False, True])
    monkeypatch.setattr(bridge, "_healthy", lambda: next(answers, True))
    monkeypatch.setattr(bridge.subprocess, "Popen", lambda argv, **kw: started.append((argv, kw["env"])))
    assert bridge.ensure_running(timeout_s=5) is True
    argv, env = started[0]
    assert argv == [sys.executable, "-m", "vulcan"]
    assert env["VULCAN_PORT"] == "1" and env["PORT_STRICT"] == "1"
    assert (tmp_path / "logs").is_dir()
