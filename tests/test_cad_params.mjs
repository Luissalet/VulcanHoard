import test from "node:test";
import assert from "node:assert/strict";
import { cadDraftValues, cadParamChanges } from "../client/src/cad-params.js";
import { draftState, changeDraft, acknowledgeDraft, refreshDraft } from "../client/src/drafts.js";

test("only the edited dimension is sent, derived values refresh and later edits stay pending", () => {
  const params = [{ name: "width", value: 40 }, { name: "height", value: 20, expression: "$width / 2" }, { name: "offset", value: 0 }];
  const fields = { ...cadDraftValues(params), width: "60", height: "20.0" };
  assert.deepEqual(cadParamChanges(params, fields), { changes: { width: 60 }, error: "" });
  assert.deepEqual(cadParamChanges(params, { ...fields, height: "25" }).changes, { width: 60, height: 25 });
  assert.match(cadParamChanges(params, { ...fields, width: "" }).error, /width/);
  let draft = changeDraft(draftState(cadDraftValues(params)), { width: "60" });
  draft = changeDraft(draft, { width: "70" });
  const saved = { width: "60", height: "30", offset: "0" };
  draft = refreshDraft(acknowledgeDraft(draft, { width: "60" }, saved), saved);
  assert.deepEqual(draft.fields, { width: "70", height: "30", offset: "0" });
  assert.deepEqual(draft.dirty, ["width"]);
});
