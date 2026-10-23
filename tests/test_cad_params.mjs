import test from "node:test";
import assert from "node:assert/strict";
import { cadAcknowledgedFields, cadDraftValues, cadParamChanges, cadVariantRequest } from "../client/src/cad-params.js";
import { api } from "../client/src/api.js";
import { draftState, changeDraft, acknowledgeDraft, refreshDraft } from "../client/src/drafts.js";

test("only the edited dimension is sent, derived values refresh and later edits stay pending", () => {
  const params = [{ name: "width", value: 40 }, { name: "height", value: 20, expression: "$width / 2" }, { name: "offset", value: 0 }];
  const fields = { ...cadDraftValues(params), width: "60", height: "20.0" };
  assert.deepEqual(cadParamChanges(params, fields), { changes: { width: 60 }, error: "" });
  assert.deepEqual(cadParamChanges(params, { ...fields, height: "25" }).changes, { width: 60, height: 25 });
  assert.match(cadParamChanges(params, { ...fields, width: "" }).error, /width/);
  const snapshot = { ...fields };
  const newParams = params.map((param) => ({ ...param, value: param.name === "width" ? 60 : param.name === "height" ? 30 : 0 }));
  const saved = { width: "60", height: "30", offset: "0" };
  for (const [later, expected, pending] of [
    [{}, saved, {}],
    [{ height: "20.00" }, saved, {}],
    [{ width: "70", height: "38" }, { width: "70", height: "38", offset: "0" }, { width: 70, height: 38 }],
    [{ width: "40" }, { width: "40", height: "30", offset: "0" }, { width: 40 }],
  ]) {
    let draft = changeDraft(draftState(cadDraftValues(params)), fields);
    draft = changeDraft(draft, later);
    draft = refreshDraft(acknowledgeDraft(draft, cadAcknowledgedFields(snapshot, draft.fields), saved), saved);
    assert.deepEqual(draft.fields, expected);
    assert.deepEqual(draft.dirty, Object.keys(pending));
    assert.deepEqual(cadParamChanges(newParams, draft.fields).changes, pending);
  }
});

test("variant sends an explicit recipe and numerical edits to creation while the source draft stays pending", async (t) => {
  const info = { id: 7, spec: { name: "Caja original", params: { width: 40, height: "$width / 2" }, root: { type: "box", size: ["$width", 10, "$height"] } }, params: [{ name: "width", value: 40 }, { name: "height", value: 20 }] };
  const original = structuredClone(info);
  const draft = changeDraft(draftState(cadDraftValues(info.params)), { width: "60", height: "20.0" });
  const pending = structuredClone(draft);
  const request = cadVariantRequest(info, draft.fields, "  Variante ñ  ");
  assert.deepEqual(request, { body: { spec: original.spec, name: "Variante ñ", params: { width: 60 }, save: true }, error: "" });
  assert.equal(cadVariantRequest(info, { ...draft.fields, width: "" }, "Nombre").body, null);
  const oldWindow = globalThis.window, oldFetch = globalThis.fetch, calls = [];
  t.after(() => { globalThis.window = oldWindow; globalThis.fetch = oldFetch; });
  globalThis.window = { location: { origin: "http://127.0.0.1:63354" } };
  globalThis.fetch = async (url, options) => {
    calls.push({ path: url.pathname, method: options.method, body: JSON.parse(options.body) });
    return { ok: true, text: async () => JSON.stringify({ id: url.pathname.endsWith("from-text") ? 91 : 7 }) };
  };
  assert.equal((await api.cadCreateVariant(request.body)).id, 91);
  assert.equal((await api.cadRegenerate(7, { width: 60 })).id, 7);
  assert.deepEqual(calls, [
    { path: "/api/cad/from-text", method: "POST", body: request.body },
    { path: "/api/cad/7/regenerate", method: "POST", body: { params: { width: 60 } } },
  ]);
  assert.deepEqual(info, original);
  assert.deepEqual(draft, pending);
  assert.deepEqual(cadParamChanges(info.params, draft.fields).changes, { width: 60 });
});
