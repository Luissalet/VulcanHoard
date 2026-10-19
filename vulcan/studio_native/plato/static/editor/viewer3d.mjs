// viewer3d.mjs — 3D assembly viewer (Z-up, canvas in XY, thickness in Z)
import { store } from './store.js';
import { pathToLocalMatrix } from './affine.mjs';

function affineMatrix(p) {
  const t = p.angle_deg * Math.PI / 180;
  const c = p.scale * Math.cos(t);
  const s = p.scale * Math.sin(t);
  return [c, s, -s, c, p.tx, p.ty];
}
const FLIP_H = [-1, 0, 0, 1, 0, 0];
function layerAffineMatrix(node) {
  let m = affineMatrix(node.pose);
  if (node.flip_h) m = matMul(m, FLIP_H);
  return m;
}
function matMul(A, B) {
  const [a, b, c, d, e, f] = A, [g, h, i, j, k, l] = B;
  return [a * g + c * h, b * g + d * h, a * i + c * j, b * i + d * j, a * k + c * l + e, b * k + d * l + f];
}

function _layerWorldMatrix(layerId, asset) {
  const chain = [];
  let cur = layerId;
  const seen = new Set();
  while (cur && !seen.has(cur)) {
    seen.add(cur);
    const node = store.layerById(cur);
    if (!node) break;
    // flip_h mirrors only the layer itself; ancestors contribute pose only.
    chain.unshift(cur === layerId ? layerAffineMatrix(node) : affineMatrix(node.pose));
    cur = node.parent_id;
  }
  let m = [1, 0, 0, 1, 0, 0];
  for (const lm of chain) m = matMul(m, lm);
  if (asset) {
    // path coords → local mm (N + viewBox origin)
    const N = pathToLocalMatrix(asset);
    m = matMul(m, N);
  }
  return m;
}

function _effectiveVisible(node) {
  let cur = node;
  const seen = new Set();
  while (cur && !seen.has(cur.id)) {
    seen.add(cur.id);
    if (!cur.visible) return false;
    cur = cur.parent_id ? store.layerById(cur.parent_id) : null;
  }
  return true;
}

function _extrusionOf(node) {
  const v = node.extrusion_mm;
  return (v === null || v === undefined) ? (store.doc?.default_extrusion_mm ?? 3) : v;
}

/** Normal hierarchical Z: parent below, children progressively above. */
function _stackZNormal(layerId) {
  const gap = store.doc?.stack_gap_mm ?? 0.4;
  const node = store.layerById(layerId);
  if (!node) return 0;
  if (node.parent_id) {
    const parent = store.layerById(node.parent_id);
    if (!parent) return 0;
    const sibs = store.childrenOf(node.parent_id);
    const idx = Math.max(0, sibs.findIndex((s) => s.id === layerId));
    return _stackZNormal(node.parent_id) + _extrusionOf(parent) + gap + idx * 0.05;
  }
  const roots = store.roots().slice().sort((a, b) => (a.stack_rank ?? 0) - (b.stack_rank ?? 0));
  let z = 0;
  for (const r of roots) {
    if (r.id === layerId) break;
    if (!_effectiveVisible(r)) continue;
    z += _extrusionOf(r) + gap;
  }
  return z;
}

function _isFrameFloor(node) {
  if (!_isFrameLayer(node)) return false;
  const name = node.name || '';
  // Single solid Marco tray counts as floor for content stacking.
  return name.includes('fondo') || name === 'Marco';
}

function _isFrameWalls(node) {
  if (!_isFrameLayer(node)) return false;
  return (node.name || '').includes('paredes');
}

/** Floor thickness for content Z — tray uses floor_h, not full wall height. */
function _frameFloorThickness(node) {
  const extr = _extrusionOf(node);
  if ((node.name || '') === 'Marco') {
    const asset = store.doc?.assets?.[node.asset_id];
    const fh = Number(asset?.trace_settings?.floor_h_mm);
    if (Number.isFinite(fh) && fh > 0) return Math.min(fh, extr);
    return Math.min(3, extr * 0.45);
  }
  return extr;
}

/** Top of the box floor — content sits here; taller walls do NOT push content. */
function _fondoTop() {
  const gap = store.doc?.stack_gap_mm ?? 0.4;
  let h = 0;
  for (const l of Object.values(store.doc?.layers || {})) {
    if (!_effectiveVisible(l) || !_isFrameFloor(l)) continue;
    h = Math.max(h, _frameFloorThickness(l));
  }
  return h > 0 ? h + gap : 0;
}

/**
 * Marco (solid tray) at z=0; legacy paredes on the floor.
 * Content stacks on the floor, ignoring wall height.
 */
function _stackZFrame(layerId) {
  const node = store.layerById(layerId);
  if (!node) return 0;
  if (_isFrameWalls(node)) return _fondoTop();
  return 0; // fondo / solid Marco tray
}

/** Content / silhouette stack starting on top of the floor only. */
function _stackZContent(layerId) {
  const gap = store.doc?.stack_gap_mm ?? 0.4;
  const node = store.layerById(layerId);
  if (!node) return 0;
  const base = _fondoTop();
  if (node.parent_id) {
    const parent = store.layerById(node.parent_id);
    if (!parent) return base;
    const sibs = store.childrenOf(node.parent_id).filter((s) => !_isFrameLayer(s));
    const idx = Math.max(0, sibs.findIndex((s) => s.id === layerId));
    return _stackZContent(node.parent_id) + _extrusionOf(parent) + gap + idx * 0.05;
  }
  const roots = store.roots()
    .filter((r) => !_isFrameLayer(r))
    .slice()
    .sort((a, b) => (a.stack_rank ?? 0) - (b.stack_rank ?? 0));
  let z = base;
  for (const r of roots) {
    if (r.id === layerId) break;
    if (!_effectiveVisible(r)) continue;
    z += _extrusionOf(r) + gap;
  }
  return z;
}

/**
 * Normal: parent below, children above (on the box floor).
 * Inverse: flip content only — Marco always stays at the bottom; wall height ignored for content Z.
 */
function _stackZ(layerId, mode = 'normal') {
  const node = store.layerById(layerId);
  if (!node) return 0;

  if (_isFrameLayer(node)) return _stackZFrame(layerId);

  const normalZ = _stackZContent(layerId);
  if (mode !== 'inverse') return normalZ;

  const base = _fondoTop();
  let maxRelTop = 0;
  for (const l of Object.values(store.doc?.layers || {})) {
    if (!_effectiveVisible(l) || _isFrameLayer(l)) continue;
    const rel = Math.max(0, _stackZContent(l.id) - base);
    maxRelTop = Math.max(maxRelTop, rel + _extrusionOf(l));
  }
  const rel = Math.max(0, normalZ - base);
  return base + Math.max(0, maxRelTop - rel - _extrusionOf(node));
}

function _isFrameLayer(node) {
  const name = node?.name || '';
  return name === 'Marco' || name === 'Marco fondo' || name === 'Marco paredes'
    || String(node?.id || '').startsWith('layer_marco_');
}

function _allLayerIds() {
  return Object.keys(store.doc?.layers || {});
}

/**
 * @param {HTMLElement} container
 * @param {{ mode?: 'normal'|'inverse' }} [opts]  Fixed view: the editor mounts
 *   one viewer per mode so both assemblies are always visible.
 */
export function mountViewer3D(container, { mode = 'normal' } = {}) {
  const viewMode = mode === 'inverse' ? 'inverse' : 'normal';
  if (typeof THREE === 'undefined') {
    container.innerHTML = '<p class="empty-state">Three.js no disponible.</p>';
    return { update: () => {}, dispose: () => {} };
  }

  container.innerHTML = '';
  container.classList.add('viewer-3d-host');

  const scene = new THREE.Scene();
  scene.background = new THREE.Color(0x1f2937);

  // Z-up: canvas lies on XY, thickness along +Z
  const camera = new THREE.PerspectiveCamera(40, 1, 0.1, 100000);
  camera.up.set(0, 0, 1);

  const renderer = new THREE.WebGLRenderer({ antialias: true, alpha: false });
  renderer.setPixelRatio(Math.min(window.devicePixelRatio || 1, 2));
  renderer.setClearColor(0x1f2937, 1);
  const canvas = renderer.domElement;
  canvas.className = 'viewer-3d-canvas';
  canvas.style.display = 'block';
  canvas.style.width = '100%';
  canvas.style.height = '100%';
  container.appendChild(canvas);

  scene.add(new THREE.AmbientLight(0xffffff, 0.55));
  const dir = new THREE.DirectionalLight(0xffffff, 0.85);
  dir.position.set(1, -1.2, 2);
  scene.add(dir);
  const fill = new THREE.DirectionalLight(0xb0c4de, 0.35);
  fill.position.set(-1, 0.5, 1);
  scene.add(fill);

  // Grid in XY (rotate default XZ grid)
  let grid = new THREE.GridHelper(200, 20, 0x6b7280, 0x374151);
  grid.rotation.x = Math.PI / 2;
  scene.add(grid);

  // Canvas outline (physical sheet)
  const sheetMat = new THREE.LineBasicMaterial({ color: 0x9ca3af });
  let sheet = null;

  let controls = null;
  if (THREE.OrbitControls) {
    controls = new THREE.OrbitControls(camera, canvas);
    controls.enableDamping = true;
    controls.dampingFactor = 0.08;
    controls.enablePan = true;
    controls.screenSpacePanning = true;
    controls.enableZoom = true;
    controls.enableRotate = true;
    controls.mouseButtons = {
      LEFT: THREE.MOUSE.ROTATE,
      MIDDLE: THREE.MOUSE.DOLLY,
      RIGHT: THREE.MOUSE.PAN,
    };
    canvas.addEventListener('contextmenu', (e) => e.preventDefault());
  }

  const assembly = new THREE.Group();
  scene.add(assembly);

  let _lastW = 0, _lastH = 0;
  function resize() {
    const rect = container.getBoundingClientRect();
    const w = Math.max(2, Math.floor(rect.width));
    const h = Math.max(2, Math.floor(rect.height));
    if (w === _lastW && h === _lastH) return;
    _lastW = w;
    _lastH = h;
    renderer.setSize(w, h, false);
    canvas.style.width = '100%';
    canvas.style.height = '100%';
    camera.aspect = w / h;
    camera.updateProjectionMatrix();
  }

  const ro = (typeof ResizeObserver !== 'undefined')
    ? new ResizeObserver(() => { resize(); })
    : null;
  if (ro) ro.observe(container);
  window.addEventListener('resize', resize);
  // Layout may settle after first paint
  requestAnimationFrame(() => { resize(); frameStable(); });
  setTimeout(() => { resize(); frameStable(); }, 100);

  function syncSheetAndGrid() {
    const c = store.doc?.canvas;
    const W = c ? c.width_mm : 200;
    const H = c ? c.height_mm : 200;
    if (sheet) scene.remove(sheet);
    const pts = [
      new THREE.Vector3(0, 0, 0),
      new THREE.Vector3(W, 0, 0),
      new THREE.Vector3(W, H, 0),
      new THREE.Vector3(0, H, 0),
      new THREE.Vector3(0, 0, 0),
    ];
    sheet = new THREE.Line(new THREE.BufferGeometry().setFromPoints(pts), sheetMat);
    scene.add(sheet);

    scene.remove(grid);
    const gSize = Math.max(W, H) * 1.15;
    const divs = 20;
    grid = new THREE.GridHelper(gSize, divs, 0x6b7280, 0x374151);
    grid.rotation.x = Math.PI / 2;
    grid.position.set(W / 2, H / 2, -0.05);
    scene.add(grid);
  }

  function frameStable() {
    resize();
    const c = store.doc?.canvas;
    const W = c ? c.width_mm : 200;
    const H = c ? c.height_mm : 200;
    let maxZ = 3;
    for (const l of Object.values(store.doc?.layers || {})) {
      if (_effectiveVisible(l)) maxZ = Math.max(maxZ, _stackZ(l.id, viewMode) + _extrusionOf(l));
    }
    const cx = W / 2;
    const cy = H / 2;
    const span = Math.max(W, H, maxZ * 4);
    const dist = span * 1.6;
    camera.position.set(cx + dist * 0.55, cy - dist * 0.7, maxZ + dist * 0.65);
    camera.lookAt(cx, cy, maxZ * 0.35);
    if (controls) {
      controls.target.set(cx, cy, maxZ * 0.35);
      controls.update();
    }
  }

  function _svgPaths(asset) {
    if (!asset?.canonical_svg) return [];
    const parsed = new DOMParser().parseFromString(asset.canonical_svg, 'image/svg+xml');
    return [...parsed.querySelectorAll('path')].map(p => p.getAttribute('d')).filter(Boolean);
  }

  /**
   * Parse one SVG `d` into contour Paths (full curves — no sampling).
   * Each M/m starts a new contour (never lineTo across subpaths).
   */
  function _parseContours(d) {
    const tokens = String(d).match(/[a-df-z]|[-+]?(?:\d+\.?\d*|\.\d+)(?:[eE][-+]?\d+)?/gi) || [];
    const contours = [];
    let i = 0;
    const num = () => parseFloat(tokens[i++]);
    let path = null;
    let cx = 0, cy = 0, sx = 0, sy = 0;

    const ensure = () => {
      if (!path) {
        path = new THREE.Path();
        contours.push(path);
      }
    };
    const startContour = (x, y) => {
      path = new THREE.Path();
      contours.push(path);
      path.moveTo(x, y);
      cx = x; cy = y; sx = x; sy = y;
    };

    try {
      while (i < tokens.length) {
        const cmd = tokens[i++];
        switch (cmd) {
          case 'M': {
            startContour(num(), num());
            // Implicit lineTos after first pair
            while (i < tokens.length && !/[a-zA-Z]/.test(tokens[i])) {
              cx = num(); cy = num();
              path.lineTo(cx, cy);
            }
            break;
          }
          case 'm': {
            startContour(cx + num(), cy + num());
            while (i < tokens.length && !/[a-zA-Z]/.test(tokens[i])) {
              cx += num(); cy += num();
              path.lineTo(cx, cy);
            }
            break;
          }
          case 'L': ensure(); cx = num(); cy = num(); path.lineTo(cx, cy); break;
          case 'l': ensure(); cx += num(); cy += num(); path.lineTo(cx, cy); break;
          case 'H': ensure(); cx = num(); path.lineTo(cx, cy); break;
          case 'h': ensure(); cx += num(); path.lineTo(cx, cy); break;
          case 'V': ensure(); cy = num(); path.lineTo(cx, cy); break;
          case 'v': ensure(); cy += num(); path.lineTo(cx, cy); break;
          case 'C': {
            ensure();
            const x1 = num(), y1 = num(), x2 = num(), y2 = num(), x = num(), y = num();
            path.bezierCurveTo(x1, y1, x2, y2, x, y); cx = x; cy = y; break;
          }
          case 'c': {
            ensure();
            const x1 = cx + num(), y1 = cy + num(), x2 = cx + num(), y2 = cy + num(), x = cx + num(), y = cy + num();
            path.bezierCurveTo(x1, y1, x2, y2, x, y); cx = x; cy = y; break;
          }
          case 'S': {
            ensure();
            const x2 = num(), y2 = num(), x = num(), y = num();
            path.bezierCurveTo(cx, cy, x2, y2, x, y); cx = x; cy = y; break;
          }
          case 's': {
            ensure();
            const x2 = cx + num(), y2 = cy + num(), x = cx + num(), y = cy + num();
            path.bezierCurveTo(cx, cy, x2, y2, x, y); cx = x; cy = y; break;
          }
          case 'Q': {
            ensure();
            const x1 = num(), y1 = num(), x = num(), y = num();
            path.quadraticCurveTo(x1, y1, x, y); cx = x; cy = y; break;
          }
          case 'q': {
            ensure();
            const x1 = cx + num(), y1 = cy + num(), x = cx + num(), y = cy + num();
            path.quadraticCurveTo(x1, y1, x, y); cx = x; cy = y; break;
          }
          case 'T': {
            ensure();
            const x = num(), y = num();
            path.quadraticCurveTo(cx, cy, x, y); cx = x; cy = y; break;
          }
          case 't': {
            ensure();
            const x = cx + num(), y = cy + num();
            path.quadraticCurveTo(cx, cy, x, y); cx = x; cy = y; break;
          }
          case 'A': {
            // Approximate arc with cubic beziers via endpoint sample — keep density high
            ensure();
            num(); num(); num(); num(); num();
            const x = num(), y = num();
            path.lineTo(x, y); cx = x; cy = y; break;
          }
          case 'a': {
            ensure();
            num(); num(); num(); num(); num();
            const x = cx + num(), y = cy + num();
            path.lineTo(x, y); cx = x; cy = y; break;
          }
          case 'Z': case 'z':
            if (path) { path.closePath(); cx = sx; cy = sy; }
            break;
          default:
            return [];
        }
      }
    } catch {
      return [];
    }
    return contours.filter((p) => p.curves && p.curves.length > 0);
  }

  function _contourArea(path) {
    // Classification only — uses a fine polyline; extrusion keeps original curves.
    const pts = path.getPoints(24);
    let a = 0;
    for (let i = 0, j = pts.length - 1; i < pts.length; j = i++) {
      a += pts[j].x * pts[i].y - pts[i].x * pts[j].y;
    }
    return a * 0.5;
  }

  function _contourCentroid(path) {
    const pts = path.getPoints(24);
    let cx = 0, cy = 0;
    for (const p of pts) { cx += p.x; cy += p.y; }
    return { x: cx / pts.length, y: cy / pts.length };
  }

  function _pathContains(path, x, y) {
    const pts = path.getPoints(24);
    let inside = false;
    for (let i = 0, j = pts.length - 1; i < pts.length; j = i++) {
      const xi = pts[i].x, yi = pts[i].y;
      const xj = pts[j].x, yj = pts[j].y;
      const hit = ((yi > y) !== (yj > y))
        && (x < ((xj - xi) * (y - yi)) / ((yj - yi) || 1e-12) + xi);
      if (hit) inside = !inside;
    }
    return inside;
  }

  /** Full-fidelity THREE.Shapes from SVG path `d`s (curves preserved). */
  function _shapesFromSvgPaths(pathDs) {
    const contours = [];
    for (const d of pathDs) {
      for (const path of _parseContours(d)) {
        const area = _contourArea(path);
        const c = _contourCentroid(path);
        contours.push({ path, area: Math.abs(area), signed: area, cx: c.x, cy: c.y });
      }
    }
    if (!contours.length) return [];
    contours.sort((a, b) => b.area - a.area);

    const solids = [];
    for (const c of contours) {
      let parent = null;
      for (const s of solids) {
        if (_pathContains(s.path, c.cx, c.cy) && c.area < s.area * 0.98) {
          parent = s;
          break;
        }
      }
      if (parent) {
        parent.shape.holes.push(c.path);
      } else {
        const shape = new THREE.Shape();
        // Copy curves from Path into Shape
        shape.curves = c.path.curves.slice();
        shape.currentPoint.copy(c.path.currentPoint);
        solids.push({ shape, path: c.path, area: c.area });
      }
    }
    return solids.map((s) => s.shape);
  }

  function _layerColor(layerId) {
    // Stable colour matching 2D (do not recolour selection — that looked like a blue rim).
    const ids = Object.keys(store.doc?.layers || {});
    const i = Math.max(0, ids.indexOf(layerId));
    const hue = (i * 137.508) % 360;
    const h = hue / 360, s = 0.55, l = 0.5;
    const aa = s * Math.min(l, 1 - l);
    const fn = (n) => {
      const k = (n + h * 12) % 12;
      return l - aa * Math.max(Math.min(k - 3, 9 - k, 1), -1);
    };
    const r = Math.round(fn(0) * 255), g = Math.round(fn(8) * 255), bl = Math.round(fn(4) * 255);
    return (r << 16) | (g << 8) | bl;
  }

  /** Affine-transform a Path/Shape keeping every curve control point (no resampling). */
  function transformPathAffine(src, m) {
    const [a, b, c, d, e, f] = m;
    const map = (p) => new THREE.Vector2(a * p.x + c * p.y + e, b * p.x + d * p.y + f);
    const out = src.isShape || src.type === 'Shape' ? new THREE.Shape() : new THREE.Path();

    // Replay curves with transformed control points
    let started = false;
    const moveTo = (p) => {
      const q = map(p);
      out.moveTo(q.x, q.y);
      started = true;
    };
    for (const curve of src.curves || []) {
      if (curve.type === 'LineCurve') {
        if (!started) moveTo(curve.v1);
        const q = map(curve.v2);
        out.lineTo(q.x, q.y);
      } else if (curve.type === 'CubicBezierCurve') {
        if (!started) moveTo(curve.v0);
        const c1 = map(curve.v1), c2 = map(curve.v2), c3 = map(curve.v3);
        out.bezierCurveTo(c1.x, c1.y, c2.x, c2.y, c3.x, c3.y);
      } else if (curve.type === 'QuadraticBezierCurve') {
        if (!started) moveTo(curve.v0);
        const c1 = map(curve.v1), c2 = map(curve.v2);
        out.quadraticCurveTo(c1.x, c1.y, c2.x, c2.y);
      } else {
        // Fallback: densify this curve only
        const pts = curve.getPoints(16);
        for (let i = 0; i < pts.length; i++) {
          const q = map(pts[i]);
          if (!started) { out.moveTo(q.x, q.y); started = true; }
          else out.lineTo(q.x, q.y);
        }
      }
    }
    if (!started) return null;

    // Transform holes recursively
    if (src.holes && src.holes.length) {
      out.holes = [];
      for (const hole of src.holes) {
        const th = transformPathAffine(hole, m);
        if (th) out.holes.push(th);
      }
    }
    return out;
  }

  function _worldShapes(layerId) {
    const node = store.layerById(layerId);
    const asset = store.assetById(node?.asset_id);
    if (!asset) return [];
    const m = _layerWorldMatrix(layerId, asset);
    const locals = _shapesFromSvgPaths(_svgPaths(asset));
    const out = [];
    for (const local of locals) {
      const world = transformPathAffine(local, m);
      if (world) out.push(world);
    }
    return out;
  }

  /** Document-space geometry → manufacturing mesh (Y flip). */
  function _meshFromDocShapes(shapes, depth, z0, color, name) {
    if (!shapes?.length) return null;
    const canvasH = store.doc?.canvas?.height_mm ?? 200;
    const geo = new THREE.ExtrudeGeometry(shapes, {
      depth, bevelEnabled: false, steps: 1, curveSegments: 24,
    });
    const mat4 = new THREE.Matrix4().set(
      1, 0, 0, 0,
      0, -1, 0, canvasH,
      0, 0, 1, z0,
      0, 0, 0, 1,
    );
    geo.applyMatrix4(mat4);
    const mesh = new THREE.Mesh(geo, new THREE.MeshStandardMaterial({
      color, roughness: 0.65, metalness: 0.08, side: THREE.DoubleSide,
    }));
    mesh.name = name || '';
    return mesh;
  }

  function buildLayerMesh(layerId) {
    const node = store.layerById(layerId);
    const asset = store.assetById(node.asset_id);
    if (!asset?.canonical_svg) return null;

    const ts = asset.trace_settings || {};
    if (ts.kind === 'procedural_frame_tray' || (node.name || '') === 'Marco') {
      const tray = buildTrayMesh(layerId, asset, node);
      if (tray) return tray;
    }

    const m = _layerWorldMatrix(layerId, asset);
    const canvasH = store.doc?.canvas?.height_mm ?? 200;
    const t = _extrusionOf(node);
    const z0 = _stackZ(layerId, viewMode);

    const cacheKey = `${asset.id || node.asset_id}|${asset.geometry_hash || asset.canonical_svg}|${t}`;
    let baseGeo = geometryCache.get(cacheKey);
    let geo;
    try {
      if (!baseGeo) {
        const paths = _svgPaths(asset);
        if (!paths.length) return null;
        const shapes = _shapesFromSvgPaths(paths);
        if (!shapes.length) return null;
        baseGeo = new THREE.ExtrudeGeometry(shapes, {
          depth: t,
          bevelEnabled: false,
          steps: 1,
          curveSegments: 24,
        });
        geometryCache.set(cacheKey, baseGeo);
        if (geometryCache.size > 64) {
          const oldest = geometryCache.keys().next().value;
          const stale = geometryCache.get(oldest);
          stale?.dispose?.();
          geometryCache.delete(oldest);
        }
      }
      // Keep the cached geometry in source-local coordinates. Each instance
      // receives its own clone before pose/flip/Y/Z transforms are applied.
      geo = baseGeo.clone();
    } catch (err) {
      console.warn('extrude failed', layerId, err);
      return null;
    }

    // SVG affine [a b c d e f]: x' = a x + c y + e, y' = b x + d y + f
    // Manufacturing: Y_mfg = H − y_doc. Thickness stays +Z.
    const [a, b, c, d, e, f] = m;
    const mat = new THREE.Matrix4().set(
      a,  c, 0, e,
      -b, -d, 0, canvasH - f,
      0,  0, 1, z0,
      0,  0, 0, 1,
    );
    geo.applyMatrix4(mat);

    const mesh = new THREE.Mesh(geo, new THREE.MeshStandardMaterial({
      color: _layerColor(layerId), roughness: 0.65, metalness: 0.08, side: THREE.DoubleSide,
    }));
    mesh.name = layerId;
    return mesh;
  }

  /** Solid open-top tray as one manifold mesh (no floor∥wall internal faces).
   * Geometry is built in SVG source units (0..ow, 0..oh) so the same
   * pathToLocalMatrix + pose chain as other layers centers it correctly.
   */
  function buildTrayMesh(layerId, asset, node) {
    const ts = asset.trace_settings || {};
    const wallW = Number(ts.wall_w_mm);
    const wallH = _extrusionOf(node);
    let floorH = Number(ts.floor_h_mm);
    if (!(floorH > 0) || floorH >= wallH) floorH = Math.min(3, wallH * 0.45);
    if (!(wallW > 0) || !(wallH > floorH)) return null;

    const ow = Number(ts.outer_w) || Number(asset.source_viewbox?.[2]);
    const oh = Number(ts.outer_h) || Number(asset.source_viewbox?.[3]);
    if (!(ow > 2 * wallW) || !(oh > 2 * wallW)) return null;

    const geo = _openRectTrayGeometry(0, 0, ow, oh, wallW, wallH, floorH);
    if (!geo) return null;

    const m = _layerWorldMatrix(layerId, asset);
    const canvasH = store.doc?.canvas?.height_mm ?? 200;
    const z0 = _stackZ(layerId, viewMode);
    const [a, b, c, d, e, f] = m;
    const mat = new THREE.Matrix4().set(
      a, c, 0, e,
      -b, -d, 0, canvasH - f,
      0, 0, 1, z0,
      0, 0, 0, 1,
    );
    geo.applyMatrix4(mat);
    // Manufacturing Y-flip makes det(mat) < 0 and reverses winding — fix for FrontSide.
    const index = geo.getIndex();
    if (index) {
      const arr = index.array;
      for (let i = 0; i < arr.length; i += 3) {
        const t = arr[i + 1];
        arr[i + 1] = arr[i + 2];
        arr[i + 2] = t;
      }
      index.needsUpdate = true;
    }
    geo.computeVertexNormals();

    // Flat shading: the tray is a box — hard edges, no smoothed corners.
    const mesh = new THREE.Mesh(geo, new THREE.MeshStandardMaterial({
      color: 0x9aa3b2, roughness: 0.8, metalness: 0.05, side: THREE.FrontSide,
      flatShading: true,
    }));
    mesh.name = layerId;
    return mesh;
  }

  /** Manifold open-top rectangular tray: exterior faces only. */
  function _openRectTrayGeometry(x0, y0, x1, y1, wallW, wallH, floorH) {
    const ix0 = x0 + wallW, iy0 = y0 + wallW;
    const ix1 = x1 - wallW, iy1 = y1 - wallW;
    if (!(ix1 > ix0) || !(iy1 > iy0) || !(floorH > 0) || !(wallH > floorH)) return null;

    const pos = [];
    const idx = [];
    const push = (x, y, z) => {
      pos.push(x, y, z);
      return (pos.length / 3) - 1;
    };
    const quad = (a, b, c, d) => {
      idx.push(a, b, c, a, c, d);
    };
    const ring = (xa, ya, xb, yb, z) => [
      push(xa, ya, z),
      push(xb, ya, z),
      push(xb, yb, z),
      push(xa, yb, z),
    ];

    const ob = ring(x0, y0, x1, y1, 0);
    const ot = ring(x0, y0, x1, y1, wallH);
    const iff = ring(ix0, iy0, ix1, iy1, floorH);
    const it = ring(ix0, iy0, ix1, iy1, wallH);

    // Bottom (-Z)
    quad(ob[0], ob[3], ob[2], ob[1]);
    // Cavity floor (+Z)
    quad(iff[0], iff[1], iff[2], iff[3]);
    // Rim top
    for (let i = 0; i < 4; i++) {
      const j = (i + 1) % 4;
      quad(ot[i], ot[j], it[j], it[i]);
    }
    // Outer walls
    for (let i = 0; i < 4; i++) {
      const j = (i + 1) % 4;
      quad(ob[i], ob[j], ot[j], ot[i]);
    }
    // Inner walls (normals into cavity)
    for (let i = 0; i < 4; i++) {
      const j = (i + 1) % 4;
      quad(iff[j], iff[i], it[i], it[j]);
    }

    const geo = new THREE.BufferGeometry();
    geo.setAttribute('position', new THREE.Float32BufferAttribute(pos, 3));
    geo.setIndex(idx);
    geo.computeVertexNormals();
    return geo;
  }

  /** One plate = canvas − THIS layer only (children are separate plates). */
  function buildInverseMesh(layerId) {
    const node = store.layerById(layerId);
    if (!node || !_effectiveVisible(node)) return null;
    const c = store.doc?.canvas;
    if (!c) return null;
    const W = c.width_mm, H = c.height_mm;
    const plate = new THREE.Shape();
    plate.moveTo(0, 0);
    plate.lineTo(W, 0);
    plate.lineTo(W, H);
    plate.lineTo(0, H);
    plate.closePath();

    const islands = [];
    for (const sil of _worldShapes(layerId)) {
      const outer = new THREE.Path();
      outer.curves = (sil.curves || []).slice();
      if (sil.currentPoint) outer.currentPoint.copy(sil.currentPoint);
      plate.holes.push(outer);
      for (const h of sil.holes || []) {
        const island = new THREE.Shape();
        island.curves = (h.curves || []).slice();
        if (h.currentPoint) island.currentPoint.copy(h.currentPoint);
        islands.push(island);
      }
    }
    if (!plate.holes.length) return null;

    try {
      const t = _extrusionOf(node);
      const z0 = _stackZ(layerId, viewMode);
      return _meshFromDocShapes([plate, ...islands], t, z0, _layerColor(layerId), `inv-${layerId}`);
    } catch (err) {
      console.warn('inverse mesh failed', layerId, err);
      return null;
    }
  }

  /** Build mesh from server Shapely rings (full evenodd + islands). */
  function _meshFromRings(rings, depth, z0, color, name) {
    if (!rings?.length) return null;
    const shapes = [];
    for (const ring of rings) {
      const ext = ring.exterior;
      if (!ext || ext.length < 3) continue;
      const shape = new THREE.Shape();
      shape.moveTo(ext[0][0], ext[0][1]);
      for (let i = 1; i < ext.length; i++) shape.lineTo(ext[i][0], ext[i][1]);
      shape.closePath();
      for (const hole of ring.holes || []) {
        if (!hole || hole.length < 3) continue;
        const hp = new THREE.Path();
        hp.moveTo(hole[0][0], hole[0][1]);
        for (let i = 1; i < hole.length; i++) hp.lineTo(hole[i][0], hole[i][1]);
        hp.closePath();
        shape.holes.push(hp);
      }
      shapes.push(shape);
    }
    return _meshFromDocShapes(shapes, depth, z0, color, name);
  }

  /** Parent silhouette with child silhouettes cut out. */
  function buildShellMesh(layerId) {
    const node = store.layerById(layerId);
    const asset = store.assetById(node?.asset_id);
    if (!asset?.canonical_svg) return null;

    const children = store.childrenOf(layerId).filter(
      (ch) => _effectiveVisible(ch) && store.assetById(ch.asset_id)?.canonical_svg,
    );
    if (!children.length) return buildLayerMesh(layerId);

    const parentShapes = _worldShapes(layerId);
    if (!parentShapes.length) return null;

    const holeShapes = [];
    for (const ch of children) {
      for (const h of _worldShapes(ch.id)) holeShapes.push(h);
    }

    // Attach all child holes to the first (outer) parent contour; extrude remaining
    // parent contours as solids (rare multi-path assets).
    const outer = parentShapes[0];
    for (const h of holeShapes) outer.holes.push(h);
    const shapes = [outer, ...parentShapes.slice(1)];
    const t = _extrusionOf(node);
    const z0 = _stackZ(layerId, viewMode);
    return _meshFromDocShapes(shapes, t, z0, _layerColor(layerId), `shell-${layerId}`);
  }

  let _inverseSeq = 0;
  const geometryCache = new Map();
  let disposed = false;
  let updateRaf = null;
  let updateQueued = false;
  let rebuildQueued = false;
  let lastBuiltDoc = null;
  let lastBuiltRevision = null;
  let inverseAbort = null;

  function clearAssembly() {
    while (assembly.children.length) {
      const ch = assembly.children[0];
      assembly.remove(ch);
      ch.traverse?.((o) => {
        o.geometry?.dispose?.();
        if (Array.isArray(o.material)) o.material.forEach((m) => m.dispose?.());
        else o.material?.dispose?.();
      });
      if (!ch.traverse) {
        ch.geometry?.dispose?.();
        ch.material?.dispose?.();
      }
    }
  }

  function setMeshLayer(mesh, layerId) {
    if (mesh) mesh.userData.layerId = layerId;
    return mesh;
  }

  function meshLayerId(mesh) {
    if (mesh?.userData?.layerId) return mesh.userData.layerId;
    const name = String(mesh?.name || '');
    return name.replace(/^(?:inv|shell)-/, '') || null;
  }

  function applySelection() {
    const selected = store.selectedId;
    assembly.traverse((obj) => {
      if (!obj.material) return;
      const id = meshLayerId(obj);
      const paint = (mat) => {
        if (!mat) return;
        if (id === selected) {
          mat.emissive?.set?.(0x1d4ed8);
          mat.emissiveIntensity = 0.25;
        } else {
          mat.emissive?.set?.(0x000000);
          mat.emissiveIntensity = 0;
        }
      };
      if (Array.isArray(obj.material)) obj.material.forEach(paint);
      else paint(obj.material);
    });
  }

  async function _loadInverseFromServer(targets) {
    const seq = ++_inverseSeq;
    const rev = store.revision;
    const docRef = store.doc;
    inverseAbort?.abort?.();
    inverseAbort = typeof AbortController !== 'undefined' ? new AbortController() : null;
    try {
      const res = await fetch(
        `${store.baseUrl}/documents/${encodeURIComponent(store.doc.id)}/mesh-rings`,
        {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({
            mode: 'inverse',
            layer_ids: targets,
            include_subtree: false,
          }),
          signal: inverseAbort?.signal,
        },
      );
      const body = await res.json().catch(() => ({}));
      if (!res.ok) throw new Error(body?.error?.message || `HTTP ${res.status}`);
      if (disposed || seq !== _inverseSeq || store.doc !== docRef || store.revision !== rev) return;

      clearAssembly();
      syncSheetAndGrid();

      // Keep procedural Marco as a normal ring under/around inverse plates.
      for (const lid of Object.keys(store.doc?.layers || {})) {
        const n = store.layerById(lid);
        if (!n || !_effectiveVisible(n) || !_isFrameLayer(n)) continue;
        const fm = buildLayerMesh(lid);
        if (fm) assembly.add(fm);
      }

      for (const layerId of targets) {
        const entry = body.layers?.[layerId];
        if (!entry?.rings?.length) continue;
        const node = store.layerById(layerId);
        if (!node || !_effectiveVisible(node)) continue;
        const t = entry.extrusion_mm ?? _extrusionOf(node);
        const z0 = _stackZ(layerId, viewMode);
        const mesh = _meshFromRings(entry.rings, t, z0, _layerColor(layerId), `inv-${layerId}`);
        if (mesh) {
          if (layerId === store.selectedId) {
            mesh.material.emissive = new THREE.Color(0x1d4ed8);
            mesh.material.emissiveIntensity = 0.2;
          }
          assembly.add(setMeshLayer(mesh, layerId));
        }
      }
      frameStable();
    } catch (err) {
      console.warn('inverse mesh-rings failed, client fallback', err);
      // Only the current request may install the client fallback; stale
      // failures must leave the newer assembly untouched.
      if (disposed || seq !== _inverseSeq || store.doc !== docRef || store.revision !== rev) return;
      clearAssembly();
      syncSheetAndGrid();
      for (const lid of Object.keys(store.doc?.layers || {})) {
        const n = store.layerById(lid);
        if (!n || !_effectiveVisible(n) || !_isFrameLayer(n)) continue;
        const fm = buildLayerMesh(lid);
        if (fm) assembly.add(fm);
      }
      for (const layerId of targets) {
        const mesh = buildInverseMesh(layerId);
        if (mesh) assembly.add(setMeshLayer(mesh, layerId));
      }
      frameStable();
    }
  }

  function rebuild() {
    if (disposed) return;
    resize();
    // Keep the last inverse preview visible while its updated cut-outs load.
    // Clearing it on every pose change made the panel flash empty repeatedly.
    const keepInverse = viewMode === 'inverse' && store.doc && lastBuiltDoc?.id === store.doc.id;
    if (!keepInverse) clearAssembly();
    syncSheetAndGrid();
    if (!store.doc) { frameStable(); return; }

    const mode = viewMode;
    const targets = _allLayerIds();

    if (mode === 'inverse') {
      const invTargets = targets.filter((id) => {
        const n = store.layerById(id);
        return n && !_isFrameLayer(n);
      });
      const frameIds = targets.filter((id) => {
        const n = store.layerById(id);
        return n && _effectiveVisible(n) && _isFrameLayer(n);
      });
      // Add frame rings immediately; inverse plates load async.
      if (!keepInverse) {
        for (const layerId of frameIds) {
          const mesh = buildLayerMesh(layerId);
          if (mesh) assembly.add(setMeshLayer(mesh, layerId));
        }
      }
      _loadInverseFromServer(invTargets);
      lastBuiltDoc = store.doc;
      lastBuiltRevision = store.revision;
      return;
    }

    for (const layerId of targets) {
      const node = store.layerById(layerId);
      if (!node || !_effectiveVisible(node)) continue;
      const mesh = buildLayerMesh(layerId);
      if (!mesh) continue;
      if (layerId === store.selectedId) {
        const paint = (mat) => {
          if (!mat) return;
          mat.emissive = new THREE.Color(0x1d4ed8);
          mat.emissiveIntensity = 0.25;
        };
        if (mesh.isGroup) mesh.traverse((o) => paint(o.material));
        else paint(mesh.material);
      }
      assembly.add(setMeshLayer(mesh, layerId));
    }
    lastBuiltDoc = store.doc;
    lastBuiltRevision = store.revision;
    frameStable();
  }

  function scheduleRebuild() {
    rebuildQueued = true;
    scheduleUpdate();
  }
  function flushUpdate() {
    updateRaf = null;
    updateQueued = false;
    if (disposed) return;
    if (rebuildQueued) {
      rebuildQueued = false;
      if (lastBuiltDoc !== store.doc || lastBuiltRevision !== store.revision) rebuild();
      else applySelection();
    } else {
      applySelection();
    }
  }
  function scheduleSelection() {
    if (rebuildQueued) return;
    updateQueued = true;
    if (updateRaf) return;
    updateRaf = requestAnimationFrame(flushUpdate);
  }
  function scheduleUpdate() {
    if (updateRaf) return;
    updateRaf = requestAnimationFrame(flushUpdate);
  }

  const onPoseChanged = () => scheduleRebuild();
  const onSelection = () => scheduleSelection();
  const onDocChanged = () => scheduleRebuild();
  window.addEventListener('editor:pose-changed', onPoseChanged);
  window.addEventListener('editor:selection', onSelection);
  window.addEventListener('editor:doc-changed', onDocChanged);

  let raf = null;
  function loop() {
    raf = requestAnimationFrame(loop);
    resize();
    if (controls) controls.update();
    renderer.render(scene, camera);
  }
  loop();
  scheduleRebuild();

  return {
    update: scheduleRebuild,
    resize,
    dispose() {
      disposed = true;
      ++_inverseSeq;
      inverseAbort?.abort?.();
      if (updateRaf) cancelAnimationFrame(updateRaf);
      window.removeEventListener('editor:pose-changed', onPoseChanged);
      window.removeEventListener('editor:selection', onSelection);
      window.removeEventListener('editor:doc-changed', onDocChanged);
      cancelAnimationFrame(raf);
      window.removeEventListener('resize', resize);
      ro?.disconnect();
      controls?.dispose?.();
      clearAssembly();
      for (const geo of geometryCache.values()) geo.dispose?.();
      geometryCache.clear();
      renderer.dispose();
      container.innerHTML = '';
    },
  };
}
