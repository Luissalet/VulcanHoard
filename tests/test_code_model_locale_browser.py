"""Check code modelling labels and diagnostics in the built library."""
import json
import socket
import threading
import time
from pathlib import Path

import pytest
import uvicorn

from test_code_model import VALIDATION, fake_engine
from vulcan import code_model as cm
from vulcan.studio.app import create_app

ROOT = Path(__file__).resolve().parents[1]


def test_code_model_locales(tmp_path, monkeypatch):
    playwright = pytest.importorskip("playwright.sync_api")
    if not (ROOT / "vulcan/static/index.html").is_file():
        pytest.skip("Build the library client with npm run build")
    monkeypatch.setenv("HOARD_HUB_AUTOSTART", "0")
    monkeypatch.setenv("VULCAN_BRIDGE_AUTOSTART", "0")
    engine_installed = False
    monkeypatch.setattr(cm, "find_engine", lambda *_: Path("fake-python") if engine_installed else None)
    monkeypatch.setattr(cm, "engine_info", lambda *_: {"installed": engine_installed, "sand3d": "test", "bpy": "test"})
    monkeypatch.setattr(cm, "llm_status", lambda *_: {"available": True, "model": "test"})
    monkeypatch.setattr(cm, "run_engine", fake_engine)
    monkeypatch.setattr(cm, "validate_export", lambda *_, **__: dict(VALIDATION))

    def timeout(*_):
        raise TimeoutError

    monkeypatch.setattr(cm, "_ask", timeout)
    data = tmp_path / "data"
    data.mkdir()
    (data / "studio-modules.json").write_text(json.dumps({"enabled": ["library"]}), encoding="utf-8")
    description = "Test plate 80 x 40 mm"

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
                browser = pw.chromium.launch(
                    channel="chrome", headless=True, args=["--use-angle=swiftshader", "--enable-unsafe-swiftshader"]
                )
                try:
                    for locale, labels in (
                        ("en-US", {
                            "title": "Model with code", "nav": "Code", "install": "Install engine", "editor": "sand3d code",
                            "description": "Description (with dimensions in mm)", "register": "Log", "attempt": "attempt 1 of",
                            "error": "Vulcan timed out waiting for the global language model",
                            "example_select": "Load a tested example", "example": "Box with lid", "run": "Run this code", "measure": "Measure only",
                            "parameter": "Parameter t", "apply": "Apply parameters (1)", "badge": "Valid glTF",
                            "dimensions": "Size 80 × 40 mm: measured", "history": "Previous code versions (1)",
                            "viewer": "3D viewer", "view_hint": "Drag to rotate", "edit": "Edit by instruction",
                        }),
                        ("es-ES", {
                            "title": "Modelar con código", "nav": "Código", "install": "Instalar el motor", "editor": "Código sand3d",
                            "description": "Descripción (con medidas en mm)", "register": "Registro", "attempt": "intento 1 de",
                            "error": "Vulcan agotó el tiempo de espera del modelo de lenguaje global",
                            "example_select": "Cargar un ejemplo probado", "example": "Caja con tapa", "run": "Ejecutar este código", "measure": "Solo medir",
                            "parameter": "Parámetro t", "apply": "Aplicar parámetros (1)", "badge": "glTF válido",
                            "dimensions": "Tamaño 80 × 40 mm: medida", "history": "Versiones anteriores del código (1)",
                            "viewer": "Visor 3D", "view_hint": "Arrastra para girar", "edit": "Cambiar hablando",
                        }),
                    ):
                        engine_installed = False
                        page = browser.new_page(locale=locale, viewport={"width": 1280, "height": 900})
                        errors = []
                        page.on("pageerror", lambda error: errors.append(str(error)))
                        page.goto(f"http://127.0.0.1:{port}/library/#/codigo", wait_until="domcontentloaded")
                        expect = playwright.expect
                        expect(page.get_by_role("heading", name=labels["title"], exact=True)).to_be_visible()
                        expect(page.get_by_role("link", name=labels["nav"], exact=True)).to_have_attribute("aria-current", "page")
                        expect(page.get_by_role("button", name=labels["install"], exact=True)).to_be_visible()
                        expect(page.get_by_role("textbox", name=labels["editor"], exact=True)).to_be_visible()
                        examples = page.get_by_label(labels["example_select"])
                        expect(examples.locator('option[value="box_with_lid"]')).to_have_text(labels["example"])
                        page.screenshot(path=str(tmp_path / f"code-missing-{locale}.png"), full_page=True)

                        engine_installed = True
                        page.reload(wait_until="domcontentloaded")
                        page.get_by_label(labels["description"]).fill(description)
                        page.get_by_role("button", name=labels["title"], exact=True).click()
                        expect(page.get_by_role("alert")).to_contain_text(labels["error"])
                        expect(page.locator("ol")).to_contain_text(labels["attempt"])
                        page.get_by_text(labels["register"], exact=True).click()
                        expect(page.locator("details[open] pre")).to_contain_text("model_timeout")
                        page.screenshot(path=str(tmp_path / f"code-job-{locale}.png"), full_page=True)

                        page.get_by_role("button", name=labels["measure"], exact=True).click()
                        expect(page.locator("[data-requirements]")).to_contain_text(labels["dimensions"])
                        page.get_by_role("button", name=labels["run"], exact=True).click()
                        expect(page.get_by_text(labels["badge"], exact=True)).to_be_visible()
                        expect(page.get_by_label(labels["viewer"])).to_contain_text(labels["view_hint"], timeout=15000)
                        expect(page.get_by_role("heading", name=labels["edit"], exact=True)).to_be_visible()
                        page.get_by_label(labels["parameter"], exact=True).fill("8")
                        page.get_by_role("button", name=labels["apply"], exact=True).click()
                        expect(page.get_by_text(labels["history"], exact=True)).to_be_visible()
                        page.screenshot(path=str(tmp_path / f"code-result-{locale}.png"), full_page=True)
                        assert not errors, errors
                        page.close()
                finally:
                    browser.close()
        finally:
            server.should_exit = True
            thread.join(timeout=15)
            assert not thread.is_alive(), "Vulcan did not stop"
