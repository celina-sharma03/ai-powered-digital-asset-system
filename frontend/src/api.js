// Thin wrapper around the backend REST API.

async function request(path, options = {}) {
  const res = await fetch(path, options);
  if (!res.ok) {
    const body = await res.json().catch(() => ({}));
    throw new Error(body.detail || `${res.status} ${res.statusText}`);
  }
  return res.json();
}

export function search(query, filters) {
  const params = new URLSearchParams({ q: query, mode: filters.mode, limit: "36" });
  filters.types.forEach((t) => params.append("type", t));
  if (filters.folder) params.set("folder", filters.folder);
  if (filters.size) {
    const [min, max] = filters.size.split("-");
    if (min) params.set("min_size", min);
    if (max) params.set("max_size", max);
  }
  if (filters.duration) {
    const [min, max] = filters.duration.split("-");
    if (min) params.set("min_duration", min);
    if (max) params.set("max_duration", max);
  }
  return request(`/api/search?${params}`);
}

export const getStats = () => request("/api/stats");
export const getAsset = (id) => request(`/api/assets/${id}`);
export const revealFile = (id) => request(`/api/assets/${id}/reveal`, { method: "POST" });

export const getIndexStatus = () => request("/api/index/status");
export const getIndexIssues = () => request("/api/index/issues");
export const cancelIndex = () => request("/api/index/cancel", { method: "POST" });
export const startIndex = (full = false) =>
  request("/api/index", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ full }),
  });
