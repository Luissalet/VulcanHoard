"""Browser walk-through of the Espacial page against a real Vulcan process (no mocks): load the example, compute coverage by network
and straight line, see the overestimate, change a parameter, screen parcels, provoke the Web Mercator refusal."""
import shutil
import socket
import subprocess
import sys
import time
from pathlib import Path

import httpx
import pytest

sync_api = pytest.importorskip("playwright.sync_api")
ROOT = Path(__file__).resolve().parents[1]
CHROME = shutil.which("google-chrome") or shutil.which("chromium") or shutil.which("chromium-browser")
pytestmark = pytest.mark.skipif(not CHROME or not (ROOT / "vulcan" / "static" / "index.html").exists(), reason="needs Chrome and the built client (npm run build)")

LAUNCHER = """
import sys, uvicorn
from pathlib import Path
sys.path.insert(0, sys.argv[1])
from vulcan.config import Config
from vulcan.main import create_app
cfg = Config(data_dir=Path(sys.argv[2]), watch=False, autostart=False, thumb_size=128, scan_workers=1, data_dir_configured=True)
uvicorn.run(create_app(cfg), host="127.0.0.1", port=int(sys.argv[3]), log_level="warning")
"""


@pytest.fixture
def server(tmp_path):
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
    script = tmp_path / "run.py"
    script.write_text(LAUNCHER, encoding="utf-8")
    proc = subprocess.Popen([sys.executable, str(script), str(ROOT), str(tmp_path / "data"), str(port)], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    base = f"http://127.0.0.1:{port}"
    for _ in range(100):
        try:
            if httpx.get(base + "/api/status", timeout=1).status_code == 200:
                break
        except httpx.HTTPError:
            time.sleep(0.2)
    else:
        proc.terminate()
        pytest.fail("Vulcan did not start")
    yield base
    proc.terminate()
    proc.wait(10)


def test_espacial_page_walkthrough(server, tmp_path):
    with sync_api.sync_playwright() as pw:
        browser = pw.chromium.launch(executable_path=CHROME, args=["--no-sandbox"])
        page = browser.new_page(viewport={"width": 1280, "height": 1000})
        errors = []
        page.on("pageerror", lambda e: errors.append(str(e)))
        page.goto(server + "/#/espacial")
        page.get_by_role("heading", name="Análisis espacial").wait_for()
        page.get_by_role("button", name="Calcular cobertura").click()
        page.get_by_role("heading", name="Mapa de cobertura").wait_for()
        text = page.locator("main").text_content()
        for needle in ("86.2 %", "100.0 %", "óptimo exacto", "A, G"):
            assert needle in text, (needle, text[:3000])
        assert page.locator("svg[role=img] path").count() > 5 and page.locator("svg[role=img] circle").count() >= 8
        page.screenshot(path=str(tmp_path / "cobertura.png"))
        page.locator("svg[role=img]").screenshot(path=str(tmp_path / "mapa.png"))
        page.get_by_label("k (sedes)").fill("3")
        page.get_by_role("button", name="Calcular cobertura").click()
        page.get_by_text("con 3 sedes").first.wait_for()  # k=3 now
        page.get_by_role("button", name="Preseleccionar parcelas").click()
        page.get_by_role("heading", name="Parcelas:").wait_for()
        assert "2 aceptadas, 5 rechazadas" in page.get_by_role("heading", name="Parcelas:").text_content()
        rows = page.locator("main").text_content()
        assert "frente a" in rows and "P2" in rows and "Pendiente" in rows
        page.screenshot(path=str(tmp_path / "parcelas.png"))
        page.get_by_label("CRS de trabajo (métrico)").fill("EPSG:3857")
        page.get_by_role("button", name="Calcular cobertura").click()
        page.get_by_role("alert").wait_for()
        assert "distorts ground distance" in page.get_by_role("alert").inner_text()
        assert not errors, errors
        browser.close()
