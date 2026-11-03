// Heron assemblies: build, edit parameters, read relations/residuals/DOF/interference, check revisions in place.
(() => {
  const el = (id) => document.getElementById(id);
  const make = (tag, text, cls) => { const n = document.createElement(tag); if (text !== undefined && text !== null) n.textContent = String(text); if (cls) n.className = cls; return n; };
  const fmt = (v, d = 3) => (typeof v === 'number' ? (Math.abs(v) < 1e-9 ? '0' : v.toFixed(d)) : (v ?? '—'));
  const STATUS = {
    solved: 'Resuelto: todas las relaciones se cumplen y no quedan movimientos libres',
    under_constrained: 'Grados de libertad restantes: las relaciones se cumplen pero hay movimientos libres',
    incompatible: 'Relaciones incompatibles: no hay posición que cumpla todas',
    ambiguous: 'Ambiguo: hay más de una solución y no se ha elegido ninguna',
  };
  const KIND = { penetration: 'Penetración', contact: 'Contacto', clearance: 'Holgura', not_checkable: 'No comprobable' };
  const STATE = { satisfied: 'se cumple', violated: 'NO se cumple', invalidated: 'invalidada', invalid_reference: 'referencia inválida', not_evaluated: 'sin evaluar' };
  let families = null, current = null;

  async function call(path, body) {
    const response = await fetch(path, body ? { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) } : {});
    const data = await response.json();
    if (!response.ok) throw Error(data.detail || data.error || 'No disponible');
    return data;
  }
  async function busy(fn) {
    const buttons = [...document.querySelectorAll('#assembly button')];
    buttons.forEach((b) => (b.disabled = true));
    el('asm-status').textContent = 'Resolviendo y comprobando…';
    try { await fn(); if (el('asm-status').textContent === 'Resolviendo y comprobando…') el('asm-status').textContent = 'Listo.'; }
    catch (e) { el('asm-status').textContent = e.message; }
    finally { buttons.forEach((b) => (b.disabled = false)); }
  }
  async function loadFamilies() { if (!families) families = await call('/api/assembly/families'); return families; }
  function setSpec(spec) { el('asm-spec').value = JSON.stringify(spec, null, 2); el('asm-spec-box').open = true; }

  function table(headers, rows) {
    const wrap = make('div', null, 'tablewrap'), t = make('table'), head = make('tr');
    headers.forEach((h) => head.append(make('th', h)));
    t.append(head);
    rows.forEach((cells) => { const tr = make('tr'); cells.forEach((c) => { const td = make('td'); if (c instanceof Node) td.append(c); else td.textContent = c ?? '—'; tr.append(td); }); t.append(tr); });
    wrap.append(t);
    return wrap;
  }
  function stateCell(state) { return make('span', STATE[state] || state, state === 'satisfied' ? 'ok' : state === 'violated' || state === 'invalid_reference' ? 'bad' : 'warn'); }

  function parametersEditor(row) {
    const box = make('div'), grid = make('div', null, 'params');
    box.append(make('h4', 'Cotas y parámetros'));
    const inputs = [];
    for (const [name, value] of Object.entries(row.spec.variables || {})) {
      const label = make('label', 'Variable ' + name), input = make('input');
      input.type = 'number'; input.step = 'any'; input.value = value; input.dataset.variable = name; input.setAttribute('aria-label', 'Variable ' + name);
      label.append(input); grid.append(label); inputs.push(input);
    }
    for (const comp of row.spec.components) {
      if (comp.source.kind !== 'family') continue;
      for (const [name, value] of Object.entries(comp.source.params || {})) {
        // Numbers or expressions over the variables (for example "standoff_h + board_t + gap").
        const label = make('label', comp.id + '.' + name), input = make('input');
        input.type = 'text'; input.inputMode = 'decimal'; input.value = value; input.dataset.component = comp.id; input.dataset.parameter = name;
        input.setAttribute('aria-label', comp.id + '.' + name);
        label.append(input); grid.append(label); inputs.push(input);
      }
    }
    const apply = make('button', 'Aplicar cambios (nueva revisión)');
    apply.id = 'asm-apply';
    apply.onclick = () => busy(async () => {
      const changes = [];
      for (const input of inputs) {
        const text = input.value.trim(), number = Number(text);
        const value = text !== '' && Number.isFinite(number) ? number : text;
        const old = input.dataset.variable ? row.spec.variables[input.dataset.variable] : row.spec.components.find((c) => c.id === input.dataset.component).source.params[input.dataset.parameter];
        if (value === '' || value === old || String(value) === String(old)) continue;
        if (input.dataset.variable && typeof value !== 'number') throw Error('La variable ' + input.dataset.variable + ' necesita un número.');
        changes.push(input.dataset.variable ? { variable: input.dataset.variable, value } : { component: input.dataset.component, parameter: input.dataset.parameter, value });
      }
      if (!changes.length) throw Error('No has cambiado ninguna cota.');
      show(await call('/api/assemblies/' + encodeURIComponent(row.id) + '/parameters', { base_revision: row.revision, request_id: crypto.randomUUID(), changes }));
    });
    box.append(grid, make('div', null, 'actions'));
    box.lastChild.append(apply);
    return box;
  }

  function revisionCheck(row) {
    const box = make('details'), summary = make('summary', 'Comprobar otra revisión de un componente sin realinearla');
    box.append(summary, make('p', 'La revisión nueva se coloca en la misma posición guardada del ensamblaje. No se vuelve a resolver ni se ajusta, para no ocultar choques.', 'muted'));
    const select = make('select'); select.setAttribute('aria-label', 'Componente a sustituir');
    row.spec.components.forEach((c) => { const o = make('option', c.id); o.value = c.id; select.append(o); });
    const source = make('textarea'); source.setAttribute('aria-label', 'Origen de la revisión'); source.id = 'asm-check-source';
    const fill = () => { source.value = JSON.stringify(row.spec.components.find((c) => c.id === select.value).source, null, 2); };
    select.onchange = fill; fill();
    const run = make('button', 'Comprobar sin realinear'); run.id = 'asm-check';
    const out = make('div'); out.id = 'asm-check-output';
    run.onclick = () => busy(async () => {
      const result = await call('/api/assemblies/' + encodeURIComponent(row.id) + '/revision-check', { request_id: crypto.randomUUID(), revision: row.revision, component: select.value, source: JSON.parse(source.value) });
      out.replaceChildren();
      if (!result.ok) { out.append(make('p', result.error, 'bad')); return; }
      out.append(make('p', result.new_penetrations.length ? 'Choques nuevos: ' + result.new_penetrations.map((p) => p.join(' ↔ ')).join(', ') : 'Sin penetraciones nuevas.', result.new_penetrations.length ? 'bad' : 'ok'));
      for (const [label, rev] of [['Revisión actual', result.revision_a], ['Revisión propuesta', result.revision_b]]) {
        out.append(make('h4', label));
        out.append(table(['Par', 'Resultado', 'Volumen común (mm³)', 'Separación (mm)'], rev.pairs.map((p) => [p.a + ' ↔ ' + p.b, KIND[p.kind] || p.kind, fmt(p.volume_mm3), fmt(p.min_distance_mm)])));
        out.append(table(['Relación', 'Estado', 'Residuo (mm)', 'Residuo (°)'], rev.relations.map((r) => [r.id, stateCell(r.state), fmt(r.residual_mm, 6), fmt(r.residual_deg, 6)])));
      }
      out.append(make('p', result.note, 'muted'));
    });
    const actions = make('div', null, 'actions'); actions.append(run);
    box.append(make('label', 'Componente'), select, source, actions, out);
    return box;
  }

  function show(row) {
    current = row;
    const out = el('asm-output'); out.replaceChildren();
    const r = row.result || {};
    out.append(make('h3', row.spec.title + ' · revisión ' + row.revision));
    if (row.state !== 'finished') { out.append(make('p', 'Estado: ' + row.state + (r.error ? ' — ' + r.error : ''), 'bad')); return; }
    const badge = make('span', STATUS[r.status] || r.status, 'badge ' + r.status); badge.id = 'asm-badge'; badge.dataset.status = r.status;
    out.append(badge);
    if (row.revisions && row.revisions.length > 1) {
      const nav = make('div', null, 'actions');
      row.revisions.forEach((n) => { const b = make('button', 'r' + n, n === row.revision ? '' : 'secondary'); b.onclick = () => busy(async () => show(await call('/api/assemblies/' + encodeURIComponent(row.id) + '?revision=' + n))); nav.append(b); });
      out.append(nav);
    }
    if (r.excluded_relations?.length) {
      out.append(make('h4', 'Relaciones excluidas'));
      out.append(table(['Relación', 'Estado', 'Motivo'], r.excluded_relations.map((e) => [e.id, stateCell(e.state), e.reason])));
    }
    for (const c of r.conflicts || []) out.append(make('p', `La relación «${c.failing_relation}» entre ${c.components.join(' y ')} no se cumple (residuo ${fmt(c.residual_mm)} mm, ${fmt(c.residual_deg)}°). Es incompatible junto con: ${c.conflicts_with.join(', ')}.`, 'bad conflict'));
    if (r.ambiguity) {
      out.append(make('p', 'Relaciones con orientación indeterminada: ' + r.ambiguity.relations.join(', ') + '. ' + r.ambiguity.candidates.length + ' soluciones posibles; indica la orientación para elegir.', 'warn'));
    }
    out.append(parametersEditor(row));
    const views = make('div', null, 'views');
    for (const name of ['isometric.svg', 'front.svg', 'top.svg']) {
      if (!row.downloads?.[name]) continue;
      const img = make('img'); img.alt = 'Vista ' + name.replace('.svg', '') + ' del ensamblaje'; img.setAttribute('src', row.downloads[name]); views.append(img);
    }
    out.append(views);
    out.append(make('h4', 'Relaciones y residuos'));
    out.append(table(['Relación', 'Tipo', 'A', 'B', 'Estado', 'Residuo (mm)', 'Residuo (°)'], r.relations.map((x) => [x.id, x.type, x.a || x.component, x.b || '', stateCell(x.state), x.type === 'clearance' ? 'mín ' + fmt(x.min_mm) + ' · medido ' + fmt(x.measured_gap_mm) : fmt(x.residual_mm, 6), fmt(x.residual_deg, 6)])));
    out.append(make('h4', 'Grados de libertad'));
    if (!r.remaining_dof.length) out.append(make('p', 'No quedan movimientos libres' + (r.status === 'incompatible' ? ' entre las relaciones aceptadas.' : '.'), 'ok'));
    r.remaining_dof.forEach((d) => out.append(make('p', `${d.component}: ${d.dof} libre(s) — ` + d.motions.map((m) => m.type === 'translation' ? 'traslación [' + m.direction.map((v) => fmt(v, 2)).join(', ') + ']' : 'giro sobre eje [' + m.axis_direction.map((v) => fmt(v, 2)).join(', ') + '] por (' + m.axis_point.map((v) => fmt(v, 2)).join(', ') + ')').join('; '), 'warn dof')));
    if (r.symmetric_dof.length) out.append(make('p', 'Giros sobre ejes de simetría (no cambian la geometría): ' + r.symmetric_dof.map((s) => s.component).join(', '), 'muted'));
    out.append(make('h4', 'Interferencias'));
    const verdict = { no_penetration: 'Sin penetraciones entre sólidos comprobados', interference: 'Hay interferencias', incomplete: 'Incompleto: hay pares que no se pueden comprobar', not_evaluated: 'No evaluado' }[r.interference.verdict] || r.interference.verdict;
    const v = make('p', verdict, r.interference.verdict === 'no_penetration' ? 'ok' : 'bad'); v.id = 'asm-verdict'; v.dataset.verdict = r.interference.verdict; out.append(v);
    const pairs = r.interference.pairs.filter((p) => p.kind !== 'clearance' || p.requested_clearance);
    out.append(table(['Par', 'Resultado', 'Volumen común (mm³)', 'Separación (mm)', 'Método', 'Holgura pedida'], pairs.map((p) => [p.a + ' ↔ ' + p.b, (KIND[p.kind] || p.kind) + (p.containment ? ' (contenido por completo)' : ''), fmt(p.volume_mm3), fmt(p.min_distance_mm), p.method === 'native_brep_boolean' ? 'sólidos B-rep (exacto)' : 'aproximación de malla' + (p.reason ? ': ' + p.reason : ''), (p.requested_clearance || []).map((c) => c.relation + ' ≥ ' + c.min_mm + ' mm: ' + (c.verdict === 'met' ? 'se cumple' : 'NO se cumple')).join('; ')])));
    if (r.mounting_axes?.length) {
      const moved = r.mounting_axes.filter((m) => m.axis_shift_mm > 1e-6 || m.direction_change_deg > 1e-6);
      out.append(make('p', moved.length ? 'Ejes de montaje desplazados respecto a la revisión anterior: ' + moved.map((m) => m.reference + ' ' + fmt(m.axis_shift_mm) + ' mm').join(', ') : 'Los ejes de montaje se conservan respecto a la revisión anterior.', moved.length ? 'warn' : 'ok'));
    }
    const changes = Object.values(r.components).filter((c) => c.reference_changes && (c.reference_changes.invalidated.length || c.reference_changes.moved_in_part.length));
    if (changes.length) out.append(table(['Componente', 'Referencias invalidadas', 'Referencias movidas en la pieza'], changes.map((c) => [c.id, c.reference_changes.invalidated.join(', ') || '—', c.reference_changes.moved_in_part.join(', ') || '—'])));
    out.append(make('h4', 'Componentes y posiciones'));
    out.append(table(['Componente', 'Origen', 'Revisión', 'Posición (mm)', 'Giro XYZ (°)'], Object.values(r.components).map((c) => [c.name, c.source.kind === 'family' ? c.source.family : c.source.kind === 'build' ? 'pieza ' + c.source.build_id : 'malla', (c.revision.digest || c.revision.step_sha256 || '').slice(0, 12), c.pose.translation_mm.map((v) => fmt(v, 3)).join(', '), c.pose.rotation_deg_xyz.map((v) => fmt(v, 2)).join(', ')])));
    const actions = make('div', null, 'actions');
    for (const name of ['manifest.json', 'assembly.step', 'assembly.glb']) {
      if (!row.downloads?.[name]) continue;
      const a = make('a', name, 'download'); a.setAttribute('href', row.downloads[name]); a.setAttribute('download', name); actions.append(a);
    }
    out.append(actions);
    if (r.roundtrip) out.append(make('p', 'Reimportación: STEP ' + (r.roundtrip.step.ok ? 'correcta' : 'con diferencias') + ', GLB ' + (r.roundtrip.glb.ok ? 'correcta' : 'con diferencias') + ' (nombres y posiciones de los componentes).', r.roundtrip.ok ? 'ok' : 'bad'));
    out.append(revisionCheck(row));
    out.append(make('p', r.limits, 'muted'));
    setSpec(row.spec);
  }

  el('asm-example-box').onclick = () => busy(async () => { setSpec((await loadFamilies()).examples.box_board_lid); el('asm-status').textContent = 'Ejemplo cargado: pulsa «Resolver y guardar».'; });
  el('asm-example-bracket').onclick = () => busy(async () => { setSpec((await loadFamilies()).examples.bracket_screw); el('asm-status').textContent = 'Ejemplo cargado: pulsa «Resolver y guardar».'; });
  el('asm-create').onclick = () => busy(async () => { show(await call('/api/assemblies', { request_id: crypto.randomUUID(), spec: JSON.parse(el('asm-spec').value) })); });
  el('asm-list').onclick = () => busy(async () => {
    const out = el('asm-output'); out.replaceChildren();
    const rows = (await call('/api/assemblies')).assemblies;
    if (!rows.length) out.append(make('p', 'Todavía no hay ensamblajes guardados.'));
    rows.forEach((a) => { const b = make('button', `${a.title} · r${a.revision} · ${a.status || a.state}`, 'secondary'); b.onclick = () => busy(async () => show(await call('/api/assemblies/' + encodeURIComponent(a.id)))); out.append(b); });
  });
  const opened = new URLSearchParams(location.search).get('assembly');
  if (opened) busy(async () => show(await call('/api/assemblies/' + encodeURIComponent(opened))));
  window.heronAssembly = { current: () => current };
})();
