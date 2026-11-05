// Thin fetch wrapper. Every server error has the shape { error: { code, message, details } } with friendly copy.
export class ApiError extends Error {
  constructor(status, code, message, details = {}) { super(message); this.status = status; this.code = code; this.details = details; }
}

// Hosted (serverless) build: the server keeps nothing about a visitor, so this tab keeps the statement and re-sends it with every action.
// The server analyses it in memory and forgets it. In the normal local build none of this is used and the server holds the analysis.
let hosted = false;
let statement = null;           // { demo: true } or { file: File, options: { mapping, invert_sign, default_currency } }
export const hostedMode = {
  enable(on) { hosted = !!on; },
  remember(s) { statement = s; },
  forget() { statement = null; },
};

function statelessRequest(opts) {
  const fd = new FormData();
  if (statement.demo) fd.set("options", JSON.stringify({ demo: true }));
  else {
    fd.set("statement", statement.file, statement.file.name);
    fd.set("options", JSON.stringify({ ...statement.options, filename: statement.file.name }));
  }
  if (typeof opts.body === "string") fd.set("json", opts.body);                // the original JSON body travels inside the form
  return { method: "POST", credentials: "same-origin", body: fd,
    headers: { "X-SubSweep-Stateless": "1", "X-SubSweep-Method": (opts.method || "GET").toUpperCase() } };
}

async function call(path, opts = {}) {
  let res;
  const viaStatement = hosted && statement && path.startsWith("/api/") && !path.startsWith("/api/analyze") && path !== "/api/health";
  try { res = await fetch(path, viaStatement ? statelessRequest(opts) : { credentials: "same-origin", ...opts }); }
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
