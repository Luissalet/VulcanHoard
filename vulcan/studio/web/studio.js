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
    $("#summary").textContent = active
      ? `${active} de ${data.modules.length} funciones activas`
      : "Todo desactivado · ningún motor en segundo plano";
    $("#stop-all").disabled = !active;
    // Preserve in-progress project forms while reflecting the current engines.
    for (const select of document.querySelectorAll("[data-module-selector]")) {
      for (const option of select.options) {
        const module = data.modules.find((item) => item.id === option.value);
        if (module) option.textContent = module.name + (module.enabled ? "" : " · apagada");
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
await refresh();
try {
  await projects();
} catch (error) {
  showError(error.message);
}
