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
  const OP = { hole: 'Taladro', fillet: 'Redondeo', chamfer: 'Chaflán', shell: 'Vaciado' };
  const FSTATE = { resolved: 'resuelta', invalidated: 'INVALIDADA', failed: 'falló', not_evaluated: 'sin evaluar' };
  const CHANGE = { kept: 'se conserva', invalidated: 'invalidada', new: 'nueva', removed: 'quitada', restored: 'recuperada' };
  const MEASURE = { hole_distance: 'distancia entre taladros', hole_diameter: 'diámetro del taladro', bbox_x: 'caja X', bbox_y: 'caja Y', bbox_z: 'caja Z', volume: 'volumen' };
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
        (f.holes ? ` · ${f.holes.length} taladro(s)` : '') + (f.edges ? ` · ${f.edges} aristas` : '') : (f.reason || '—')])));
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
    if (result.sketch) sketchSection(out, result.sketch);
    featuresSection(out, result);
    checksSection(out, result);
    const details = make('details');
    details.append(make('summary', 'Informe completo'), make('pre', JSON.stringify(row, null, 2)));
    out.append(details);
  }

  function renderSolve(result) {
    current = null;
    const out = el('part-output');
    out.replaceChildren(make('h3', 'Boceto resuelto (sin guardar)'));
    sketchSection(out, result);
  }

  function readSpec() {
    try { return JSON.parse(el('part-spec').value); } catch (e) { throw Error('La definición no es JSON válido: ' + e.message); }
  }

  function showList(parts) {
    current = null;
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
      Object.values(data.face_references).join(' ');
    const first = Object.values(examples)[0];
    if (first && !el('part-spec').value) el('part-spec').value = JSON.stringify(first, null, 2);
  }

  el('part-solve').onclick = () => busy('Resolviendo el boceto…', async () => renderSolve(await call('/api/part/solve', { spec: readSpec() })));
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
