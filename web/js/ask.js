import { el, icon, clear } from "./util.js";
import { api } from "./api.js";

const SUGGESTIONS = ["What do I spend on streaming?", "Which subscription went up the most this year?", "How much could I save?", "What renews in the next 30 days?",
  "What is my most expensive subscription?", "Am I paying for anything twice?"];
const history = [];          // survives switching tabs within a session

function table(columns, rows) {
  if (!rows.length) return null;
  const t = el("table", { class: "mini" }, el("caption", { class: "vh" }, "Rows the answer is based on"),
    el("tr", {}, columns.map((c) => el("th", { scope: "col" }, c.replaceAll("_", " ")))));
  rows.forEach((r) => t.append(el("tr", {}, columns.map((c) => el("td", {}, r[c] == null ? "" : String(r[c]))))));
  return t;
}

function botMessage(r) {
  const refused = r.status === "refused" || r.status === "unsupported" || r.status === "no_data";
  const meta = el("div", { class: "meta-row" });
  if (r.status === "answered") meta.append(el("span", { class: "chip" + (r.generated_by === "model" ? " good" : "") }, r.generated_by === "model" ? "Claude · checked against your numbers" : "Built-in answer"));
  if (r.status === "no_data") meta.append(el("span", { class: "chip" }, "Nothing to report"));
  if (r.status === "refused") meta.append(el("span", { class: "chip" }, "Out of scope"));
  const m = el("div", { class: "msg bot glass" }, el("p", { class: "answer" + (refused ? " refused" : ""), style: { whiteSpace: "pre-wrap" } }, r.answer), meta);
  if (r.notes?.length && r.status === "answered" && r.generated_by !== "model") meta.append(el("span", { class: "chip" }, "AI is off, so this is the built-in answer"));
  if (r.status === "answered") {
    m.append(el("details", { class: "disclosure" }, el("summary", {}, "Show data used"),
      el("p", { style: { margin: ".4em 0 0", color: "var(--ink-3)" } }, "The computed result this answer is based on, and exactly what was sent to the model (personal details removed)."),
      table(r.columns, r.table), el("pre", { class: "data" }, JSON.stringify(r.data_used, null, 2))));
  }
  return m;
}

export function renderAsk(root, state) {
  root.append(el("div", { class: "page-head" }, el("p", { class: "eyebrow" }, "Ask"), el("h1", { id: "ask-title" }, "Ask about your money"),
    el("p", {}, "Questions about the subscriptions and spending in your statement. We work the answer out first, then explain it. We won't guess, and we can't give investment advice.")));
  const log = el("div", { class: "chat", role: "log", "aria-live": "polite", "aria-label": "Conversation" });
  const input = el("textarea", { rows: "1", maxlength: "500", placeholder: "Ask something, like “What do I spend on streaming?”", "aria-label": "Your question" });
  const send = el("button", { class: "btn btn-primary", type: "submit", "aria-label": "Send question" }, icon("send"), el("span", { class: "vh" }, "Send"));
  const count = el("span", { class: "chip count", "aria-hidden": "true" }, "0/500");
  const form = el("form", { class: "composer glass", on: { submit: (e) => { e.preventDefault(); ask(input.value); } } }, input, count, send);
  const chips = el("div", { class: "suggest", role: "group", "aria-label": "Suggested questions" }, SUGGESTIONS.map((s) => el("button", { class: "btn btn-sm", type: "button", on: { click: () => ask(s) } }, s)));
  root.append(el("div", { class: "ask-wrap" }, chips, log, form));

  history.forEach((h) => log.append(h.type === "user" ? el("div", { class: "msg user" }, h.text) : botMessage(h.r)));
  input.addEventListener("input", () => { count.textContent = `${input.value.length}/500`; input.style.height = "auto"; input.style.height = Math.min(160, input.scrollHeight) + "px"; });
  input.addEventListener("keydown", (e) => { if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); form.requestSubmit(); } });

  let busy = false;
  async function ask(q) {
    q = (q || "").trim();
    if (!q || busy) return;
    busy = true; send.disabled = true;
    input.value = ""; input.style.height = "auto"; count.textContent = "0/500";
    log.append(el("div", { class: "msg user" }, q)); history.push({ type: "user", text: q });
    const wait = el("div", { class: "msg bot glass" }, el("span", { class: "thinking", role: "status" }, el("i"), el("i"), el("i"), el("span", { class: "vh" }, "Looking at your numbers"), el("span", { "aria-hidden": "true" }, " Looking at your numbers…")));
    log.append(wait); wait.scrollIntoView({ block: "center", behavior: "smooth" });
    try {
      const r = await api.ask(q);
      wait.replaceWith(botMessage(r)); history.push({ type: "bot", r });
    } catch (e) {
      const r = { status: "unsupported", answer: e.message, columns: [], table: [], data_used: {}, notes: [] };
      wait.replaceWith(botMessage(r));
    } finally {
      busy = false; send.disabled = false; input.focus({ preventScroll: true });
      log.lastElementChild?.scrollIntoView({ block: "nearest", behavior: "smooth" });
    }
  }
}
