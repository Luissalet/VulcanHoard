// Thin fetch wrapper: JSON in/out, `{ error }` bodies become exceptions.
async function request(method, path, { params, body } = {}) {
  const url = new URL(path, window.location.origin);
  for (const [key, value] of Object.entries(params || {})) {
    if (value !== undefined && value !== null && value !== "" && value !== false) url.searchParams.set(key, value);
  }
  const response = await fetch(url, {
    method,
    headers: body !== undefined ? { "Content-Type": "application/json" } : undefined,
    body: body !== undefined ? JSON.stringify(body) : undefined,
  });
  const text = await response.text();
  let data = null;
  try {
    data = text ? JSON.parse(text) : null;
  } catch {
    data = { error: text };
  }
  if (!response.ok) throw new Error((data && data.error) || `Error ${response.status}`);
  return data;
}

export const api = {
  status: () => request("GET", "/api/status"),
  stats: () => request("GET", "/api/stats"),
  roots: () => request("GET", "/api/roots"),
  addRoot: (body) => request("POST", "/api/roots", { body }),
  updateRoot: (id, patch) => request("PATCH", `/api/roots/${id}`, { body: patch }),
  removeRoot: (id) => request("DELETE", `/api/roots/${id}`),
  rescan: (id) => request("POST", `/api/roots/${id}/rescan`),
  models: (params) => request("GET", "/api/models", { params }),
  facets: () => request("GET", "/api/models/facets"),
  model: (id) => request("GET", `/api/models/${id}`),
  updateModel: (id, patch) => request("PATCH", `/api/models/${id}`, { body: patch }),
  listing: (id) => request("GET", `/api/models/${id}/listing`),
  saveListing: (id, body) => request("PUT", `/api/models/${id}/listing`, { body }),
  removeListing: (id) => request("DELETE", `/api/models/${id}/listing`),
  dupes: (kind) => request("GET", "/api/dupes", { params: { kind } }),
  collections: () => request("GET", "/api/collections"),
  createAlbum: (body) => request("POST", "/api/collections", { body }),
  updateAlbum: (id, patch) => request("PATCH", `/api/collections/${id}`, { body: patch }),
  removeAlbum: (id) => request("DELETE", `/api/collections/${id}`),
  maintenance: (action) => request("POST", `/api/maintenance/${action}`),
  thumbUrl: (model) => (model.has_thumb ? `/api/models/${model.id}/thumb?v=${model.sha256?.slice(0, 8) || ""}` : null),
  fileUrl: (id) => `/api/models/${id}/file`,
};
