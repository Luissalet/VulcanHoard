"""
Silhouette 3D — Flask web UI.

Upload a PNG, watch it become a black silhouette, a vector SVG, and an
extruded 3D mesh (STL). The 3D preview uses Three.js in the browser.

Run:
    python app.py
Then open http://localhost:5000
"""

import os
import io
import base64
import mimetypes
import multiprocessing
from pathlib import Path
from flask import Flask, request, jsonify, render_template, render_template_string, redirect

# Browsers enforce strict MIME checking for <script type="module">.
# Flask's default map has no entry for .mjs (served as text/plain), which
# breaks the editor's ES modules. Register it before the first request.
mimetypes.add_type("application/javascript", ".mjs")

from vulcan.studio_native.plato import plato_family
from vulcan.studio_native.plato import plato_copilot
from vulcan.studio_native.plato.pipeline import process_image
from vulcan.studio_native.plato.silhouettes.editor.api import editor_bp, create_editor_app
from vulcan.studio_native.plato.silhouettes.editor.document_store import DocumentStore
from vulcan.studio_native.plato.silhouettes.editor.jobs import JobScheduler

app = Flask(__name__)
app.config["TEMPLATES_AUTO_RELOAD"] = True
app.jinja_env.auto_reload = True
app.config["MAX_CONTENT_LENGTH"] = 16 * 1024 * 1024  # 16 MB upload limit


from . import studio_config
PORT = studio_config.port
HOST = os.environ.get("PLATO_HOST") or "127.0.0.1"


@app.before_request
def _local_requests_only():
    """The shared request guard: loopback Host (DNS rebinding), Origin and Fetch Metadata rules."""
    refused = plato_family.check_request(request.method, dict(request.headers), PORT)
    if refused:
        return jsonify({"error": refused[1]}), refused[0]
    return None


@app.after_request
def _no_cache_editor_assets(response):
    # Avoid stale ES modules during local iteration (you restart the server).
    if request.path.startswith("/static/editor/") or request.path == "/editor":
        response.headers["Cache-Control"] = "no-store, max-age=0"
    return response

# Editor v2 wiring (task 04): scheduler created at startup, not import time.
_DATA_DIR = Path(studio_config.data_dir) if studio_config.data_dir is not None else Path(__file__).parent / "data"
_DATA_DIR.mkdir(parents=True, exist_ok=True)
_JOBS_DIR = _DATA_DIR / "jobs"
_JOBS_DIR.mkdir(exist_ok=True)

_store = DocumentStore(_DATA_DIR / "documents")
_scheduler = JobScheduler(output_dir=_JOBS_DIR)
app.extensions["editor_store"] = _store
app.extensions["job_scheduler"] = _scheduler
app.register_blueprint(editor_bp)
# Faustus copilot: same-origin relay (/api/copilot/*) and the launcher added to every HTML page (HOARD_COPILOT=0 turns it off).
plato_copilot.install_copilot(app, _DATA_DIR)


HTML_TEMPLATE = """
<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8"/>
<title>Plato's Hoard</title>
<link rel="icon" href="/static/favicon.ico" sizes="any">
<link rel="icon" type="image/png" sizes="192x192" href="/static/icon-192.png">
<link rel="apple-touch-icon" href="/static/apple-touch-icon.png">
<style>
  :root {
    --bg: #0f1115;
    --panel: #1a1d24;
    --border: #2a2e38;
    --text: #e4e6eb;
    --muted: #8b8f9a;
    --accent: #4f8cff;
  }
  * { box-sizing: border-box; margin: 0; padding: 0; }
  body {
    font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
    background: var(--bg);
    color: var(--text);
    min-height: 100vh;
    padding: 24px;
  }
  h1 { font-size: 1.4rem; font-weight: 600; margin-bottom: 4px; }
  .subtitle { color: var(--muted); font-size: 0.85rem; margin-bottom: 24px; }

  /* Upload area */
  .upload-area {
    border: 2px dashed var(--border);
    border-radius: 12px;
    padding: 40px;
    text-align: center;
    cursor: pointer;
    transition: border-color 0.2s, background 0.2s;
    margin-bottom: 24px;
  }
  .upload-area:hover, .upload-area.dragover {
    border-color: var(--accent);
    background: rgba(79, 140, 255, 0.05);
  }
  .upload-area p { color: var(--muted); font-size: 0.95rem; }
  .upload-area .icon { font-size: 2.5rem; margin-bottom: 12px; }
  #file-input { display: none; }

  /* Controls */
  .controls {
    display: flex;
    gap: 16px;
    align-items: center;
    margin-bottom: 16px;
    flex-wrap: wrap;
  }
  .control-group { display: flex; align-items: center; gap: 8px; }
  .control-group label { font-size: 0.85rem; color: var(--muted); }
  .control-group input[type="range"] { width: 120px; accent-color: var(--accent); }
  .control-group .value {
    font-size: 0.85rem;
    min-width: 32px;
    text-align: right;
    font-variant-numeric: tabular-nums;
  }
  .control-group select {
    background: var(--panel);
    color: var(--text);
    border: 1px solid var(--border);
    border-radius: 6px;
    padding: 6px 10px;
    font-size: 0.85rem;
  }
  button {
    background: var(--accent);
    color: white;
    border: none;
    border-radius: 8px;
    padding: 10px 20px;
    font-size: 0.9rem;
    font-weight: 500;
    cursor: pointer;
    transition: opacity 0.15s;
  }
  button:hover { opacity: 0.85; }
  button:disabled { opacity: 0.4; cursor: not-allowed; }
  button.secondary {
    background: var(--border);
    color: var(--text);
    padding: 6px 12px;
    font-size: 0.8rem;
  }
  button.secondary.active { background: var(--accent); color: white; }

  .advanced-toggle {
    font-size: 0.8rem;
    color: var(--muted);
    cursor: pointer;
    user-select: none;
    margin-bottom: 16px;
  }
  .advanced-toggle:hover { color: var(--text); }
  .advanced { display: none; margin-bottom: 16px; }
  .advanced.open { display: flex; gap: 16px; flex-wrap: wrap; align-items: center; }

  /* View buttons */
  .view-buttons { display: flex; gap: 6px; }

  /* Results grid */
  .results {
    display: grid;
    grid-template-columns: repeat(auto-fit, minmax(280px, 1fr));
    gap: 16px;
  }
  .card {
    background: var(--panel);
    border: 1px solid var(--border);
    border-radius: 12px;
    overflow: hidden;
  }
  .card-header {
    padding: 12px 16px;
    font-size: 0.8rem;
    font-weight: 600;
    text-transform: uppercase;
    letter-spacing: 0.05em;
    color: var(--muted);
    border-bottom: 1px solid var(--border);
    display: flex;
    justify-content: space-between;
    align-items: center;
  }
  .card-header .badge {
    background: var(--accent);
    color: white;
    padding: 2px 8px;
    border-radius: 10px;
    font-size: 0.7rem;
  }
  #stl-body {
    position: relative;
    display: block;
    padding: 0;
    height: 420px;
    min-height: 420px;
    overflow: hidden;
  }
  #stl-body canvas {
    position: absolute;
    inset: 0;
    display: block;
    width: 100%;
    height: 100%;
    touch-action: none;
    image-rendering: auto;
  }
  .card-body {
    padding: 16px;
    display: flex;
    align-items: center;
    justify-content: center;
    min-height: 200px;
    background: #ffffff; /* white preview background (CSS, not part of SVG) */
  }
  .card-body img { max-width: 100%; max-height: 300px; object-fit: contain; }
  .card-body svg { max-width: 100%; max-height: 300px; }
  .card-body canvas { width: 100%; height: 300px; border-radius: 8px; }
  .card-body .placeholder { color: var(--muted); font-size: 0.85rem; }

  /* 3D card spans full width */
  .card-3d { grid-column: 1 / -1; }
  .card-3d .card-body { min-height: 400px; padding: 0; background: var(--panel); }
  .card-3d canvas { height: 400px; border-radius: 0; }

  /* Metadata bar */
  .meta-bar {
    display: flex;
    gap: 16px;
    flex-wrap: wrap;
    padding: 8px 16px;
    font-size: 0.75rem;
    color: var(--muted);
    border-top: 1px solid var(--border);
  }
  .meta-bar .ok { color: #4caf50; }
  .meta-bar .bad { color: #ff6b6b; }

  /* Download buttons */
  .downloads { display: flex; gap: 8px; }
  .downloads a {
    font-size: 0.75rem;
    padding: 4px 10px;
    border-radius: 6px;
    background: var(--border);
    color: var(--text);
    text-decoration: none;
    transition: background 0.15s;
  }
  .downloads a:hover { background: var(--accent); color: white; }

  /* Spinner */
  .spinner {
    display: inline-block;
    width: 20px;
    height: 20px;
    border: 2px solid var(--border);
    border-top-color: var(--accent);
    border-radius: 50%;
    animation: spin 0.8s linear infinite;
  }
  @keyframes spin { to { transform: rotate(360deg); } }

  .status { text-align: center; padding: 20px; color: var(--muted); font-size: 0.9rem; }
  .status.error { color: #ff6b6b; }
</style>
</head>
<body>
  <h1>Silhouette 3D</h1>
  <p class="subtitle">PNG &rarr; black silhouette &rarr; SVG &rarr; extruded 3D mesh (single source of truth)</p>

  <div class="upload-area" id="upload-area">
    <div class="icon">&#128193;</div>
    <p>Drop a PNG here or click to browse</p>
    <input type="file" id="file-input" accept="image/png"/>
  </div>

  <div class="controls">
    <div class="control-group">
      <label for="thickness">Thickness</label>
      <input type="range" id="thickness" min="1" max="50" value="10"/>
      <span class="value" id="thickness-val">10</span>
    </div>
    <div class="control-group">
      <label for="detail">Detail</label>
      <input type="range" id="detail" min="0.1" max="3" step="0.05" value="0.8"/>
      <span class="value" id="detail-val">0.5</span>
    </div>
    <div class="control-group">
      <label for="speckle">Speck removal</label>
      <input type="range" id="speckle" min="0" max="200" step="5" value="10"/>
      <span class="value" id="speckle-val">10</span>
    </div>
    <div class="control-group">
      <label for="preset">Preset</label>
      <select id="preset">
        <option value="exact">Exact</option>
        <option value="clean" selected>Clean</option>
        <option value="smooth">Smooth</option>
      </select>
    </div>
    <button id="process-btn" disabled>Process</button>
  </div>

  <div class="advanced-toggle" id="adv-toggle">&#9656; Advanced</div>
  <div class="advanced" id="advanced">
    <div class="control-group">
      <label for="alpha-threshold">Alpha threshold</label>
      <input type="range" id="alpha-threshold" min="1" max="254" value="128"/>
      <span class="value" id="alpha-threshold-val">128</span>
    </div>
  </div>

  <div class="status" id="status" style="display:none"></div>

  <div class="results" id="results" style="display:none">
    <div class="card">
      <div class="card-header">
        Original
        <div class="downloads" id="dl-original"></div>
      </div>
      <div class="card-body" id="original-body">
        <span class="placeholder">No image loaded</span>
      </div>
    </div>

    <div class="card">
      <div class="card-header">
        Silhouette
        <div class="downloads" id="dl-silhouette"></div>
      </div>
      <div class="card-body" id="silhouette-body">
        <span class="placeholder">Waiting for processing</span>
      </div>
    </div>

    <div class="card">
      <div class="card-header">
        SVG Vector
        <div class="downloads" id="dl-svg"></div>
      </div>
      <div class="card-body" id="svg-body">
        <span class="placeholder">Waiting for processing</span>
      </div>
    </div>

    <div class="card card-3d">
      <div class="card-header">
        3D Extrusion
        <div style="display:flex;gap:8px;align-items:center">
          <div class="view-buttons">
            <button class="secondary" data-view="front">Front</button>
            <button class="secondary" data-view="iso">Iso</button>
            <button class="secondary" data-view="side">Side</button>
            <button class="secondary" data-view="top">Top</button>
            <button class="secondary" data-view="fit">Encuadrar</button>
            <button class="secondary" id="preview-hq">HQ</button>
            <button class="secondary" id="overlay-btn" title="Overlay SVG contour on STL front view">Overlay SVG</button>
          </div>
          <div class="downloads" id="dl-stl"></div>
        </div>
      </div>
      <div class="card-body" id="stl-body">
        <span class="placeholder">Waiting for processing</span>
      </div>
      <div class="meta-bar" id="meta-bar" style="display:none"></div>
    </div>
  </div>

  <script src="https://cdn.jsdelivr.net/npm/three@0.128.0/build/three.min.js"></script>
  <script src="https://cdn.jsdelivr.net/npm/three@0.128.0/examples/js/loaders/STLLoader.js"></script>
  <script>
    const uploadArea = document.getElementById('upload-area');
    const fileInput = document.getElementById('file-input');
    const processBtn = document.getElementById('process-btn');
    const statusEl = document.getElementById('status');
    const resultsEl = document.getElementById('results');
    const thicknessSlider = document.getElementById('thickness');
    const detailSlider = document.getElementById('detail');
    const speckleSlider = document.getElementById('speckle');
    const presetSelect = document.getElementById('preset');
    const alphaSlider = document.getElementById('alpha-threshold');
    const thicknessVal = document.getElementById('thickness-val');
    const detailVal = document.getElementById('detail-val');
    const speckleVal = document.getElementById('speckle-val');
    const alphaVal = document.getElementById('alpha-threshold-val');
    const advToggle = document.getElementById('adv-toggle');
    const advancedEl = document.getElementById('advanced');

    let currentFile = null;
    let currentSvgB64 = null;
    let threeScene = null;
    let threeRenderer = null;
    let threeCamera = null;
    let threeMesh = null;
    let threeAnimId = null;
    let overlayLine = null;

    // Slider value display
    thicknessSlider.addEventListener('input', () => { thicknessVal.textContent = thicknessSlider.value; });
    detailSlider.addEventListener('input', () => { detailVal.textContent = detailSlider.value; });
    speckleSlider.addEventListener('input', () => { speckleVal.textContent = speckleSlider.value; });
    alphaSlider.addEventListener('input', () => { alphaVal.textContent = alphaSlider.value; });

    const detailByPreset = { exact: 0.1, clean: 0.8, smooth: 1.25 };
    function applyTracePreset() {
      detailSlider.value = String(detailByPreset[presetSelect.value]);
      detailVal.textContent = detailSlider.value;
    }
    presetSelect.addEventListener('change', applyTracePreset);
    applyTracePreset();

    // Advanced toggle
    advToggle.addEventListener('click', () => {
      advancedEl.classList.toggle('open');
      advToggle.innerHTML = advancedEl.classList.contains('open')
        ? '&#9662; Advanced' : '&#9656; Advanced';
    });

    // Upload handling
    uploadArea.addEventListener('click', () => fileInput.click());
    uploadArea.addEventListener('dragover', (e) => { e.preventDefault(); uploadArea.classList.add('dragover'); });
    uploadArea.addEventListener('dragleave', () => { uploadArea.classList.remove('dragover'); });
    uploadArea.addEventListener('drop', (e) => {
      e.preventDefault();
      uploadArea.classList.remove('dragover');
      const files = e.dataTransfer.files;
      if (files.length > 0) handleFile(files[0]);
    });
    fileInput.addEventListener('change', () => {
      if (fileInput.files.length > 0) handleFile(fileInput.files[0]);
    });

    function handleFile(file) {
      if (!file.type.includes('png') && !file.name.toLowerCase().endsWith('.png')) {
        showStatus('Please upload a PNG file.', true);
        return;
      }
      currentFile = file;
      processBtn.disabled = false;

      const reader = new FileReader();
      reader.onload = (e) => {
        document.getElementById('original-body').innerHTML =
          `<img src="${e.target.result}" alt="Original"/>`;
        const dl = document.getElementById('dl-original');
        dl.innerHTML = `<a href="${e.target.result}" download="${file.name}">Download</a>`;
      };
      reader.readAsDataURL(file);

      resultsEl.style.display = 'grid';
      showStatus('');
    }

    function showStatus(msg, isError = false) {
      if (!msg) { statusEl.style.display = 'none'; return; }
      statusEl.style.display = 'block';
      statusEl.className = 'status' + (isError ? ' error' : '');
      statusEl.innerHTML = msg;
    }

    // Process button
    processBtn.addEventListener('click', async () => {
      if (!currentFile) return;

      processBtn.disabled = true;
      processBtn.textContent = 'Processing...';
      showStatus('<span class="spinner"></span> Processing silhouette...');

      const formData = new FormData();
      formData.append('file', currentFile);
      formData.append('thickness', thicknessSlider.value);
      formData.append('detail', detailSlider.value);
      formData.append('speckle_area', speckleSlider.value);
      formData.append('preset', presetSelect.value);
      formData.append('alpha_threshold', alphaSlider.value);

      try {
        const resp = await fetch('/api/process', { method: 'POST', body: formData });
        if (!resp.ok) {
          const err = await resp.json().catch(() => ({}));
          throw new Error(err.error || `HTTP ${resp.status}`);
        }
        const data = await resp.json();
        displayResults(data);
        showStatus('');
      } catch (err) {
        showStatus(`Error: ${err.message}`, true);
      } finally {
        processBtn.disabled = false;
        processBtn.textContent = 'Process';
      }
    });

    function displayResults(data) {
      // Silhouette
      const silB64 = data.silhouette_png;
      document.getElementById('silhouette-body').innerHTML =
        `<img src="data:image/png;base64,${silB64}" alt="Silhouette"/>`;
      document.getElementById('dl-silhouette').innerHTML =
        `<a href="data:image/png;base64,${silB64}" download="silhouette.png">Download</a>`;

      // SVG
      const svgB64 = data.svg;
      currentSvgB64 = svgB64;
      const svgDataUrl = `data:image/svg+xml;base64,${svgB64}`;
      document.getElementById('svg-body').innerHTML =
        `<img src="${svgDataUrl}" alt="SVG"/>`;
      document.getElementById('dl-svg').innerHTML =
        `<a href="${svgDataUrl}" download="silhouette.svg">Download</a>`;

      // STL 3D preview
      const stlB64 = data.stl;
      document.getElementById('dl-stl').innerHTML =
        `<a href="data:application/octet-stream;base64,${stlB64}" download="silhouette.stl">Download STL</a>`;

      // Metadata bar
      const meta = data.metadata || {};
      const metaBar = document.getElementById('meta-bar');
      metaBar.style.display = 'flex';
      metaBar.innerHTML = `
        <span class="${meta.watertight ? 'ok' : 'bad'}">watertight: ${meta.watertight}</span>
        <span>vertices: ${meta.vertices}</span>
        <span>triangles: ${meta.triangle_count}</span>
        <span>components: ${meta.components}</span>
        <span>holes: ${meta.holes}</span>
        <span>volume: ${meta.volume ? meta.volume.toFixed(1) : '?'}</span>
      `;

      // Load STL into Three.js
      const stlBody = document.getElementById('stl-body');
      stlBody.innerHTML = '';
      loadSTL(stlB64, stlBody, data.outline_rings || []);
    }

    // Full replacement for the previous loadSTL, NOT an additional renderer.
    // Requires Three.js + STLLoader already loaded by app.py (r128 is sufficient).
    // Call: loadSTL(stlB64, stlBody, data.outline_rings || []);
    // Add the CSS and optional Fit/HQ buttons documented in the Markdown.
    function loadSTL(b64, container, outlineRings = []) {
      if (loadSTL.dispose) loadSTL.dispose();
      const bytes = Uint8Array.from(atob(b64), c => c.charCodeAt(0));
      const geometry = new THREE.STLLoader().parse(bytes.buffer);
      geometry.computeBoundingBox();
      const center = geometry.boundingBox.getCenter(new THREE.Vector3());
      const size = geometry.boundingBox.getSize(new THREE.Vector3());
      const topZ = geometry.boundingBox.max.z;
      const span = Math.max(size.x, size.y, size.z);
      if (!(span > 0) || !Number.isFinite(span)) throw new Error('Invalid STL bounds');
      // Display-only centering. Never rewrite or rescale the downloadable STL.
      geometry.translate(-center.x, -center.y, -center.z);

      const scene = new THREE.Scene();
      scene.background = new THREE.Color(0x1a1d24);
      const radius = size.length() / 2;
      const distance = radius * 4 + 1;
      const camera = new THREE.OrthographicCamera(-1, 1, 1, -1, 0.01, distance + radius * 4 + 1);
      camera.position.set(0, 0, distance);
      camera.lookAt(0, 0, 0);
      const renderer = new THREE.WebGLRenderer({ antialias: true, alpha: false });
      // Exactly ONE resolution multiplier: buffer dimensions below. No double DPR.
      renderer.setPixelRatio(1);
      const canvas = renderer.domElement;
      container.replaceChildren(canvas);
      const material = new THREE.MeshPhongMaterial({ color: 0x4f8cff, shininess: 80 });
      const mesh = new THREE.Mesh(geometry, material);
      const pivot = new THREE.Group();
      pivot.add(mesh);
      scene.add(pivot);
      scene.add(new THREE.AmbientLight(0xffffff, 0.7));
      const light = new THREE.DirectionalLight(0xffffff, 0.8);
      light.position.set(1, 1, 2);
      scene.add(light);

      const overlay = new THREE.Group();
      for (const ring of outlineRings) {
        if (ring.length < 3) continue;
        const last = ring[ring.length - 1], first = ring[0];
        const closed = last[0] === first[0] && last[1] === first[1];
        const source = closed ? ring.slice(0, -1) : ring;
        const points = source.map(([x, y]) => new THREE.Vector3(
          x - center.x, y - center.y, topZ - center.z + span * 0.00001
        ));
        overlay.add(new THREE.LineLoop(
          new THREE.BufferGeometry().setFromPoints(points),
          new THREE.LineBasicMaterial({ color: 0xff4444 })
        ));
      }
      overlay.visible = false;
      pivot.add(overlay);

      let supersample = 1;
      let disposed = false;
      let raf = 0;
      const removers = [];
      const on = (target, type, handler, options) => {
        if (!target) return;
        target.addEventListener(type, handler, options);
        removers.push(() => target.removeEventListener(type, handler, options));
      };
      const gl = renderer.getContext();
      const maxDimension = gl.getParameter(gl.MAX_RENDERBUFFER_SIZE);
      function updateBuffer() {
        const rect = canvas.getBoundingClientRect();
        if (!(rect.width > 0 && rect.height > 0)) return false;
        const dpr = window.devicePixelRatio || 1;
        let w = Math.ceil(rect.width * dpr * supersample);
        let h = Math.ceil(rect.height * dpr * supersample);
        const factor = Math.min(1, Math.sqrt(8_000_000 / (w * h)), maxDimension / w, maxDimension / h);
        w = Math.max(1, Math.floor(w * factor));
        h = Math.max(1, Math.floor(h * factor));
        if (canvas.width !== w || canvas.height !== h) renderer.setSize(w, h, false);
        return true;
      }
      function setFrustum(resetZoom) {
        const rect = canvas.getBoundingClientRect();
        if (!(rect.width > 0 && rect.height > 0)) return;
        pivot.updateMatrixWorld(true);
        // Use the actual rotated mesh, not screenshot dimensions or source image size.
        const box = new THREE.Box3().setFromObject(mesh);
        const aspect = rect.width / rect.height;
        const xRadius = Math.max(Math.abs(box.min.x), Math.abs(box.max.x));
        const yRadius = Math.max(Math.abs(box.min.y), Math.abs(box.max.y));
        const halfHeight = Math.max(yRadius, xRadius / aspect, span * 0.01) * 1.12;
        camera.left = -halfHeight * aspect; camera.right = halfHeight * aspect;
        camera.top = halfHeight; camera.bottom = -halfHeight;
        if (resetZoom) camera.zoom = 1;
        camera.updateProjectionMatrix();
      }
      function render() {
        if (disposed) return;
        updateBuffer();
        renderer.render(scene, camera);
      }
      function scheduleRender() {
        if (disposed || raf) return;
        raf = requestAnimationFrame(() => { raf = 0; render(); });
      }
      function resize() {
        if (disposed) return;
        setFrustum(false);
        scheduleRender();
      }
      const observer = new ResizeObserver(resize);
      observer.observe(container);
      on(window, 'resize', resize); // Covers most browser zoom / DPR changes too.

      let dragging = false, prevX = 0, prevY = 0;
      on(canvas, 'pointerdown', e => {
        dragging = true; prevX = e.clientX; prevY = e.clientY;
        canvas.setPointerCapture(e.pointerId);
      });
      on(canvas, 'pointermove', e => {
        if (!dragging) return;
        pivot.rotation.y += (e.clientX - prevX) * 0.01;
        pivot.rotation.x += (e.clientY - prevY) * 0.01;
        prevX = e.clientX; prevY = e.clientY;
        scheduleRender();
      });
      on(canvas, 'pointerup', () => { dragging = false; });
      on(canvas, 'pointercancel', () => { dragging = false; });
      on(canvas, 'wheel', e => {
        e.preventDefault();
        camera.zoom = Math.max(0.1, Math.min(20, camera.zoom * Math.exp(-e.deltaY * 0.001)));
        camera.updateProjectionMatrix();
        scheduleRender();
      }, { passive: false });
      const views = {
        front: [0, 0], iso: [Math.PI / 6, Math.PI / 4],
        side: [0, Math.PI / 2], top: [Math.PI / 2, 0]
      };
      for (const button of document.querySelectorAll('[data-view]')) {
        on(button, 'click', () => {
          const name = button.dataset.view;
          if (views[name]) pivot.rotation.set(views[name][0], views[name][1], 0);
          if (views[name] || name === 'fit') { setFrustum(true); scheduleRender(); }
        });
      }
      const overlayButton = document.getElementById('overlay-btn');
      if (overlayButton) {
        overlayButton.disabled = !outlineRings.length;
        overlayButton.classList.remove('active');
        on(overlayButton, 'click', () => {
          overlay.visible = !overlay.visible;
          overlayButton.classList.toggle('active', overlay.visible);
          scheduleRender();
        });
      }
      const hqButton = document.getElementById('preview-hq');
      if (hqButton) {
        hqButton.classList.remove('active');
        on(hqButton, 'click', () => {
          supersample = supersample === 1 ? 2 : 1;
          hqButton.classList.toggle('active', supersample === 2);
          scheduleRender();
        });
      }
      loadSTL.diagnostics = () => {
        const rect = canvas.getBoundingClientRect();
        return {
          cssWidth: rect.width, cssHeight: rect.height,
          bufferWidth: canvas.width, bufferHeight: canvas.height,
          devicePixelRatio: window.devicePixelRatio || 1,
          supersample, actualAntialias: gl.getContextAttributes().antialias,
          defaultFramebufferSamples: gl.getParameter(gl.SAMPLES),
          zoom: camera.zoom,
          note: 'Pixel-budget cap can lower requested supersampling. AA does not alter STL geometry.'
        };
      };
      loadSTL.dispose = () => {
        disposed = true;
        if (raf) cancelAnimationFrame(raf);
        observer.disconnect(); removers.forEach(remove => remove());
        overlay.children.forEach(line => { line.geometry.dispose(); line.material.dispose(); });
        geometry.dispose(); material.dispose(); renderer.dispose();
        loadSTL.diagnostics = null;
      };
      setFrustum(true);
      render();
    }

  </script>
</body>
</html>
"""


@app.route("/")
def index():
    # The layer editor is the app; the original single-shot page stays reachable at /legacy.
    return redirect("/editor", code=302)


@app.route("/legacy")
def legacy():
    return render_template_string(HTML_TEMPLATE)


@app.route("/editor")
def editor():
    return render_template("editor.html")


@app.route("/api/process", methods=["POST"])
def api_process():
    """Process an uploaded PNG through the full pipeline."""
    if "file" not in request.files:
        return jsonify({"error": "No file uploaded"}), 400

    file = request.files["file"]
    if file.filename == "":
        return jsonify({"error": "Empty filename"}), 400

    if not file.filename.lower().endswith(".png"):
        return jsonify({"error": "Only PNG files are supported"}), 400

    png_bytes = file.read()
    if not png_bytes:
        return jsonify({"error": "Empty file"}), 400

    thickness = float(request.form.get("thickness", 10))
    detail = float(request.form.get("detail", 0.5))
    speckle_area = float(request.form.get("speckle_area", 10))
    preset = request.form.get("preset", "clean")
    alpha_threshold = int(request.form.get("alpha_threshold", 128))

    try:
        result = process_image(
            png_bytes,
            thickness=thickness,
            preset=preset,
            detail=detail,
            speckle_area=speckle_area,
            alpha_threshold=alpha_threshold,
        )
        return jsonify(result)
    except Exception as e:
        return jsonify({"error": str(e)}), 500


if __name__ == "__main__":
    multiprocessing.freeze_support()
    plato_family.start(_scheduler, _store, _DATA_DIR)  # family bus: plato.export.done when an STL export finishes
    # Reloader disabled: it would spawn a second process and duplicate the
    # job worker pool (spec §13.2).  Use debug=False in production.
    app.run(host=HOST, port=PORT, debug=False, use_reloader=False)
