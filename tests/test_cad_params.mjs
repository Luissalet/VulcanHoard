import test from "node:test";
import assert from "node:assert/strict";
import { cadAcknowledgedFields, cadDraftValues, cadParamChanges } from "../client/src/cad-params.js";
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
