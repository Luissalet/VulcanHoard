"use strict";
// Pure helpers that describe the open view to the family assistant panel.
// Loaded as a classic script in the browser and required by node tests.
(function (root) {
  const same = (a, b) => JSON.stringify(a) === JSON.stringify(b);

  // Only the top-level fields that differ from what was loaded.
  function changedFields(initial, draft) {
    const base = initial || {};
    return Object.fromEntries(Object.entries(draft || {}).filter(([key, value]) => !same(value, base[key])));
  }

  // Ask every open form for its unsaved fields. Each form answers with
  // detail.provide({kind, object_id, dirty, fields}).
  function editorContext(target, detail) {
    const found = [];
    target.dispatchEvent(new CustomEvent("heron:context-request", {
      detail: {
        ...(detail || {}),
        provide: (value) => { if (value && typeof value === "object" && value.kind) found.push(value); },
      },
    }));
    if (!found.length) return { editor: undefined, editors: undefined };
    const first = found.find((f) => f.dirty) || found[0];
    return { editor: first, editors: found.length > 1 ? found : undefined };
  }

  // Answer for the recipe form. An unsaved draft exists only in the editor, so invalid JSON is sent whole (never clipped).
  function recipeSnapshot({ loadedId, loadedRecipe, loadedText, text }) {
    const dirty = text !== loadedText;
    let fields = {};
    if (dirty) {
      try {
        const draft = JSON.parse(text);
        fields = draft && typeof draft === "object" && !Array.isArray(draft)
          ? changedFields(loadedRecipe, draft) : { recipe_text: String(text), invalid_json: true };
      } catch (e) {
        fields = { recipe_text: String(text), invalid_json: true };
      }
    }
    return { kind: "cad_recipe", object_id: loadedId || null, dirty, fields };
  }

  // One trailing call per burst, so the event and onTurnEnd trigger one refresh.
  function debounced(run, wait, timers) {
    const t = timers || { set: (fn, ms) => setTimeout(fn, ms), clear: (h) => clearTimeout(h) };
    let handle = null;
    return function schedule() {
      if (handle !== null) t.clear(handle);
      handle = t.set(() => { handle = null; run(); }, wait);
    };
  }

  // row: a retained build receipt, or null when nothing real is open (new recipe, history list, errors).
  function buildContext({ row, page, editor, editors } = {}) {
    const out = { active_objects: [], view: { page: page || "new" } };
    if (row && row.id && row.recipe) {
      const r = row.result || {};
      const mesh = r.mesh || {};
      const obj = {
        kind: "cad_build", object_id: row.id, title: row.recipe.title, state: row.state,
        parts: Array.isArray(row.recipe.parts) ? row.recipe.parts.length : undefined,
      };
      if (row.state === "finished") {
        Object.assign(obj, {
          solids: r.solids, bounds_mm: r.bounds_mm, volume_mm3: r.volume_mm3, valid: r.valid,
          watertight: mesh.watertight, files: Object.keys(r.files || {}),
        });
      } else if (r.error) obj.error = String(r.error).slice(0, 300);
      out.active_objects.push(obj);
      out.view = { page: "build", object_id: row.id };
    } else if (page === "history") out.view = { page: "history" };
    else out.view = { page: "new" };
    if (editor) out.view.editor = editor;
    if (editors) out.view.editors = editors;
    return out;
  }

  const api = { changedFields, editorContext, recipeSnapshot, debounced, buildContext };
  if (typeof module !== "undefined" && module.exports) module.exports = api;
  else root.heronCopilotContext = api;
})(typeof globalThis !== "undefined" ? globalThis : this);
