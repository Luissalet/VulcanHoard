// Thin fetch wrapper: JSON in/out. `{ error, code, hint }` bodies become exceptions that keep those fields.
async function request(method, path, { params, body } = {}) {
  const url = new URL(path, window.location.origin);
  for (const [key, value] of Object.entries(params || {})) {
    if (value !== undefined && value !== null && value !== "") url.searchParams.set(key, value);
  }
  let response;
  try {
    response = await fetch(url, {
      method,
      headers: body !== undefined ? { "Content-Type": "application/json" } : undefined,
      body: body !== undefined ? JSON.stringify(body) : undefined,
    });
  } catch (cause) {
    const error = new Error(cause && cause.message ? cause.message : "Network error");
    error.code = "network";
    throw error;
  }
  const text = await response.text();
  let data = null;
  try {
    data = text ? JSON.parse(text) : null;
  } catch {
    data = { error: text.slice(0, 300) };
  }
  if (!response.ok) {
    const detail = data && (data.error || data.detail);
    const error = new Error((typeof detail === "string" && detail) || `Error ${response.status}`);
    error.code = (data && data.code) || "";
    error.hint = (data && data.hint) || "";
    error.issues = (data && data.issues) || [];
    error.status = response.status;
    throw error;
  }
  return data;
}

// Every write and most reads go through the same tool handlers the assistant uses.
const call = (name, args) => request("POST", "/api/ui/call", { body: { name, arguments: args || {} } });

export const fileUrl = (modelId, name, { download = false, build = "", kept = "" } = {}) => {
  const q = new URLSearchParams();
  if (download) q.set("download", "1");
  if (build) q.set("b", build);
  if (kept) q.set("build", kept);   // a kept earlier build instead of the current one
  const qs = q.toString();
  return `/api/models/${encodeURIComponent(modelId)}/files/${name.split("/").map(encodeURIComponent).join("/")}${qs ? `?${qs}` : ""}`;
};

export const api = {
  health: () => request("GET", "/api/health"),
  events: (after) => request("GET", "/api/events", { params: { after } }),
  call,
};
