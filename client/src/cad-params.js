export const cadDraftValues = (params) => Object.fromEntries(params.map((param) => [param.name, String(param.value ?? "")]));

// Send numerical edits only: resending a derived value would replace its expression.
export function cadParamChanges(params, fields) {
  const changes = {};
  for (const param of params) {
    const text = fields[param.name] ?? String(param.value ?? "");
    if (text === String(param.value ?? "")) continue;
    if (!String(text).trim() || !Number.isFinite(Number(text))) return { changes: {}, error: `${param.name}: introduce un número finito.` };
    if (Number(text) !== param.value) changes[param.name] = Number(text);
  }
  return { changes, error: "" };
}
