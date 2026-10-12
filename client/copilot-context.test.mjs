import { test } from "node:test";
import assert from "node:assert/strict";
import { addRefreshHandler, buildContext, changedFields, filtersFromQuery, installCopilot, onTurnEnd, parseRoute, readViewStates, rebaseForm, setViewState } from "./src/copilot-context.js";

const model = {
  id: 7, name: "Dragon", path: "C:/models/Dragons/dragon.stl", format: "stl", collection: "Dragons", tags: ["dragon", "fantasy"],
  triangles: 12000, volume_cm3: 20.5, watertight: true, units_guess: "mm", status: "ok",
  listing: { title: "Dragon figure", description: "A dragon.", tags: ["dragon"], category: "figures", price_hint: "3-5", language: "en", listing_source: "manual" },
  dupes: { exact: [{ id: 8 }], near: [] },
};
const folderListing = { root_id: 1, rel_path: "Dragons", title: "Dragon pack", description: "x".repeat(300), tags: ["a", "b"], status: "draft", issues: ["Description too short"] };
const roots = { roots: [{ id: 1, name: "Library", path: "C:/models", enabled: true, watch: false, thumbnails: "all", last_scanned_at: 1760000000 }] };
const collections = { albums: [{ id: 3, name: "Spring campaign", count: 4 }], folders: [{ name: "Dragons", count: 2 }] };
const deps = {
  getModel: async (id) => { if (Number(id) !== 7) throw new Error("Model not found."); return model; },
  getFolderListing: async (rootId, path) => { if (Number(rootId) !== 1 || path !== "Dragons") throw new Error("No folder listing at that path."); return folderListing; },
  getRoots: async () => roots,
  getCollections: async () => collections,
};
const ids = (ctx) => ctx.active_objects.map((o) => `${o.kind}:${o.object_id}`);

test("route parsing and the gallery filters of the address", () => {
  assert.deepEqual([parseRoute("").page, parseRoute("").param], ["galeria", null]);
  const r = parseRoute("#/modelo/7?x=1");
  assert.deepEqual([r.page, r.param, r.query.get("x")], ["modelo", "7", "1"]);
  const q = parseRoute("#/galeria?q=dragon&root=1&tag=&sort=name&other=1").query;
  assert.deepEqual(filtersFromQuery(q), { q: "dragon", root: "1", sort: "name" });
});

test("changed fields of a form and rebasing onto a refreshed baseline", () => {
  assert.deepEqual(changedFields({ a: 1, b: "x", tags: ["t"] }, { a: 1, b: "", tags: ["t"] }), { b: "" });
  assert.deepEqual(changedFields(null, { a: "x" }), { a: "x" });
  assert.deepEqual(rebaseForm({ title: "A", category: "c" }, { title: "Agent", category: "c" }, { title: "A", category: "typed" }), { title: "Agent", category: "typed" });
});

test("model page: the model, its store listing and the unsaved fields", async () => {
  const states = { "listing-form": { id: 7, fields: { title: "Dragon, new" } }, "model-form": { id: 7, fields: { notes: "print at 0.1" } } };
  const ctx = await buildContext({ hash: "#/modelo/7", states, ...deps });
  assert.deepEqual(ids(ctx), ["model:7", "listing:7"]);
  assert.equal(ctx.active_objects[0].title, "Dragon");
  assert.equal(ctx.active_objects[0].watertight, true);
  assert.equal(ctx.active_objects[0].exact_duplicates, 1);
  assert.equal(ctx.active_objects[1].title, "Dragon figure");
  assert.equal(ctx.active_objects[1].description_chars, 9);
  assert.equal(ctx.view.object_id, "7");
  assert.equal(ctx.view.editors.length, 2);
  assert.deepEqual(ctx.view.editors.map((e) => e.kind).sort(), ["listing-form", "model-form"]);
  assert.ok(ctx.view.editor.dirty);
});

test("gallery: filters, the album/root/folder behind them and the top models on screen", async () => {
  const states = { galeria: { total: 2, models: [{ id: 7, name: "Dragon", format: "stl", has_listing: true }, { id: 9, name: "Wyvern", format: "3mf" }] }, "gallery-search": { fields: { q: "dragon fire" } } };
  const ctx = await buildContext({ hash: "#/galeria?root=1&album=3&collection=Dragons&q=dragon", states, ...deps });
  assert.deepEqual(ids(ctx), ["root:1", "album:3", "collection:Dragons", "model:7", "model:9"]);
  assert.deepEqual(ctx.view.filters, { q: "dragon", root: "1", album: "3", collection: "Dragons" });
  assert.equal(ctx.view.total, 2);
  assert.deepEqual(ctx.view.editor, { kind: "gallery-search", object_id: null, dirty: true, fields: { q: "dragon fire" } });
});

test("collections, folder listings, roots, organize and statistics", async () => {
  const col = await buildContext({ hash: "#/colecciones", states: { colecciones: { open: 3 }, "album-new": { fields: { name: "Summer" } }, "album-field:3": { id: 3, fields: { name: "Spring 2" } } }, ...deps });
  assert.deepEqual(ids(col), ["album:3"]);
  assert.equal(col.view.open_album, "3");
  assert.deepEqual(col.view.editors.map((e) => e.kind).sort(), ["album-form", "album-new"]);

  const fichas = await buildContext({ hash: "#/fichas", states: { fichas: { root_id: "1", status: "draft", counts: { total: 3, none: 1 }, selected: { root_id: 1, rel_path: "Dragons" } }, "folder-listing-form": { root_id: 1, rel_path: "Dragons", fields: { title: "Dragons, pack" } } }, ...deps });
  assert.deepEqual(ids(fichas), ["folder_listing:1:Dragons"]);
  assert.equal(fichas.active_objects[0].issue_count, 1);
  assert.equal(fichas.active_objects[0].description_chars, 300);
  assert.equal(fichas.view.status_filter, "draft");
  assert.equal(fichas.view.editor.object_id, "1:Dragons");

  const carpetas = await buildContext({ hash: "#/carpetas", states: { "root-new": { fields: { path: "C:/new" } } }, ...deps });
  assert.deepEqual(ids(carpetas), ["root:1"]);
  assert.equal(carpetas.view.editor.kind, "root-new");

  const org = await buildContext({ hash: "#/organizar", states: { organizar: { plan: { id: "p1", summary: { to_move: 5, conflicts: 0 }, stale: true } }, "organize-form": { fields: { root: "C:/x", reference: "x".repeat(4000) } } }, ...deps });
  assert.deepEqual(ids(org), ["organize_plan:p1", "root:1"]);
  assert.equal(org.active_objects[0].to_move, 5);
  assert.equal(org.active_objects[0].stale, true);
  assert.ok(org.view.editor.fields.reference.length < 1600);

  const stats = await buildContext({ hash: "#/estadisticas", states: { estadisticas: { summary: { models: 10, listings: 4 } } }, ...deps });
  assert.deepEqual(stats.active_objects, []);
  assert.deepEqual(stats.view.summary, { models: 10, listings: 4 });
  const settings = await buildContext({ hash: "#/ajustes", states: {}, ...deps });
  assert.deepEqual(settings.active_objects, []);
});

test("unknown ids, failing reads and a model that does not exist never throw and yield no objects", async () => {
  const fail = async () => { throw new Error("down"); };
  const broken = { getModel: fail, getFolderListing: fail, getRoots: fail, getCollections: fail };
  const states = { fichas: { selected: { root_id: 1, rel_path: "Dragons" } }, colecciones: { open: 3 } };
  for (const hash of ["", "#/modelo/7", "#/modelo/new", "#/modelo/abc", "#/galeria?root=1&album=3", "#/colecciones", "#/fichas", "#/carpetas", "#/organizar", "#/nope"]) {
    const ctx = await buildContext({ hash, states, ...broken });
    assert.ok(Array.isArray(ctx.active_objects), hash);
    assert.deepEqual(ctx.active_objects.filter((o) => o.kind !== "collection"), [], hash);
    assert.ok(ctx.view.page);
  }
  const missing = await buildContext({ hash: "#/modelo/999", states: {}, ...deps });
  assert.deepEqual(missing.active_objects, []);
  const emptyForm = await buildContext({ hash: "#/modelo/7", states: { "listing-form": { id: 7, fields: {} } }, ...deps });
  assert.equal(emptyForm.view.editor, undefined);
});

test("view states are a registry: set and clear", () => {
  setViewState("a", { x: 1 });
  assert.deepEqual(readViewStates().a, { x: 1 });
  setViewState("a", null);
  assert.equal(readViewStates().a, undefined);
});

test("turn end (event and callback) refreshes every registered view once", async () => {
  const target = new EventTarget();
  installCopilot(target);
  assert.equal(typeof target.hoardCopilot.context, "function");
  assert.equal(typeof target.hoardCopilot.onTurnEnd, "function");
  let a = 0;
  let b = 0;
  const offA = addRefreshHandler(() => { a += 1; });
  addRefreshHandler(() => { b += 1; });
  addRefreshHandler(() => { throw new Error("a broken view must not stop the others"); });
  target.dispatchEvent(new Event("hoard:copilot-turn-end"));
  onTurnEnd();
  await new Promise((r) => setTimeout(r, 400));
  assert.deepEqual([a, b], [1, 1]);
  offA();
  onTurnEnd();
  await new Promise((r) => setTimeout(r, 400));
  assert.deepEqual([a, b], [1, 2]);
});
