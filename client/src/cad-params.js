export const cadDraftValues = (params) => Object.fromEntries(params.map((param) => [param.name, String(param.value ?? "")]));

// Equivalent formatting belongs to the submitted snapshot, even for a derived
// field omitted from the API payload. Real edits made while saving stay local.
export function cadAcknowledgedFields(snapshot, current) {
  return Object.fromEntries(Object.entries(snapshot).map(([name, value]) => [name,
    String(value).trim() && String(current[name] ?? "").trim() &&
    Number.isFinite(Number(value)) && Number(value) === Number(current[name])
      ? current[name] : value,
  ]));
}

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

export function cadVariantRequest(info, fields, name) {
  const { changes, error } = cadParamChanges(info.params, fields);
  return { body: error ? null : { spec: info.spec, name: name.trim() || null, params: changes, save: true }, error };
}
