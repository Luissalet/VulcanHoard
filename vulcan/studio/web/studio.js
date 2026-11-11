let dockQuery = "";
let dockFocus = null;
const dockFold = (value) => String(value || "").normalize("NFD").replace(/\p{M}/gu, "").toLowerCase().trim();
const dockMatches = (value, query) => dockFold(value).includes(dockFold(query));
const $ = (selector) => document.querySelector(selector);
const element = (tag, value, className = "") => {
  const node = document.createElement(tag);
  node.textContent = value;
  node.className = className;
  return node;
};
async function request(path, options = {}) {
  const response = await fetch(path, {
    headers: { "Content-Type": "application/json" },
    ...options,
  });
  const data = await response.json();
  if (!response.ok)
    throw new Error(
      typeof data.detail === "string"
        ? data.detail
        : data.error || "No se pudo completar la operación.",
    );
  return data;
}
function showError(message) {
  $("#error").textContent = message;
  $("#error").hidden = !message;
}
async function refresh() {
  try {
    const data = await request("/api/modules");
    const active = data.modules.filter((module) => module.enabled).length;
    const pending = data.modules.filter((module) => module.phase === "stopping").length;
    $("#summary").textContent = pending
      ? `${pending} cierres pendientes · ${active} funciones activas`
      : active
      ? `${active} de ${data.modules.length} funciones activas`
      : "Todo desactivado · ningún motor en segundo plano";
    $("#stop-all").disabled = !active && !pending;
    // Preserve in-progress project forms while reflecting the current engines.
    for (const select of document.querySelectorAll("[data-module-selector]")) {
      for (const option of select.options) {
        const module = data.modules.find((item) => item.id === option.value);
        if (module) option.textContent = module.name + (module.phase === "stopping" ? " · cierre pendiente" : module.enabled ? "" : " · apagada");
      }
    }
    $("#modules").replaceChildren();
    $("#module-nav").replaceChildren();
    for (const module of data.modules) {
      const row = element("article", "", "module");
      row.id = module.id;
      const content = element("div", "");
      const heading = element("h2", module.name);
      heading.append(element("span", module.origin, "origin"));
      content.append(heading, element("p", module.description));
      const details = element("details", "");
      details.append(
        element("summary", "Ubicación de los datos"),
        element("code", module.data_dir),
      );
      content.append(details);
      if (module.error)
        content.append(element("p", module.error, "module-error"));
      const controls = element("div", "", "controls");
      const open = element("a", "Abrir", "open");
      open.href = `/${module.id}/`;
      open.setAttribute("aria-label", `Abrir ${module.name}`);
      open.setAttribute("aria-disabled", String(!module.enabled));
      if (!module.enabled) open.tabIndex = -1;
      const toggle = element("button", "", "switch");
      toggle.type = "button";
      toggle.setAttribute("role", "switch");
      toggle.setAttribute("aria-checked", String(module.enabled));
      toggle.setAttribute("aria-label", module.name);
      const label = element(
        "span",
        module.phase === "stopping" ? "Reintentar apagado" : module.enabled ? "Activada" : "Desactivada",
      );
      const track = element("span", "", "switch-track");
      track.setAttribute("aria-hidden", "true");
      track.append(element("span", "", "switch-thumb"));
      toggle.append(label, track);
      toggle.onclick = async () => {
        toggle.disabled = true;
        label.textContent = module.enabled || module.phase === "stopping" ? "Deteniendo…" : "Activando…";
        showError("");
        try {
          await request(`/api/modules/${module.id}`, {
            method: "PUT",
            body: JSON.stringify({ enabled: module.phase !== "stopping" && !module.enabled }),
          });
        } catch (error) {
          showError(error.message);
        } finally {
          await refresh();
          document.getElementById(module.id)?.querySelector("button")?.focus();
        }
      };
      controls.append(open, toggle);
      if (module.automation) {
        const auto = element("label", "", "automation");
        const check = element("input", "");
        check.type = "checkbox";
        check.checked = module.automatic;
        check.setAttribute("aria-label", `Automatismos de ${module.name}`);
        auto.append(check, document.createTextNode("Automatismos"));
        controls.append(auto);
        content.append(element("p", module.automation, "automation-help"));
        check.onchange = async () => {
          check.disabled = true;
          try {
            await request(`/api/modules/${module.id}`, {
              method: "PUT",
              body: JSON.stringify({
                enabled: module.enabled,
                automatic: check.checked,
              }),
            });
          } catch (error) {
            showError(error.message);
          } finally {
            await refresh();
          }
        };
      }
      row.append(content, controls);
      $("#modules").append(row);
      const nav = element("a", module.name, "nav-link");
      nav.href = "#" + module.id;
      $("#module-nav").append(nav);
    }
    $("#modules").setAttribute("aria-busy", "false");
  } catch (error) {
    showError(
      `${error.message} Comprueba que el servidor siga abierto y recarga la página.`,
    );
  }
}
async function projects() {
  const [data, modules] = await Promise.all([
    request("/api/studio/projects"),
    request("/api/modules"),
  ]);
  const list = $("#project-list");
  // Capture after the requests, immediately before replacing rows, including later typing.
  const drafts = [...list.querySelectorAll('[data-project-id]')].map((row) => ({
    id: row.id, fields: [...row.querySelectorAll('input, select')].map((el, index) => ({ index, value: el.value, focused: el === document.activeElement, start: el.selectionStart, end: el.selectionEnd,
      dirty: el.tagName === 'SELECT' ? el.value !== ([...el.options].find((option) => option.defaultSelected) || el.options[0])?.value : el.value !== el.defaultValue,
    })).filter((field) => field.dirty),
  }));
  list.replaceChildren();
  if (!data.projects.length)
    list.append(
      element(
        "p",
        "No hay proyectos compartidos. Crea uno para reunir las revisiones de una pieza.",
      ),
    );
  for (const project of data.projects) {
    const row = element("article", "");
    row.id = "project-" + project.id;
    row.dataset.projectId = project.id;
    row.dataset.projectTitle = project.title;
    row.hidden = !dockMatches(project.title, dockQuery);
    row.append(element("h3", project.title));
    const status = element("p", "", "project-status");
    status.setAttribute("role", "status");
    for (const link of project.links) {
      const target = element(
        "a",
        `${link.title || link.native_id} · ${link.module}${link.revision ? " · revisión " + link.revision : ""}`,
      );
      target.href = link.url || `/${link.module}/`;
      row.append(target, element("br", ""));
    }
    project.artifacts.forEach((artifact, index) => {
      const block = element("div", "", "artifact-path");
      const download = element("a", artifact.path.split(/[\\/]/).pop());
      download.href = `/api/studio/projects/${project.id}/artifacts/${index}/file`;
      block.append(
        download,
        element(
          "p",
          `Referencia registrada · SHA256 ${artifact.sha256.slice(0, 12)} · ${artifact.module}`,
        ),
      );
      const indexButton = element(
        "button",
        "Indexar en biblioteca",
        "secondary",
      );
      indexButton.type = "button";
      indexButton.onclick = async () => {
        indexButton.disabled = true;
        status.textContent = "Indexando el archivo con la biblioteca…";
        try {
          await request(`/api/studio/projects/${project.id}/index-artifact`, {
            method: "POST",
            body: JSON.stringify({ artifact_index: index }),
          });
          await projects();
        } catch (error) {
          status.textContent = error.message;
        } finally {
          indexButton.disabled = false;
        }
      };
      block.append(indexButton);
      row.append(block);
    });
    const bind = element("fieldset", "");
    bind.append(element("legend", "Vincular una pieza existente"));
    const fields = element("div", "", "piece-fields");
    const moduleSelect = element("select", "");
    moduleSelect.dataset.moduleSelector = "true";
    moduleSelect.setAttribute("aria-label", `Función para ${project.title}`);
    for (const module of modules.modules) {
      const option = element(
        "option",
        module.name + (module.enabled ? "" : " · apagada"),
      );
      option.value = module.id;
      moduleSelect.append(option);
    }
    const read = element("button", "Leer piezas", "secondary");
    read.type = "button";
    const objectSelect = element("select", "");
    objectSelect.setAttribute("aria-label", `Pieza para ${project.title}`);
    objectSelect.disabled = true;
    const save = element("button", "Vincular", "secondary");
    save.type = "button";
    save.disabled = true;
    let available = [];
    moduleSelect.onchange = () => {
      available = [];
      objectSelect.replaceChildren();
      objectSelect.disabled = save.disabled = true;
    };
    read.onclick = async () => {
      read.disabled = true;
      status.textContent = "Leyendo la función seleccionada…";
      try {
        const result = await request(
          "/api/studio/objects/" + moduleSelect.value,
        );
        available = result.objects;
        objectSelect.replaceChildren();
        for (const object of available) {
          const option = element(
            "option",
            object.title + (object.revision ? " · " + object.revision : ""),
          );
          option.value = object.native_id;
          objectSelect.append(option);
        }
        objectSelect.disabled = save.disabled = !available.length;
        status.textContent = available.length
          ? "Selecciona la pieza que quieres vincular."
          : "Esta función todavía no tiene piezas.";
      } catch (error) {
        status.textContent =
          "Activa la función para consultar sus piezas. " + error.message;
      } finally {
        read.disabled = false;
      }
    };
    save.onclick = async () => {
      const object = available.find(
        (item) => item.native_id === objectSelect.value,
      );
      if (!object) return;
      save.disabled = true;
      try {
        await request(`/api/studio/projects/${project.id}/links`, {
          method: "POST",
          body: JSON.stringify(object),
        });
        await projects();
      } catch (error) {
        status.textContent = error.message;
        save.disabled = false;
      }
    };
    fields.append(moduleSelect, read, objectSelect, save);
    bind.append(fields);
    row.append(bind);
    const artifactForm = element("form", "");
    const artifacts = element("fieldset", "");
    artifacts.append(element("legend", "Registrar un archivo exportado"));
    const origin = element("select", "");
    origin.setAttribute(
      "aria-label",
      `Pieza de origen del archivo para ${project.title}`,
    );
    project.links.forEach((link, index) => {
      const option = element(
        "option",
        `${link.title || link.native_id} · ${link.module}`,
      );
      option.value = String(index);
      origin.append(option);
    });
    const path = element("input", "");
    path.required = true;
    path.placeholder = "Ruta del archivo exportado en este equipo";
    path.setAttribute(
      "aria-label",
      `Ruta del archivo exportado para ${project.title}`,
    );
    const register = element("button", "Registrar archivo", "secondary");
    register.type = "submit";
    register.disabled = !project.links.length;
    const artifactFields = element("div", "", "piece-fields");
    artifactFields.append(origin, path, register);
    artifacts.append(
      artifactFields,
      element(
        "p",
        "Se conserva una referencia con su hash. El archivo no se copia ni se cambia de formato.",
      ),
    );
    artifactForm.append(artifacts);
    artifactForm.onsubmit = async (event) => {
      event.preventDefault();
      const link = project.links[Number(origin.value)];
      if (!link) return;
      register.disabled = true;
      try {
        await request(`/api/studio/projects/${project.id}/artifacts`, {
          method: "POST",
          body: JSON.stringify({
            module: link.module,
            native_id: link.native_id,
            revision: link.revision,
            path: path.value,
          }),
        });
        await projects();
      } catch (error) {
        status.textContent = error.message;
        register.disabled = false;
      }
    };
    row.append(artifactForm, status);
    list.append(row);
  }
  for (const draft of drafts) {
    const fields = document.getElementById(draft.id)?.querySelectorAll('input, select');
    for (const draftField of draft.fields) {
      const field = fields?.[draftField.index];
      if (!field) continue;
      field.value = draftField.value;
      if (draftField.focused) { field.focus(); if (typeof draftField.start === 'number') field.setSelectionRange(draftField.start, draftField.end); }
    }
  }
}
$("#project-form").onsubmit = async (event) => {
  event.preventDefault();
  const button = event.currentTarget.querySelector("button");
  button.disabled = true;
  showError("");
  try {
    await request("/api/studio/projects", {
      method: "POST",
      body: JSON.stringify({ title: $("#project-title").value }),
    });
    $("#project-form").reset();
    await projects();
  } catch (error) {
    showError(error.message);
  } finally {
    button.disabled = false;
  }
};
$("#stop-all").onclick = async () => {
  showError("");
  try {
    await request("/api/modules/stop-all", { method: "POST", body: "{}" });
  } catch (error) {
    showError(error.message);
  } finally {
    await refresh();
  }
};
installAppDock();
await refresh();
try {
  await projects();
} catch (error) {
  showError(error.message);
}


function installAppDock() {
  const app = window.HoardApp ||= { q: [] };
  const send = (method, ...args) => app[method] ? app[method](...args) : app.q.push([method, ...args]);
  const error = (code, message, candidates) => Object.assign(new Error(message), { code, candidates });
  const choose = (items, args, label) => {
    const exact = items.filter((item) => args.id ? item.id === args.id : dockFold(label(item)) === dockFold(args.name));
    const matches = exact.length || args.id ? exact : items.filter((item) => dockMatches(label(item), args.name));
    if (matches.length !== 1) throw error(matches.length ? 'ambiguous' : 'not_found', matches.length ? 'Several records match; use an id' : 'Record not found', matches.slice(0, 8).map((item) => ({ id: item.id, label: label(item) })));
    return matches[0];
  };
  const point = async (cursor, el) => { if (el) { el.scrollIntoView({ block: 'center' }); await cursor.to(el); } };
  const tool = (name, description, properties, required, handler) => send('registerTool', name, {
    description, inputSchema: { type: 'object', properties, required, additionalProperties: false },
  }, handler);
  const reference = { id: { type: 'string', minLength: 1 }, name: { type: 'string', minLength: 1 } };
  const requireRef = (args) => { if (!args.id && !args.name?.trim()) throw error('bad_args', 'Provide id or name'); };
  tool('vulcan_project', 'Select a shared project by id or unique name', reference, [], async (args, { cursor }) => {
    requireRef(args);
    const project = choose((await request('/api/studio/projects')).projects, args, (item) => item.title);
    dockQuery = ''; await projects();
    const row = document.getElementById('project-' + project.id);
    await point(cursor, row); location.hash = '#project-' + project.id;
    dockFocus = { kind: 'project', id: project.id, label: project.title };
    return { selected: project.id };
  });
  tool('vulcan_filter', 'Filter the shared projects by name; empty query clears it', { query: { type: 'string' } }, ['query'], async ({ query }, { cursor }) => {
    dockQuery = query; await projects();
    const rows = [...document.querySelectorAll('[data-project-id]')].filter((row) => !row.hidden);
    await point(cursor, rows[0] || $('#projects-title')); location.hash = '#projects'; dockFocus = null;
    return { query, count: rows.length };
  });
  tool('vulcan_module', 'Open an enabled native module by id or unique name', reference, [], async (args, { cursor }) => {
    requireRef(args);
    const module = choose((await request('/api/modules')).modules, args, (item) => item.name);
    if (!module.enabled) throw error('disabled', 'The module is off; enable it from Functions first');
    await point(cursor, document.getElementById(module.id));
    location.assign(`/${encodeURIComponent(module.id)}/`);
    return { opened: module.id };
  });
  tool('vulcan_view', 'Show functions or shared projects', { view: { type: 'string', enum: ['functions', 'projects'] } }, ['view'], async ({ view }, { cursor }) => {
    await point(cursor, document.getElementById(view)); location.hash = '#' + view; dockFocus = null; return { view };
  });
  const focus = (event) => {
    const project = event.target.closest?.('[data-project-id]');
    const module = event.target.closest?.('.module');
    if (project) dockFocus = { kind: 'project', id: project.dataset.projectId, label: project.dataset.projectTitle };
    else if (module) dockFocus = { kind: 'module', id: module.id, label: module.querySelector('h2')?.firstChild?.textContent || module.id };
  };
  document.addEventListener('click', focus); document.addEventListener('focusin', focus);
  send('setContext', () => ({ view: location.hash.startsWith('#project') ? 'projects' : 'functions', focus: dockFocus, summary: dockQuery ? `Project filter: ${dockQuery}` : '' }));
  send('onChanged', async () => { await refresh(); await projects(); });
}
