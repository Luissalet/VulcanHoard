// Heron constrained parts: solve sketches, build with face operations, change dimensions as revisions, import library recipes.
(() => {
  const el = (id) => document.getElementById(id);
  const make = (tag, text, cls) => { const n = document.createElement(tag); if (text !== undefined && text !== null) n.textContent = String(text); if (cls) n.className = cls; return n; };
  const fmt = (v, d = 3) => (typeof v === 'number' ? (Math.abs(v) < 1e-9 ? '0' : v.toFixed(d)) : (v ?? '—'));
  const STATUS = {
    solved: 'Resuelto: todas las restricciones se cumplen y no quedan grados de libertad',
    under_constrained: 'Faltan cotas: las restricciones se cumplen pero quedan movimientos libres (se construye con la geometría actual)',
    incompatible: 'Restricciones incompatibles: no se ha construido nada',
    invalid_geometry: 'El boceto resuelto se cruza o un hueco se sale del contorno: no se ha construido nada',
    invalid_reference: 'Una referencia de cara ya no nombra una sola cara: no se ha construido nada',
    feature_failed: 'El núcleo CAD ha rechazado una operación',
  };
  const TYPE = { fixed: 'fijo', coincident: 'coincidentes', horizontal: 'horizontal', vertical: 'vertical', parallel: 'paralelas', perpendicular: 'perpendiculares',
    angle: 'ángulo', tangent: 'tangente', equal: 'iguales', symmetric: 'simétricos', midpoint: 'punto medio', point_on_line: 'punto en recta',
    point_on_circle: 'punto en círculo', concentric: 'concéntricos', distance: 'distancia', distance_x: 'distancia horizontal', distance_y: 'distancia vertical',
    length: 'longitud', radius: 'radio', diameter: 'diámetro' };
  const OP = { hole: 'Taladro', fillet: 'Redondeo', chamfer: 'Chaflán', shell: 'Vaciado', pad: 'Resalte bocetado', pocket: 'Cajera bocetada' };
  const FSTATE = { resolved: 'resuelta', invalidated: 'INVALIDADA', failed: 'falló', not_evaluated: 'sin evaluar' };
  const CHANGE = { kept: 'se conserva', invalidated: 'invalidada', new: 'nueva', removed: 'quitada', restored: 'recuperada' };
  const MEASURE = { hole_distance: 'distancia entre taladros', hole_diameter: 'diámetro del taladro', hole_count: 'taladros encontrados', bbox_x: 'caja X', bbox_y: 'caja Y', bbox_z: 'caja Z', volume: 'volumen' };
  const NS = 'http://www.w3.org/2000/svg';
  const svgEl = (tag, attrs) => { const n = document.createElementNS(NS, tag); Object.entries(attrs || {}).forEach(([k, v]) => n.setAttribute(k, v)); return n; };

  function arcPoints(sk, pts, aid, steps = 48) {
    const a = sk.arcs[aid], c = pts[a.center], s = pts[a.start], e = pts[a.end];
    const r = Math.hypot(s[0] - c[0], s[1] - c[1]);
    const a0 = Math.atan2(s[1] - c[1], s[0] - c[0]), a1 = Math.atan2(e[1] - c[1], e[0] - c[0]);
    const mod = (v) => ((v % (2 * Math.PI)) + 2 * Math.PI) % (2 * Math.PI);
    const sweep = a.clockwise ? -mod(a0 - a1) : mod(a1 - a0);
    return Array.from({ length: steps + 1 }, (_, k) => [c[0] + r * Math.cos(a0 + sweep * k / steps), c[1] + r * Math.sin(a0 + sweep * k / steps)]);
  }

  /** Interactive sketch: the solved geometry drawn from the solve result; dragging a point asks the solver where it may go. */
  function sketchEditor(spec, solved) {
    const box = el('part-editor');
    box.replaceChildren();
    if (!spec || !spec.sketch || !solved || !solved.points) return;
    const sk = spec.sketch, pts = solved.points;
    const free = new Set((solved.free || []).filter((f) => f.kind === 'point').map((f) => f.entity));
    const xs = [], ys = [];
    Object.values(pts).forEach((p) => { xs.push(p[0]); ys.push(p[1]); });
    Object.entries(sk.circles || {}).forEach(([cid, c]) => { const r = (solved.radii || {})[cid] || 0; xs.push(pts[c.center][0] - r, pts[c.center][0] + r); ys.push(pts[c.center][1] - r, pts[c.center][1] + r); });
    Object.keys(sk.arcs || {}).forEach((aid) => arcPoints(sk, pts, aid, 16).forEach((p) => { xs.push(p[0]); ys.push(p[1]); }));
    const x0 = Math.min(...xs), x1 = Math.max(...xs), y0 = Math.min(...ys), y1 = Math.max(...ys);
    const span = Math.max(x1 - x0, y1 - y0, 1), pad = span * 0.12, W = 800, H = 480;
    const k = Math.min((W - 40) / (x1 - x0 + 2 * pad || 1), (H - 40) / (y1 - y0 + 2 * pad || 1));
    const X = (x) => 20 + (x - x0 + pad) * k, Y = (y) => H - 20 - (y - y0 + pad) * k;
    const back = (sx, sy) => [(sx - 20) / k + x0 - pad, (H - 20 - sy) / k + y0 - pad];
    const used = new Set([...(sk.profile || []), ...(sk.holes || []).filter(Array.isArray).flat()]);
    const svg = svgEl('svg', { viewBox: `0 0 ${W} ${H}`, class: 'sketch-editor', role: 'img', 'aria-label': 'Boceto interactivo: arrastra los puntos' });
    svg.append(svgEl('rect', { width: W, height: H, fill: '#ffffff' }));
    Object.entries(sk.lines || {}).forEach(([lid, [a, b]]) => svg.append(svgEl('line', { x1: X(pts[a][0]), y1: Y(pts[a][1]), x2: X(pts[b][0]), y2: Y(pts[b][1]),
      stroke: '#1d3557', 'stroke-width': 2.5, 'stroke-dasharray': used.has(lid) ? '' : '8 6', 'data-edge': lid })));
    Object.keys(sk.arcs || {}).forEach((aid) => svg.append(svgEl('polyline', { points: arcPoints(sk, pts, aid).map((p) => `${X(p[0])},${Y(p[1])}`).join(' '),
      fill: 'none', stroke: '#1d3557', 'stroke-width': 2.5, 'stroke-dasharray': used.has(aid) ? '' : '8 6', 'data-edge': aid })));
    Object.entries(sk.circles || {}).forEach(([cid, c]) => svg.append(svgEl('circle', { cx: X(pts[c.center][0]), cy: Y(pts[c.center][1]), r: ((solved.radii || {})[cid] || 0) * k,
      fill: 'none', stroke: '#1d3557', 'stroke-width': 2.5, 'data-edge': cid })));
    Object.entries(pts).forEach(([pid, p]) => {
      const g = svgEl('g', { 'data-point': pid, class: 'drag-point' + (free.has(pid) ? ' free' : ''), tabindex: 0 });
      const dot = svgEl('circle', { cx: X(p[0]), cy: Y(p[1]), r: 8, fill: free.has(pid) ? '#e0a400' : '#1d3557', stroke: '#fff', 'stroke-width': 2 });
      const label = svgEl('text', { x: X(p[0]) + 10, y: Y(p[1]) + 18, 'font-size': 13, fill: '#333', 'font-family': 'sans-serif' });
      label.textContent = pid;
      g.append(dot, label);
      g.addEventListener('pointerdown', (ev) => {
        ev.preventDefault();
        g.setPointerCapture(ev.pointerId);
        const local = (e) => { const pt = svg.createSVGPoint(); pt.x = e.clientX; pt.y = e.clientY; return pt.matrixTransform(svg.getScreenCTM().inverse()); };
        const move = (e) => { const q = local(e); dot.setAttribute('cx', q.x); dot.setAttribute('cy', q.y); };
        const up = (e) => {
          g.removeEventListener('pointermove', move); g.removeEventListener('pointerup', up);
          const q = local(e);
          dragTo(pid, back(q.x, q.y));
        };
        g.addEventListener('pointermove', move); g.addEventListener('pointerup', up);
      });
      svg.append(g);
    });
    box.append(make('h4', 'Boceto interactivo'),
      make('p', 'Arrastra un punto: el resolvedor lo lleva lo más cerca posible sin romper ninguna restricción (los puntos naranjas aún tienen grados de libertad; los fijados por cotas vuelven a su sitio). La definición JSON se actualiza con las nuevas posiciones de partida; guarda una revisión para conservarla.', 'muted'),
      svg);
    const manual = make('div', null, 'actions');
    const pick = make('select'); pick.id = 'part-drag-point'; pick.setAttribute('aria-label', 'Punto a mover');
    Object.keys(pts).forEach((pid) => { const o = make('option', pid); o.value = pid; pick.append(o); });
    const px = make('input'); px.id = 'part-drag-x'; px.setAttribute('aria-label', 'X destino (mm)'); px.placeholder = 'x mm'; px.style.width = '110px';
    const py = make('input'); py.id = 'part-drag-y'; py.setAttribute('aria-label', 'Y destino (mm)'); py.placeholder = 'y mm'; py.style.width = '110px';
    pick.onchange = () => { px.value = fmt(pts[pick.value][0], 3); py.value = fmt(pts[pick.value][1], 3); };
    pick.onchange();
    const go = make('button', 'Mover a estas coordenadas', 'secondary'); go.id = 'part-drag-go';
    go.onclick = () => dragTo(pick.value, [Number(px.value), Number(py.value)]);
    manual.append(pick, px, py, go);
    box.append(manual);
  }

  function dragTo(pid, to) {
    return busy(`Moviendo ${pid}…`, async () => {
      const r = await call('/api/part/drag', { spec: readSpec(), point: pid, to });
      el('part-spec').value = JSON.stringify(r.spec, null, 2);
      const NOTE = { 'the point is fixed by its constraints and did not move': 'está fijado por sus restricciones y no se ha movido',
        'the point reached the requested position': 'ha llegado a la posición pedida',
        'the point moved as far as its constraints allow (nearest reachable position)': 'se ha movido hasta donde le dejan sus restricciones (la posición alcanzable más cercana)' };
      renderSolve(r.solve, r.spec, true);
      el('part-status').textContent = `${pid} ${NOTE[r.note] || r.note}: ahora en (${fmt(r.position[0], 3)}, ${fmt(r.position[1], 3)}) mm, movido ${fmt(r.moved_mm, 3)} mm` +
        (r.moved_points.length > 1 ? ` · también se movieron ${r.moved_points.filter((p) => p !== pid).join(', ')}` : '');
    });
  }
  let examples = {}, current = null;

  async function call(path, body) {
    const response = await fetch(path, body ? { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) } : {});
    const data = await response.json();
    if (!response.ok) throw Error(data.detail || data.error || 'No disponible');
    return data;
  }
  async function busy(message, fn) {
    const buttons = [...document.querySelectorAll('#design button')];
    buttons.forEach((b) => (b.disabled = true));
    el('part-status').textContent = message;
    try { await fn(); if (el('part-status').textContent === message) el('part-status').textContent = 'Listo.'; }
    catch (e) { el('part-status').textContent = e.message; }
    finally { buttons.forEach((b) => (b.disabled = false)); }
  }
  function table(headers, rows) {
    const wrap = make('div', null, 'tablewrap'), t = make('table'), head = make('tr');
    headers.forEach((h) => head.append(make('th', h)));
    t.append(head);
    rows.forEach((r) => { const tr = make('tr'); r.forEach((cell) => { const td = make('td'); if (cell instanceof Node) td.append(cell); else td.textContent = cell ?? '—'; tr.append(td); }); t.append(tr); });
    wrap.append(t);
    return wrap;
  }
  const mark = (ok, yes, no) => make('span', ok ? yes : no, ok ? 'ok' : 'bad');

  function sketchSection(out, sketch) {
    const badge = make('span', STATUS[sketch.status] || sketch.status, 'badge ' + sketch.status);
    out.append(make('h4', 'Boceto'), badge);
    out.append(make('p', `Grados de libertad: ${sketch.dof} · ${sketch.unknowns} incógnitas · ${sketch.equations} ecuaciones` +
      (sketch.geometry ? ` · área ${fmt(sketch.geometry.area_mm2, 2)} mm²` : '')));
    if (sketch.free && sketch.free.length) {
      out.append(make('p', 'Se pueden mover: ' + sketch.free.map((f) => f.kind === 'circle' ? `${f.entity} (radio sin cota)` :
        f.freedom === 2 ? `${f.entity} (libre)` : `${f.entity} (desliza en ${f.direction.map((v) => fmt(v, 2)).join(', ')})`).join(' · '), 'warn'));
    }
    (sketch.conflicts || []).forEach((c) => out.append(make('p', `«${c.failing_constraint}» (${TYPE[c.type] || c.type}) no puede cumplirse junto a: ${c.conflicts_with.join(', ') || 'las anteriores'}` +
      (c.residual_mm !== undefined ? ` · le faltan ${fmt(c.residual_mm)} mm` : '') + (c.residual_deg !== undefined ? ` · ${fmt(c.residual_deg)}°` : ''), 'bad')));
    (sketch.geometry?.errors || []).forEach((e) => out.append(make('p', e, 'bad')));
    if (sketch.redundant_but_consistent && sketch.redundant_but_consistent.length) {
      out.append(make('p', 'Redundantes (ya implicadas por otras, sin conflicto): ' + sketch.redundant_but_consistent.map((r) => r.id).join(', '), 'warn'));
    }
    out.append(table(['Restricción', 'Tipo', 'Valor', 'Parámetros', 'Residuo', 'Estado'], (sketch.constraints || []).map((c) => [
      c.id, TYPE[c.type] || c.type, c.value !== undefined ? fmt(c.value) + (c.type === 'angle' ? '°' : ' mm') : '—', (c.parameters || []).join(', ') || '—',
      c.residual_mm !== undefined ? fmt(c.residual_mm, 6) + ' mm' : c.residual_deg !== undefined ? fmt(c.residual_deg, 6) + '°' : '—',
      make('span', (c.state === 'satisfied' ? 'se cumple' : 'NO se cumple') + (c.redundant ? ' · redundante' : ''), c.state === 'satisfied' ? (c.redundant ? 'warn' : 'ok') : 'bad')])));
  }

  function featuresSection(out, result) {
    if (!result.features || !result.features.length) return;
    out.append(make('h4', 'Operaciones sobre caras'));
    out.append(table(['Operación', 'Tipo', 'Cara', 'Estado', 'Cara encontrada / motivo'], result.features.map((f) => [
      f.id, OP[f.type] || f.type, f.face, make('span', FSTATE[f.state] || f.state, f.state === 'resolved' ? 'ok' : f.state === 'not_evaluated' ? 'warn' : 'bad'),
      f.resolved ? `${f.resolved.surface} · centro ${f.resolved.center_mm.map((v) => fmt(v, 2)).join(', ')} · ${fmt(f.resolved.area_mm2, 1)} mm²` +
        (f.holes ? ` · ${f.holes.length} taladro(s)` : '') + (f.edges ? ` · ${f.edges} aristas` : '') +
        (f.pattern ? (f.pattern.type === 'linear' ? ` · patrón lineal de ${f.pattern.count} cada ${fmt(f.pattern.spacing_mm, 2)} mm` : ` · patrón circular de ${f.pattern.count} cada ${fmt(f.pattern.pitch_deg, 2)}°`) : '') +
        (f.sketch ? ` · boceto en la cara: ${STATUS[f.sketch.status] ? f.sketch.status : f.sketch.status}, ${f.sketch.dof} g.d.l., ${fmt(f.sketch.area_mm2, 2)} mm² · volumen ${f.volume_change_mm3 >= 0 ? '+' : ''}${fmt(f.volume_change_mm3, 2)} mm³` : '')
        : (f.reason || '—')])));
    if ((result.reference_changes || []).length && current && current.base_revision) {
      out.append(make('h4', `Referencias frente a la revisión ${current.base_revision}`));
      out.append(table(['Operación', 'Cambio', 'Detalle'], result.reference_changes.map((r) => [
        r.id, make('span', CHANGE[r.change] || r.change, r.change === 'kept' ? 'ok' : r.change === 'invalidated' ? 'bad' : 'warn'),
        r.change === 'kept' ? `la cara se ha movido ${fmt(r.face_center_moved_mm, 3)} mm · área ${r.area_change_mm2 >= 0 ? '+' : ''}${fmt(r.area_change_mm2, 2)} mm²` : (r.reason || r.was || '—')])));
    }
  }

  function checksSection(out, result) {
    if (!result.checks || !result.checks.length) return;
    out.append(make('h4', 'Medidas comprobadas en el sólido (independientes del boceto)'));
    out.append(table(['Comprobación', 'Medida', 'Esperado', 'Medido', 'Diferencia', 'Resultado'], result.checks.map((c) => [
      c.id, MEASURE[c.measure] || c.measure, fmt(c.expected, 4), c.measured !== undefined ? fmt(c.measured, 4) : '—', c.difference !== undefined ? fmt(c.difference, 6) : (c.note || '—'),
      mark(c.passed, 'cumple', 'NO cumple')])));
  }

  function parametersForm(out, row) {
    const values = row.result.parameters || {};
    const names = Object.keys(row.spec.parameters || {});
    if (!names.length) return;
    out.append(make('h4', 'Cotas con nombre'));
    out.append(make('p', 'Cambia una cota y regenera: se crea una revisión nueva que parte de la solución anterior y conserva todas las restricciones. Puedes escribir una expresión como «hole_spacing + 5».', 'muted'));
    const grid = make('div', null, 'params');
    const inputs = {};
    names.forEach((name) => {
      const label = make('label', name + ' ');
      const input = make('input');
      input.value = typeof row.spec.parameters[name] === 'string' ? row.spec.parameters[name] : String(values[name]);
      input.dataset.original = input.value;
      input.setAttribute('aria-label', 'Cota ' + name);
      input.id = 'part-param-' + name;
      inputs[name] = input;
      label.append(input);
      if (typeof row.spec.parameters[name] === 'string') label.append(make('span', ` = ${fmt(values[name], 3)}`, 'muted'));
      grid.append(label);
    });
    out.append(grid);
    const actions = make('div', null, 'actions');
    const regen = make('button', 'Regenerar con estas cotas');
    regen.id = 'part-regenerate';
    regen.onclick = () => busy('Regenerando la pieza…', async () => {
      const changes = names.filter((n) => inputs[n].value.trim() !== inputs[n].dataset.original).map((n) => {
        const raw = inputs[n].value.trim();
        return { parameter: n, value: /^-?\d+(\.\d+)?$/.test(raw) ? Number(raw) : raw };
      });
      if (!changes.length) throw Error('No has cambiado ninguna cota.');
      render(await call('/api/parts/' + encodeURIComponent(row.id) + '/parameters', { base_revision: row.revision, request_id: crypto.randomUUID(), changes }));
    });
    actions.append(regen);
    out.append(actions);
  }

  function render(row) {
    current = row;
    const out = el('part-output');
    out.replaceChildren();
    const head = make('div', null, 'actions');
    head.append(make('h3', `${row.spec.title} · revisión ${row.revision}`));
    if ((row.revisions || []).length > 1) {
      const pick = make('select');
      pick.setAttribute('aria-label', 'Revisión de la pieza');
      pick.id = 'part-revision';
      row.revisions.forEach((r) => { const o = make('option', 'Revisión ' + r); o.value = r; if (r === row.revision) o.selected = true; pick.append(o); });
      pick.onchange = () => busy('Leyendo la revisión…', async () => render(await call(`/api/parts/${encodeURIComponent(row.id)}?revision=${pick.value}`)));
      head.append(pick);
    }
    out.append(head);
    if (row.state !== 'finished') { out.append(make('p', row.result?.error || 'La evaluación no terminó.', 'bad')); return; }
    const result = row.result;
    out.append(make('span', STATUS[result.status] || result.status, 'badge ' + result.status));
    if (result.changes) out.append(make('p', 'Cambios: ' + result.changes.map((c) => `${c.parameter || c.constraint} ${fmt(c.from)} → ${fmt(c.to)}`).join(' · ')));
    if (result.import) out.append(make('p', `Importada de la receta (${result.import.source_node}): ${result.import.points} puntos, ${result.import.constraints} restricciones` +
      (result.import.not_imported.length ? ' · Sin importar: ' + result.import.not_imported.join('; ') : ''), 'muted'));
    if (row.downloads) {
      const views = make('div', null, 'views');
      [['sketch.svg', 'Boceto resuelto'], ['isometric.svg', 'Perspectiva'], ['front.svg', 'Frontal'], ['top.svg', 'Superior']].forEach(([name, alt]) => {
        if (!row.downloads[name]) return;
        const img = make('img'); img.src = row.downloads[name]; img.alt = alt; img.loading = 'lazy'; views.append(img);
      });
      out.append(views);
      const rt = result.roundtrip || {};
      out.append(make('p', `${fmt(result.volume_mm3, 2)} mm³ · ${result.bounds_mm.map((v) => fmt(v, 3)).join(' × ')} mm · ${result.faces} caras`));
      if (rt.step) out.append(make('p', `STEP reimportado: ${rt.step.solids} sólido, volumen Δ ${fmt(rt.step.volume_difference_mm3, 9)} mm³, caja Δ ${fmt(rt.step.max_bounds_difference_mm, 9)} mm · STL ${rt.stl.watertight ? 'cerrado' : 'abierto'} (${rt.stl.faces} triángulos) · ` + (rt.ok ? 'coincide' : 'NO coincide'), rt.ok ? 'ok' : 'bad'));
      const actions = make('div', null, 'actions');
      ['model.step', 'model.stl'].forEach((name) => { const a = make('a', 'Descargar ' + name, 'download'); a.href = row.downloads[name]; a.download = name; actions.append(a); });
      out.append(actions);
    }
    parametersForm(out, row);
    if (result.sketch) { sketchSection(out, result.sketch); sketchEditor(row.spec, result.sketch); }
    if (result.revolution) out.append(make('p', `Revolución de ${fmt(result.revolution.angle_deg, 1)}° alrededor de la recta ${result.revolution.axis}`, 'muted'));
    featuresSection(out, result);
    checksSection(out, result);
    const details = make('details');
    details.append(make('summary', 'Informe completo'), make('pre', JSON.stringify(row, null, 2)));
    out.append(details);
  }

  function renderSolve(result, spec, keepPart) {
    if (!keepPart) current = null;
    const out = el('part-output');
    out.replaceChildren(make('h3', 'Boceto resuelto (sin guardar)' + (current ? ` · cambios sobre ${current.spec.title}, revisión ${current.revision}` : '')));
    sketchSection(out, result);
    sketchEditor(spec || readSpec(), result);
  }

  function readSpec() {
    try { return JSON.parse(el('part-spec').value); } catch (e) { throw Error('La definición no es JSON válido: ' + e.message); }
  }

  function showList(parts) {
    current = null;
    el('part-editor').replaceChildren();
    const out = el('part-output');
    out.replaceChildren(make('h3', 'Piezas con restricciones'));
    if (!parts.length) { out.append(make('p', 'Todavía no hay piezas guardadas.')); return; }
    out.append(table(['Pieza', 'Revisión', 'Estado', 'Grados de libertad', 'Medidas', ''], parts.map((p) => {
      const open = make('button', 'Abrir', 'secondary');
      open.onclick = () => busy('Leyendo la pieza…', async () => { const row = await call('/api/parts/' + encodeURIComponent(p.id)); el('part-spec').value = JSON.stringify(row.spec, null, 2); render(row); });
      return [p.title, p.revision, make('span', p.status || p.state, 'badge ' + (p.status || '')), p.dof ?? '—', p.checks_passed === undefined || p.checks_passed === null ? '—' : mark(p.checks_passed, 'cumplen', 'fallan'), open];
    })));
  }

  async function init() {
    const data = await call('/api/part/examples');
    examples = data.examples;
    const bar = el('part-examples');
    Object.entries(examples).forEach(([key, spec]) => {
      const button = make('button', 'Ejemplo: ' + spec.title.split(' / ')[0], 'secondary');
      button.id = 'part-example-' + key;
      button.onclick = () => { el('part-spec').value = JSON.stringify(spec, null, 2); el('part-spec-box').open = true; el('part-status').textContent = 'Ejemplo cargado: resuélvelo o constrúyelo.'; };
      bar.append(button);
    });
    el('part-help').textContent = Object.entries(data.constraint_types).map(([k, v]) => `${k}: ${v}`).join('\n') + '\n\nCaras: ' +
      Object.values(data.face_references).join(' ') + '\n\nBases: ' + Object.values(data.base_shapes || {}).join(' ') +
      '\n\nOperaciones: ' + Object.entries(data.feature_types).map(([k, v]) => `${k}: ${v}`).join(' ') +
      '\n\nEjes de un boceto sobre una cara: ' + Object.values(data.face_frames || {}).join(' ');
    const first = Object.values(examples)[0];
    if (first && !el('part-spec').value) el('part-spec').value = JSON.stringify(first, null, 2);
  }

  el('part-solve').onclick = () => busy('Resolviendo el boceto…', async () => renderSolve(await call('/api/part/solve', { spec: readSpec() }), null, true));
  el('part-create').onclick = () => busy('Construyendo, midiendo y reimportando el STEP…', async () => render(await call('/api/parts', { request_id: crypto.randomUUID(), spec: readSpec() })));
  el('part-revise').onclick = () => busy('Revisando la pieza abierta…', async () => {
    if (!current) throw Error('Abre o construye primero una pieza para revisarla.');
    render(await call('/api/parts/' + encodeURIComponent(current.id) + '/revise', { base_revision: current.revision, request_id: crypto.randomUUID(), spec: readSpec() }));
  });
  el('part-list').onclick = () => busy('Leyendo las piezas…', async () => showList((await call('/api/parts')).parts));
  el('part-import').onclick = () => busy('Importando la receta…', async () => {
    let recipe;
    try { recipe = JSON.parse(el('part-recipe').value); } catch (e) { throw Error('La receta no es JSON válido: ' + e.message); }
    const row = await call('/api/parts/from-recipe', { request_id: crypto.randomUUID(), recipe });
    el('part-spec').value = JSON.stringify(row.spec, null, 2);
    render(row);
  });
  busy('Cargando ejemplos…', init);
  window.heronDesign = { render, renderSolve };
})();
