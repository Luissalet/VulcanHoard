// viewport2d.js — 2D canvas rendering + selection (task 07)
// Renders one flat <g> per layer with its world matrix and local paths.
// Canvas, margins and overlays live in non-exportable groups.
// Selection is by fill hit-test (DOM), not bbox.
import * as affine from './affine.mjs';
import { store } from './store.js';

const SVG_NS = 'http://www.w3.org/2000/svg';

export class Viewport2D {
  /**
   * @param {SVGSVGElement} svgRoot  the #editor-svg element
   */
  constructor(svgRoot) {
    this.svg = svgRoot;
    this.defs = svgRoot.querySelector('#editor-defs') || this._ensureDefs(svgRoot);
    this.guides = svgRoot.querySelector('#canvas-guides');
    this.content = svgRoot.querySelector('#layer-content');
    this.overlays = svgRoot.querySelector('#constraint-overlays');
    this.handles = svgRoot.querySelector('#selection-handles');
    this._layerEls = new Map(); // layer_id -> <g>
    // Parsing canonical SVG strings dominates repeated layer rebuilds. Cache
    // by the source string so imports that replace an asset naturally miss.
    this._pathCache = new Map();
  }

  _ensureDefs(svgRoot) {
    let defs = svgRoot.querySelector('defs');
    if (!defs) {
      defs = document.createElementNS(SVG_NS, 'defs');
      svgRoot.insertBefore(defs, svgRoot.firstChild);
    }
    defs.id = 'editor-defs';
    return defs;
  }

  // ---- canvas + margins (non-exportable) ----------------------------------

  renderCanvas() {
    const c = store.doc?.canvas;
    if (!c) return;
    this.guides.innerHTML = '';
    this.guides.setAttribute('data-export', 'false');
    const w = c.width_mm, h = c.height_mm;
    const p = c.padding_mm || { left: 0, right: 0, top: 0, bottom: 0 };

    // Physical sheet only — sized exactly to width×height mm. Outside is the
    // editor chrome (gray), not a larger white plane.
    this.guides.appendChild(this._rect(0, 0, w, h, 'canvas-outer'));
    // Padding inset guide (non-exportable).
    const iw = w - p.left - p.right;
    const ih = h - p.top - p.bottom;
    if (iw > 0 && ih > 0 && (p.left || p.right || p.top || p.bottom)) {
      this.guides.appendChild(this._rect(p.left, p.top, iw, ih, 'canvas-inner'));
    }
  }

  // ---- layers -------------------------------------------------------------

  renderLayers() {
    if (!store.doc) {
      this.content.innerHTML = '';
      this.defs.innerHTML = '';
      this._layerEls.clear();
      return;
    }

    const mode = store.viewMode || 'normal';
    const frag = document.createDocumentFragment();
    const defsFrag = document.createDocumentFragment();
    const nextEls = new Map();

    try {
      if (mode === 'inverse') {
        // Draw order: Marco fondo (bottom) → inverse plates → Marco paredes.
        // Frame is never inverted and never flipped in stack with content.
        const frameNodes = this._drawOrder()
          .map((id) => store.layerById(id))
          .filter((n) => n && this._effectiveVisible(n) && this._isFrameLayer(n));
        const fondos = frameNodes.filter((n) => (n.name || '').includes('fondo') || (n.name || '') === 'Marco');
        const paredes = frameNodes.filter((n) => (n.name || '').includes('paredes'));
        const otherFrame = frameNodes.filter((n) => !fondos.includes(n) && !paredes.includes(n));
        for (const node of [...fondos, ...otherFrame]) {
          const g = this._renderLayer(node.id, node);
          if (g) { frag.appendChild(g); nextEls.set(node.id, g); }
        }
        this._buildInverseMode(frag, defsFrag, nextEls);
        for (const node of paredes) {
          const g = this._renderLayer(node.id, node);
          if (g) { frag.appendChild(g); nextEls.set(node.id, g); }
        }
      } else {
        // Matrioska and normal: solid stack, parents under children (children on top).
        const ordered = this._drawOrder();
        for (const layerId of ordered) {
          const node = store.layerById(layerId);
          if (!node) continue;
          if (!this._effectiveVisible(node)) continue;
          const g = this._renderLayer(layerId, node);
          if (g) {
            frag.appendChild(g);
            nextEls.set(layerId, g);
          }
        }
      }
    } catch (err) {
      console.error('renderLayers failed', err);
      return; // keep previous content instead of blanking the canvas
    }

    this.content.innerHTML = '';
    this.defs.innerHTML = '';
    this._layerEls.clear();
    this.defs.appendChild(defsFrag);
    this.content.appendChild(frag);
    for (const [id, el] of nextEls) this._layerEls.set(id, el);
    this.renderSelection();
  }

  /** Plancha del canvas con hueco = solo esta capa (hijos = otras planchas). */
  _buildInverseMode(frag, defsFrag, nextEls) {
    const c = store.doc.canvas;
    const W = c.width_mm, H = c.height_mm;
    const targets = this._drawOrder();

    for (const layerId of targets) {
      const node = store.layerById(layerId);
      if (!node || !this._effectiveVisible(node)) continue;
      if (this._isFrameLayer(node)) continue; // rendered as normal ring after
      const asset = store.assetById(node.asset_id);
      if (!asset?.canonical_svg) continue;

      const maskId = `inv-mask-${layerId}`;
      const mask = document.createElementNS(SVG_NS, 'mask');
      mask.setAttribute('id', maskId);
      mask.setAttribute('maskUnits', 'userSpaceOnUse');
      const keep = this._rect(0, 0, W, H, '');
      keep.setAttribute('fill', '#ffffff');
      mask.appendChild(keep);
      const hole = this._pathsGroup(layerId, asset, '#000000');
      if (hole) mask.appendChild(hole);
      defsFrag.appendChild(mask);

      const plate = this._rect(0, 0, W, H, 'layer inverse-plate');
      plate.dataset.layerId = layerId;
      plate.setAttribute('fill', this._layerColor(layerId));
      plate.setAttribute('mask', `url(#${maskId})`);
      plate.style.opacity = targets.length > 1 ? '0.55' : '0.92';
      frag.appendChild(plate);
      nextEls.set(layerId, plate);
    }
  }

  /** Group of layer paths with world transform (incl. normalization). */
  _pathsGroup(layerId, asset, fill) {
    if (!asset?.canonical_svg) return null;
    const g = document.createElementNS(SVG_NS, 'g');
    const m = this._worldMatrix(layerId, asset);
    g.setAttribute('transform', `matrix(${m.join(' ')})`);
    for (const d of this._extractPaths(asset.canonical_svg)) {
      const p = document.createElementNS(SVG_NS, 'path');
      p.setAttribute('d', d);
      p.setAttribute('fill', fill);
      p.setAttribute('fill-rule', 'evenodd');
      p.setAttribute('stroke', 'none');
      g.appendChild(p);
    }
    return g.childNodes.length ? g : null;
  }

  _isFrameLayer(node) {
    const name = node?.name || '';
    return name === 'Marco' || name === 'Marco fondo' || name === 'Marco paredes'
      || String(node?.id || '').startsWith('layer_marco_');
  }

  _drawOrder() {
    // Depth-first: parent before children, siblings by order
    const out = [];
    const visit = (parentId) => {
      for (const child of store.childrenOf(parentId)) {
        out.push(child.id);
        visit(child.id);
      }
    };
    visit(null);
    return out;
  }

  _effectiveVisible(node) {
    let cur = node;
    const seen = new Set();
    while (cur && !seen.has(cur.id)) {
      seen.add(cur.id);
      if (!cur.visible) return false;
      cur = cur.parent_id ? store.layerById(cur.parent_id) : null;
    }
    return true;
  }

  _renderLayer(layerId, node) {
    const asset = store.assetById(node.asset_id);
    if (!asset) return null;
    const svg = asset.canonical_svg;
    if (!svg) return null;

    const g = document.createElementNS(SVG_NS, 'g');
    g.setAttribute('class', 'layer');
    g.dataset.layerId = layerId;

    // The group carries the FULL world matrix (spec §5.2/§5.3):
    //   W = L_root · ... · L_layer · N
    // where N is the asset's normalization_pose (source → local mm, centred
    // at the origin).  The canonical SVG paths are therefore re-expressed in
    // LOCAL mm coordinates, and local bounds / selection / hit-tests all use
    // the same frame.
    const m = this._worldMatrix(layerId, asset);
    g.setAttribute('transform', `matrix(${m.join(' ')})`);

    // Local paths: parse the canonical SVG's <path> elements
    const paths = this._extractPaths(svg);
    for (const d of paths) {
      const p = document.createElementNS(SVG_NS, 'path');
      p.setAttribute('d', d);
      p.setAttribute('fill', 'currentColor');
      p.setAttribute('fill-rule', 'evenodd');
      p.setAttribute('stroke', 'none');
      g.appendChild(p);
    }

    // Hit-test: the group itself catches pointer events on filled areas.
    // Holes (evenodd) are transparent to hits → click selects what's behind.
    // Marco is scene furniture — only the panel may change it.
    if (this._isFrameLayer(node)) {
      g.style.pointerEvents = 'none';
    }
    g.style.color = this._layerColor(layerId);
    return g;
  }

  /** Layer poses only (parent chain). Use for local_bounds already in mm. */
  _layerMatrix(layerId) {
    const chain = [];
    let cur = layerId;
    const seen = new Set();
    while (cur && !seen.has(cur)) {
      seen.add(cur);
      const node = store.layerById(cur);
      if (!node) break;
      // Only the layer itself is mirrored by flip_h; ancestors contribute
      // their pose only (children are never flipped by a parent's flip).
      let lm = affine.matrix(node.pose);
      if (cur === layerId && node.flip_h) lm = affine.multiply(lm, [-1, 0, 0, 1, 0, 0]);
      chain.unshift(lm);
      cur = node.parent_id;
    }
    let m = affine.identity();
    for (const lm of chain) m = affine.multiply(m, lm);
    return m;
  }

  _worldMatrix(layerId, asset) {
    // Walk the parent chain: W = L_root · ... · L_layer · N_path
    // N_path maps raw canonical SVG path coords → local mm (incl. viewBox).
    let m = this._layerMatrix(layerId);
    if (asset) {
      m = affine.multiply(m, affine.pathToLocalMatrix(asset));
    }
    return m;
  }

  _extractPaths(svgText) {
    const cached = this._pathCache.get(svgText);
    if (cached) return cached;
    // Parse SVG text and return the `d` attribute of every <path>.
    // Uses DOMParser (browser) — no regex on geometry.
    const doc = new DOMParser().parseFromString(svgText, 'image/svg+xml');
    const out = [];
    for (const p of doc.querySelectorAll('path')) {
      const d = p.getAttribute('d');
      if (d) out.push(d);
    }
    this._pathCache.set(svgText, out);
    return out;
  }

  _layerColor(layerId) {
    // Stable per-layer colour. Selection uses the dashed overlay only —
    // filling selected layers blue made nested pieces look like a rim.
    const node = store.layerById(layerId);
    if (node?.locked) return '#9ca3af';
    const idx = this._layerIndex(layerId);
    const hue = (idx * 137.508) % 360;
    return `hsl(${hue.toFixed(0)}, 65%, 45%)`;
  }

  _layerIndex(layerId) {
    const ids = Object.keys(store.doc?.layers || {});
    const i = ids.indexOf(layerId);
    return i >= 0 ? i : 0;
  }

  // ---- selection overlay --------------------------------------------------

  renderSelection() {
    this.handles.innerHTML = '';
    this.handles.setAttribute('data-export', 'false');
    if (!store.selectedId) return;
    const node = store.layerById(store.selectedId);
    if (!node) return;
    // No gizmo for locked / marco — pose is not editable from the canvas.
    if (node.locked || this._isFrameLayer(node)) return;
    const asset = store.assetById(node.asset_id);
    // Prefer asset.local_bounds (local mm) over getBBox(source units): getBBox
    // + world transform can produce a giant dashed rect across the whole UI.
    const lb = asset?.local_bounds;
    if (!lb || !(lb[2] > lb[0]) || !(lb[3] > lb[1])) return;

    const g = document.createElementNS(SVG_NS, 'g');
    const m = this._layerMatrix(store.selectedId);
    g.setAttribute('transform', `matrix(${m.join(' ')})`);
    g.setAttribute('pointer-events', 'none');

    // Outline the SILHOUETTE, not its bounding box: a box around a curly
    // shape is mostly empty and makes a perfectly flush fit look loose.
    let outlined = false;
    try {
      const ds = this._extractPaths(asset.canonical_svg);
      if (ds.length) {
        const pg = document.createElementNS(SVG_NS, 'g');
        pg.setAttribute('transform', `matrix(${affine.pathToLocalMatrix(asset).join(' ')})`);
        for (const d of ds) {
          const pp = document.createElementNS(SVG_NS, 'path');
          pp.setAttribute('d', d);
          pp.setAttribute('fill', 'none');
          pp.setAttribute('stroke', '#2563eb');
          pp.setAttribute('stroke-width', '1.5');
          pp.setAttribute('stroke-dasharray', '4 2');
          pp.setAttribute('vector-effect', 'non-scaling-stroke');
          pg.appendChild(pp);
        }
        g.appendChild(pg);
        outlined = true;
      }
    } catch { /* fall back to the box below */ }

    if (!outlined) {
      const poly = document.createElementNS(SVG_NS, 'rect');
      poly.setAttribute('x', lb[0]);
      poly.setAttribute('y', lb[1]);
      poly.setAttribute('width', lb[2] - lb[0]);
      poly.setAttribute('height', lb[3] - lb[1]);
      poly.setAttribute('fill', 'none');
      poly.setAttribute('stroke', '#2563eb');
      poly.setAttribute('stroke-width', '1.5');
      poly.setAttribute('stroke-dasharray', '4 2');
      poly.setAttribute('vector-effect', 'non-scaling-stroke');
      g.appendChild(poly);
    }

    // Handles are sized in SCREEN pixels: the gizmo lives in the layer's own
    // frame, so a fixed local size would balloon with the layer's scale.
    const svgRect = this.svg.getBoundingClientRect();
    const vb = this.svg.viewBox?.baseVal;
    const pxPerDoc = (vb && vb.width > 0) ? (svgRect.width / vb.width) : 1;
    const perLocal = Math.max(1e-9, (Math.hypot(m[0], m[1]) || 1) * pxPerDoc);
    const px = (n) => n / perLocal;

    // Rotation handle: a dot just INSIDE the top edge.  Outside the shape it
    // falls off-screen whenever the layer fills the sheet, which is the
    // normal case here.
    const cxLocal = (lb[0] + lb[2]) / 2;
    const stemLen = Math.min(px(20), (lb[3] - lb[1]) * 0.4);
    const stem = document.createElementNS(SVG_NS, 'line');
    stem.setAttribute('x1', cxLocal);
    stem.setAttribute('y1', lb[1]);
    stem.setAttribute('x2', cxLocal);
    stem.setAttribute('y2', lb[1] + stemLen);
    stem.setAttribute('stroke', '#2563eb');
    stem.setAttribute('stroke-width', '1.5');
    stem.setAttribute('vector-effect', 'non-scaling-stroke');
    g.appendChild(stem);

    const rot = document.createElementNS(SVG_NS, 'circle');
    rot.setAttribute('cx', cxLocal);
    rot.setAttribute('cy', lb[1] + stemLen);
    rot.setAttribute('r', px(7));
    rot.setAttribute('fill', '#2563eb');
    rot.setAttribute('stroke', '#fff');
    rot.setAttribute('stroke-width', '1');
    rot.setAttribute('vector-effect', 'non-scaling-stroke');
    rot.dataset.handle = 'rotate';
    rot.style.cursor = 'grab';
    rot.style.pointerEvents = 'auto';
    const rotTitle = document.createElementNS(SVG_NS, 'title');
    rotTitle.textContent = 'Arrastra para rotar (Shift: pasos de 15°)';
    rot.appendChild(rotTitle);
    g.appendChild(rot);

    // Nested children have no scale handle: their scale is always the
    // largest that fits the parent contour at their position.
    if (node.parent_id) {
      this.handles.appendChild(g);
      return;
    }

    const handle = document.createElementNS(SVG_NS, 'rect');
    // Place the scale handle on the visual bottom-right after flip:
    // with flip_h, local +X becomes visual −X, so use the local left edge.
    const hs = px(9);
    const hx = node.flip_h ? (lb[0] - hs / 2) : (lb[2] - hs / 2);
    const hy = lb[3] - hs / 2;
    handle.setAttribute('x', hx);
    handle.setAttribute('y', hy);
    handle.setAttribute('width', hs);
    handle.setAttribute('height', hs);
    handle.setAttribute('fill', '#2563eb');
    handle.setAttribute('stroke', '#fff');
    handle.setAttribute('stroke-width', '1');
    handle.setAttribute('vector-effect', 'non-scaling-stroke');
    handle.dataset.handle = 'scale';
    handle.style.cursor = 'nwse-resize';
    handle.style.pointerEvents = 'auto';
    if (node.flip_h) {
      const cx = hx + hs / 2;
      const cy = hy + hs / 2;
      handle.setAttribute(
        'transform',
        `translate(${cx} ${cy}) scale(-1 1) translate(${-cx} ${-cy})`,
      );
    }
    g.appendChild(handle);

    this.handles.appendChild(g);
  }

  // ---- hit-test selection -------------------------------------------------

  /**
   * Select the topmost layer whose filled area contains the document point.
   * Holes are transparent (evenodd), so a click in a hole selects the layer
   * behind it. Returns the selected layer id or null.
   */
  pick(docPoint) {
    const ordered = this._drawOrder().reverse();
    for (const layerId of ordered) {
      const node = store.layerById(layerId);
      if (!node || !this._effectiveVisible(node)) continue;
      if (this._isFrameLayer(node)) continue; // not selectable in 2D
      const el = this._layerEls.get(layerId);
      if (!el) continue;
      const asset = store.assetById(node.asset_id);
      for (const p of el.querySelectorAll('path')) {
        try {
          if (p.isPointInFill && p.isPointInFill(new DOMPoint(docPoint.x, docPoint.y))) {
            return layerId;
          }
        } catch { /* isPointInFill unsupported — skip */ }
      }
      // Fallback (and testable path): filled-bounds hit-test in local mm.
      // Holes are not resolved here; the DOM fill test above is authoritative
      // in a real browser.
      if (asset && this.pointInLayer(layerId, asset, docPoint)) {
        return layerId;
      }
    }
    return null;
  }

  /**
   * True if the document point lies inside the layer's filled local bounds
   * (asset.local_bounds in mm, centred at the origin after normalization).
   */
  pointInLayer(layerId, asset, docPoint) {
    const lb = asset.local_bounds;
    if (!lb) return false;
    // local_bounds are already mm — invert pose chain only (not N again).
    const m = this._layerMatrix(layerId);
    const local = affine.point(affine.inverse(m), docPoint);
    return local.x >= lb[0] && local.x <= lb[2] &&
           local.y >= lb[1] && local.y <= lb[3];
  }

  // ---- helpers ------------------------------------------------------------

  _rect(x, y, w, h, cls) {
    const r = document.createElementNS(SVG_NS, 'rect');
    r.setAttribute('x', x); r.setAttribute('y', y);
    r.setAttribute('width', w); r.setAttribute('height', h);
    r.setAttribute('class', cls);
    r.setAttribute('pointer-events', 'none');
    return r;
  }

  /** Update the SVG viewBox to the current viewport (pan/zoom). */
  applyViewport() {
    const c = store.doc?.canvas;
    if (!c) return;
    const { visW, visH, x, y } = this._viewBoxRect();
    this.svg.setAttribute('viewBox', `${x} ${y} ${visW} ${visH}`);
  }

  /** Fit the whole canvas into the viewport with 10% margin. */
  fitToCanvas() {
    const c = store.doc?.canvas;
    if (!c) return;
    const vw = Math.max(1, this.svg.clientWidth || 800);
    const vh = Math.max(1, this.svg.clientHeight || 600);
    const margin = 1.1;
    const aspect = vw / vh;
    const neededW = c.width_mm * margin;
    const neededH = c.height_mm * margin;
    let visW, visH;
    if (neededW / neededH < aspect) {
      visH = neededH;
      visW = visH * aspect;
    } else {
      visW = neededW;
      visH = visW / aspect;
    }
    store.setViewport(
      (c.width_mm - visW) / 2,
      (c.height_mm - visH) / 2,
      c.height_mm / visH,
    );
    this.applyViewport();
  }

  _viewBoxRect() {
    const c = store.doc.canvas;
    const vw = Math.max(1, this.svg.clientWidth || 800);
    const vh = Math.max(1, this.svg.clientHeight || 600);
    const vp = store.viewport || { x: 0, y: 0, scale: 1 };
    const scale = (Number.isFinite(vp.scale) && vp.scale > 0) ? vp.scale : 1;
    const visH = c.height_mm / scale;
    const visW = visH * (vw / vh);
    return { visW, visH, x: vp.x, y: vp.y };
  }
}
