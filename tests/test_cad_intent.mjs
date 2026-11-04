import test from "node:test";
import assert from "node:assert/strict";
import { cadUsage, cadUsageText } from "../client/src/cad-intent.js";

test("direct, derived and unused dimensions read differently", () => {
  const spacing = {
    name: "hole_spacing",
    used_by: [{ node: "root.cuts[0]", type: "linear_pattern", label: "agujeros de la base", field: "root.cuts[0].step[0]" }],
    indirect: [], drives: [],
  };
  assert.equal(cadUsageText(spacing), "Afecta a: agujeros de la base (root.cuts[0].step[0]).");
  const width = {
    name: "width", used_by: [],
    indirect: [{ node: "root.base", type: "extrude", label: null, field: "root.base.height", via: "depth" }],
    drives: ["depth"],
  };
  assert.equal(cadUsageText(width), "También: extrusión de boceto (root.base.height) a través de depth. Define: depth.");
  assert.equal(cadUsage({ name: "spare", used_by: [], indirect: [] }).unused, true);
  assert.equal(cadUsageText({ name: "spare" }), "Ninguna operación de la receta usa este valor.");
});
