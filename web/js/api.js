// Thin fetch wrapper. Every server error has the shape { error: { code, message, details } } with friendly copy.
export class ApiError extends Error {
  constructor(status, code, message, details = {}) { super(message); this.status = status; this.code = code; this.details = details; }
}

async function call(path, opts = {}) {
  let res;
  try { res = await fetch(path, { credentials: "same-origin", ...opts }); }
  catch { throw new ApiError(0, "offline", "We couldn't reach SubSweep. Is it still running? Check the terminal and try again."); }
  let body = null;
  try { body = await res.json(); } catch { /* non-JSON */ }
  if (!res.ok) {
    const e = body?.error;
    throw new ApiError(res.status, e?.code || "error", e?.message || "Something went wrong. Please try again.", e?.details || {});
  }
  return body;
}
const post = (path, body) => call(path, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body ?? {}) });

export const api = {
  health: () => call("/api/health"),
  analysis: () => call("/api/analysis"),
  analyze: (formData) => call("/api/analyze", { method: "POST", body: formData }),
  forget: () => call("/api/analysis", { method: "DELETE" }),
  history: (id) => call(`/api/subscriptions/${encodeURIComponent(id)}/history`),
  dismiss: (id, reason) => post(`/api/findings/${encodeURIComponent(id)}/dismiss`, { reason }),
  restore: (id) => post(`/api/findings/${encodeURIComponent(id)}/restore`),
  explain: (id) => post(`/api/findings/${encodeURIComponent(id)}/explain`),
  draft: (id) => post(`/api/findings/${encodeURIComponent(id)}/draft`),
  ask: (question) => post("/api/ask", { question }),
};
