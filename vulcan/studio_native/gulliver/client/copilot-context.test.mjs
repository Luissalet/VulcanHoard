import { test } from "node:test";
import assert from "node:assert/strict";
import { buildContext, parseRoute, modelObject } from "./src/copilot-context.js";

const model = { id: "261014abcdef", name: "Lilliput", status: "ready", place: "Lilliput (demo)", params: { lon: -3.7, lat: 40.42, width_m: 2000, height_m: 1500, size_mm: 180, shape: "rect" },
  report: { scale: "1:11.000", size_mm: [180, 135, 14], tiles: 2, weight_g_pla: 95, fits_bed: true, check: { watertight: true } } };
const deps = {
  getList: async () => ({ models: [model] }),
  getModel: async (id) => (id === model.id ? model : Promise.reject(new Error("404"))),
  getSettings: async () => ({ bed_x_mm: 256, bed_y_mm: 256, dem: "auto" }),
};

test("routes", () => {
  assert.deepEqual(parseRoute("#/m/abc").param, "abc");
  assert.equal(parseRoute("#/nueva?edit=x").query.get("edit"), "x");
});

test("library lists the models", async () => {
  const ctx = await buildContext({ hash: "#/", ...deps });
  assert.equal(ctx.view.page, "library");
  assert.equal(ctx.active_objects[0].object_id, model.id);
  assert.equal(ctx.active_objects[0].scale, "1:11.000");
  assert.equal(ctx.active_objects[0].weight_g_pla, 95);
  assert.equal(ctx.active_objects[0].watertight, true);
});

test("model page and editor snapshot", async () => {
  const ctx = await buildContext({ hash: `#/m/${model.id}`, editors: [{ kind: "x", dirty: true, fields: { a: 1 } }], ...deps });
  assert.equal(ctx.view.object_id, model.id);
  assert.equal(ctx.view.editor.kind, "x");
  const unknown = await buildContext({ hash: "#/m/nope", ...deps });
  assert.deepEqual(unknown.active_objects, []);
});

test("edit form and settings", async () => {
  const edit = await buildContext({ hash: `#/nueva?edit=${model.id}`, ...deps });
  assert.equal(edit.view.page, "new-model");
  assert.equal(edit.active_objects[0].kind, "map-model");
  const s = await buildContext({ hash: "#/ajustes", ...deps });
  assert.equal(s.active_objects[0].bed_x_mm, 256);
  assert.deepEqual(modelObject(model).centre, [-3.7, 40.42]);
});

test("area view page describes its crop and layer", async () => {
  const view = { view_id: "ab12cd34", name: "Plaza", crop: { lon: -5.66, lat: 40.96, width_m: 600, height_m: 400, shape: "rect" }, layer: { id: "viirs", date: "2024-06-15" },
    model_check: { scale: "1:3.300", model_mm: [180, 120] } };
  const asked = [];
  const ctx = await buildContext({ hash: "#/vista?view=ab12cd34", ...deps, getView: async (id) => { asked.push(id); return view; } });
  assert.deepEqual(asked, ["ab12cd34"]);
  assert.equal(ctx.view.page, "area-view");
  assert.equal(ctx.view.object_id, "ab12cd34");
  assert.deepEqual(ctx.active_objects[0], { kind: "area-view", object_id: "ab12cd34", title: "Plaza", centre: [-5.66, 40.96], ground_m: [600, 400],
    shape: "rect", layer: "viirs", date: "2024-06-15", scale: "1:3.300", size_mm: [180, 120] });
  const none = await buildContext({ hash: "#/vista", ...deps, getView: async () => { throw new Error("no view"); } });
  assert.deepEqual(none.active_objects, []);
});
