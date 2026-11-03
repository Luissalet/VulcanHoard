"""Browser walk-through of the Heron assembly panel against a real Heron process (no mocks)."""
import os
import shutil
import socket
import subprocess
import sys
import time
from pathlib import Path

import httpx
import pytest

pytest.importorskip('cadquery')
sync_api = pytest.importorskip('playwright.sync_api')

ROOT = Path(__file__).resolve().parents[1]
HERON = ROOT / 'vulcan' / 'studio_native' / 'heron'
CHROME = shutil.which('google-chrome') or shutil.which('chromium') or shutil.which('chromium-browser')


def free_port():
    with socket.socket() as s:
        s.bind(('127.0.0.1', 0))
        return s.getsockname()[1]


@pytest.fixture
def server(tmp_path):
    port = free_port()
    proc = subprocess.Popen([sys.executable, '-m', 'heron', '--port', str(port), '--data-dir', str(tmp_path / 'data'), '--no-browser'],
                            cwd=HERON, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, env={**os.environ, 'PYTHONDONTWRITEBYTECODE': '1'})
    base = f'http://127.0.0.1:{port}'
    try:
        for _ in range(200):
            try:
                if httpx.get(base + '/api/health', timeout=1).status_code == 200:
                    break
            except httpx.HTTPError:
                time.sleep(0.1)
        else:
            raise RuntimeError('Heron did not start')
        yield base
    finally:
        proc.terminate()
        proc.wait(timeout=20)


@pytest.mark.skipif(CHROME is None, reason='no Chrome/Chromium executable')
def test_build_edit_conflict_and_penetration_in_the_browser(server, tmp_path):
    with sync_api.sync_playwright() as p:
        browser = p.chromium.launch(executable_path=CHROME, args=['--no-sandbox'])
        page = browser.new_page(viewport={'width': 390, 'height': 900})
        errors = []
        page.on('pageerror', lambda e: errors.append(str(e)))
        page.goto(server + '/')
        page.click('#asm-example-box')
        page.wait_for_function("document.getElementById('asm-spec').value.includes('board_lid_gap')")
        page.click('#asm-create')

        def revision(n):
            page.wait_for_function(f"(document.querySelector('#asm-output h3')||{{}}).textContent?.includes('revisión {n}')", timeout=600000)

        revision(1)
        assert page.get_attribute('#asm-badge', 'data-status') == 'solved'
        assert page.get_attribute('#asm-verdict', 'data-verdict') == 'no_penetration'
        assert page.locator('#asm-output img').count() == 3
        page.wait_for_function("[...document.querySelectorAll('#asm-output img')].every(i => i.complete && i.naturalWidth > 0)")
        assert page.locator('#asm-output a.download').count() == 3
        # Edit a parameter: taller standoffs keep the mounting axes and the board/lid distance.
        page.fill("input[aria-label='Variable standoff_h']", '20')
        page.click('#asm-apply')
        revision(2)
        assert page.get_attribute('#asm-badge', 'data-status') == 'solved'
        assert 'Los ejes de montaje se conservan' in page.inner_text('#asm-output')
        # Deliberate conflict: a fixed box height no longer matches standoff + board + gap.
        page.fill("input[aria-label='box.inner_height']", '25')
        page.click('#asm-apply')
        revision(3)
        assert page.get_attribute('#asm-badge', 'data-status') == 'incompatible'
        conflict = page.inner_text('#asm-output .conflict')
        assert 'board_lid_gap' in conflict and 'board y lid' in conflict
        # Deliberate penetration: restore the formula and widen the board beyond the interior.
        page.fill("input[aria-label='box.inner_height']", 'standoff_h + board_t + gap')
        page.fill("input[aria-label='board.length']", '104')
        page.click('#asm-apply')
        revision(4)
        assert page.get_attribute('#asm-badge', 'data-status') == 'solved'
        assert page.get_attribute('#asm-verdict', 'data-verdict') == 'interference'
        text = page.inner_text('#asm-output')
        assert 'box ↔ board' in text and 'Penetración' in text and '384.000' in text
        page.screenshot(path=str(tmp_path / 'assembly-mobile.png'), full_page=True)
        assert page.evaluate('document.documentElement.scrollWidth <= window.innerWidth + 1')
        # Revision check of the stored pose, without realignment.
        page.click('#asm-output details:last-of-type summary')
        assert page.input_value('#asm-check-source').startswith('{')
        page.click('#asm-check')
        page.wait_for_selector('#asm-check-output h4', timeout=600000)
        assert 'Revisión propuesta' in page.inner_text('#asm-check-output')
        # Revisions stay readable.
        page.click("#asm-output button:text('r1')")
        revision(1)
        assert page.get_attribute('#asm-badge', 'data-status') == 'solved'
        assert not errors, errors
        browser.close()
