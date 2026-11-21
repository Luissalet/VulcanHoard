"""Inspect a real GLB through Vulcan's library at desktop and phone widths."""
import json
import socket
import threading
import time
from pathlib import Path

import pytest
import uvicorn

from vulcan.anim_example import build
from vulcan.studio.app import create_app

ROOT = Path(__file__).resolve().parents[1]


def test_animation_inspection_locales(tmp_path, monkeypatch):
    playwright = pytest.importorskip("playwright.sync_api")
    if not (ROOT / "vulcan/static/index.html").is_file():
        pytest.skip("Build the library client with npm run build")
    monkeypatch.setenv("HOARD_HUB_AUTOSTART", "0")
    monkeypatch.setenv("VULCAN_BRIDGE_AUTOSTART", "0")
    rig = tmp_path / "rig.glb"
    build(rig, unnamed=False)
    data = tmp_path / "data"
    data.mkdir()
    (data / "studio-modules.json").write_text(json.dumps({"enabled": ["library"]}), encoding="utf-8")

    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        port = listener.getsockname()[1]
        server = uvicorn.Server(uvicorn.Config(create_app(data_dir=data, port=port), log_level="warning"))
        thread = threading.Thread(target=server.run, kwargs={"sockets": [listener]}, daemon=True)
        thread.start()
        try:
            deadline = time.monotonic() + 30
            while thread.is_alive() and not server.started and time.monotonic() < deadline:
                time.sleep(0.05)
            assert server.started, "Vulcan did not start"

            with playwright.sync_playwright() as pw:
                browser = pw.chromium.launch(channel="chrome", headless=True, args=["--use-angle=swiftshader", "--enable-unsafe-swiftshader"])
                try:
                    for width, height in ((1280, 800), (390, 844)):
                        for locale, heading, inspect, manual, loop, scale in (
                            ("es-ES", "Animaciones", "Inspeccionar animaciones", "A mano en la herramienta de animación",
                             "Cierra el bucle de walk", "Aplica o elimina la escala no uniforme"),
                            ("en-GB", "Animations", "Inspect animations", "Manual steps in the animation tool",
                             "close the loop of walk", "apply or remove the non-uniform scale"),
                        ):
                            page = browser.new_page(locale=locale, viewport={"width": width, "height": height})
                            errors = []
                            page.on("pageerror", lambda error: errors.append(str(error)))
                            page.goto(f"http://127.0.0.1:{port}/library/#/animar", wait_until="domcontentloaded")
                            playwright.expect(page.get_by_role("heading", name=heading, exact=True)).to_be_visible()
                            page.locator("[data-anim-path]").fill(str(rig))
                            page.locator("[data-anim-open]").click()
                            page.get_by_role("button", name=inspect, exact=True).click()
                            instructions = page.locator("[data-anim-manual]")
                            playwright.expect(instructions.get_by_text(manual, exact=True)).to_be_visible()
                            playwright.expect(instructions.locator("li")).to_have_count(2)
                            playwright.expect(instructions.locator("li").nth(0)).to_contain_text(loop)
                            playwright.expect(instructions.locator("li").nth(0)).to_contain_text("10.000")
                            playwright.expect(instructions.locator("li").nth(1)).to_contain_text(scale)
                            instructions.scroll_into_view_if_needed()
                            page.screenshot(path=str(tmp_path / f"animation-{width}-{locale}.png"))
                            assert not errors, errors
                            page.close()
                finally:
                    browser.close()
        finally:
            server.should_exit = True
            thread.join(timeout=15)
            assert not thread.is_alive(), "Vulcan did not stop"
