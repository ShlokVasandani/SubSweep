import { el, icon, money, fdate, toast, copyText, clear, TYPE_LABEL, TYPE_ICON, SEV_LABEL } from "./util.js";
import { api } from "./api.js";

const explainCache = new Map();       // finding id -> AI result (the server caches too; this avoids flicker on re-render)
const SEV_ORDER = ["high", "medium", "low"];

// A tiny queue so a page of findings doesn't fire a dozen model calls at once.
const queue = [];
let running = 0;
function enqueue(task) { queue.push(task); pump(); }
function pump() {
  while (running < 2 && queue.length) {
    const t = queue.shift(); running++;
    t().finally(() => { running--; pump(); });
  }
}

function dataUsed(data) {
  return el("details", { class: "disclosure" }, el("summary", {}, "Show data used"),
    el("p", { style: { margin: ".4em 0 0", color: "var(--ink-3)" } }, "Exactly what was sent to the model, after personal details were removed. Nothing else leaves your computer."),
    el("pre", { class: "data" }, JSON.stringify(data, null, 2)));
}

function aiBlock(f) {
  const text = el("p", { class: "ai-text" }), top = el("div", { class: "ai-top" }, icon("sparkle"), "In plain English");
  const holder = el("div", { class: "ai", "aria-live": "polite" }, top, text);
  function show(r) {
    clear(holder); top.replaceChildren(icon("sparkle"), "In plain English", el("span", { class: "chip" + (r.generated_by === "model" ? " good" : "") }, r.generated_by === "model" ? `Claude · checked against your numbers` : "Built-in wording"));
    text.textContent = r.text;
    holder.append(top, text);
    if (r.notes?.length && r.generated_by !== "model") holder.append(el("p", { class: "disclosure" }, r.notes[0]));
    holder.append(dataUsed(r.data_used));
  }
  function load() {
    if (explainCache.has(f.id)) return show(explainCache.get(f.id));
    clear(holder); holder.append(top, el("div", { class: "skeleton", style: { height: "1.1em", width: "92%" } }), el("div", { class: "skeleton", style: { height: "1.1em", width: "74%" } }));
    enqueue(() => api.explain(f.id).then((r) => { explainCache.set(f.id, r); show(r); }).catch((e) => {
      clear(holder);
      holder.append(top, el("p", { class: "ai-text" }, f.reason), el("div", { class: "banner" }, e.message, " ", el("button", { class: "btn btn-sm", type: "button", on: { click: load } }, "Try again")));
    }));
  }
  const io = new IntersectionObserver((es) => { if (es[0].isIntersecting) { io.disconnect(); load(); } }, { rootMargin: "400px 0px" });
  io.observe(holder);
  holder.append(top, el("p", { class: "ai-text" }, f.reason));
  return holder;
}

function draftBlock(f) {
  const wrap = el("div", { class: "draft-wrap" });
  const btn = el("button", { class: "btn btn-sm", type: "button", on: { click: open } }, icon("copy"), "Draft a message for me");
  wrap.append(btn);
  async function open() {
    btn.disabled = true; btn.textContent = "Writing a draft…";
    try {
      const d = await api.draft(f.id);
      clear(wrap);
      const subject = el("input", { class: "field", value: d.subject, "aria-label": "Message subject" });
      const body = el("textarea", { "aria-label": "Message body", spellcheck: "true" }); body.value = d.body;
      const copy = el("button", { class: "btn btn-primary btn-sm", type: "button", on: { click: async () => { await copyText(`Subject: ${subject.value}\n\n${body.value}`); copy.replaceChildren(icon("check"), "Copied"); setTimeout(() => copy.replaceChildren(icon("copy"), "Copy message"), 2200); } } }, icon("copy"), "Copy message");
      wrap.append(el("div", { class: "draft ai" },
        el("div", { class: "ai-top" }, icon("sparkle"), "Draft message", el("span", { class: "chip" + (d.generated_by === "model" ? " good" : "") }, d.generated_by === "model" ? "Written by Claude" : "Built-in template")),
        subject, el("div", { style: { height: "10px" } }), body,
        el("p", { class: "note" }, "SubSweep never sends this. Read it, fill in the [placeholders], and send it yourself from your own email or the merchant's help page."),
        el("div", { class: "fc-actions" }, copy), dataUsed(d.data_used)));
    } catch (e) {
      btn.disabled = false; btn.replaceChildren(icon("copy"), "Draft a message for me");
      toast(e.message);
    }
  }
  return wrap;
}

function card(f, act, dismissed) {
  const ev = f.evidence || {};
  const why = f.heuristics?.length ? el("ul", { class: "why", "aria-label": "Why we flagged this" }, f.heuristics.map((h) => el("li", {}, el("b", {}, `+${h.points}`), el("span", {}, h.text)))) : null;
  const actions = el("div", { class: "fc-actions" });
  if (dismissed) actions.append(el("button", { class: "btn btn-sm", type: "button", on: { click: () => act.restore(f) } }, icon("undo"), "Restore"));
  else {
    if (f.can_still_use) actions.append(el("button", { class: "btn btn-sm", type: "button", on: { click: () => act.decide(f, "still_using") } }, icon("check"), "Still using it"));
    actions.append(el("button", { class: "btn btn-sm btn-quiet", type: "button", on: { click: () => act.decide(f, "dismissed") } }, icon("x"), "Dismiss"));
    if (ev.manage_url) actions.append(el("a", { class: "btn btn-sm", href: ev.manage_url, target: "_blank", rel: "noopener noreferrer" }, "Manage in PayPal", icon("ext")));
  }
  const save = f.potential_savings_annual > 0 ? el("div", { class: "save" }, el("b", { class: "num" }, money(f.potential_savings_annual, f.currency, 0)), el("span", {}, "could be saved a year")) : null;
  return el("article", { class: "fc glass reveal" + (f.severity === "high" && !dismissed ? " alert" : "") + (dismissed ? " dismissed" : ""), aria: { labelledby: `f-${f.id}` } },
    el("div", { class: "fc-head" }, el("span", { class: "ico" }, icon(TYPE_ICON[f.type] || "flag")),
      el("div", {}, el("h3", { id: `f-${f.id}` }, f.title), el("div", { class: "chips" }, el("span", { class: "chip" + (f.severity === "high" ? " alert" : "") }, TYPE_LABEL[f.type]),
        f.is_guess ? el("span", { class: "chip", title: "Based on patterns in your statement, not on knowing whether you use it" }, "A guess") : null,
        dismissed ? el("span", { class: "chip" }, f.dismissed_reason === "still_using" ? "Marked as still using" : "Dismissed") : null)), save),
    dismissed ? el("p", { style: { color: "var(--ink-2)" } }, f.reason) : [aiBlock(f), why],
    dismissed ? null : (f.can_draft ? draftBlock(f) : null), actions);
}

export function renderFindings(root, state, ctx) {
  const a = state.analysis, S = a.summary;
  const open = a.findings.filter((f) => f.status === "open"), done = a.findings.filter((f) => f.status !== "open");

  async function decide(f, reason) {
    try {
      ctx.refresh(await api.dismiss(f.id, reason));
      rerender();
      toast(reason === "still_using" ? `Got it, ${f.merchant} stays. We won't flag it again.` : `Dismissed "${f.title}".`, { action: "Undo", onAction: () => restore(f) });
    } catch (e) { toast(e.message); }
  }
  async function restore(f) {
    try { ctx.refresh(await api.restore(f.id)); rerender(); } catch (e) { toast(e.message); }
  }
  function rerender() { clear(root); renderFindings(root, state, ctx); }
  const act = { decide, restore };

  root.append(el("div", { class: "page-head" }, el("p", { class: "eyebrow" }, "Findings"),
    el("h1", { id: "find-title" }, open.length ? `${open.length} thing${open.length === 1 ? "" : "s"} worth a look` : "Nothing needs your attention"),
    el("p", {}, open.length ? `Acting on all of them could save about ${money(S.potential_savings_annual, S.currency || "USD", 0)} a year. You decide what to do: SubSweep only explains and drafts.` : "No open findings. If you dismissed any earlier you can restore them below.")));

  if (!open.length) {
    root.append(el("div", { class: "empty glass panel" }, icon("shield", "art"), el("h3", {}, "All clear"),
      el("p", {}, a.findings.length ? "You've dealt with everything we found. Restore a dismissed finding below if you change your mind." : "We didn't find duplicates, price rises, or likely-forgotten subscriptions in this statement.")));
  }
  for (const sev of SEV_ORDER) {
    const group = open.filter((f) => f.severity === sev);
    if (!group.length) continue;
    root.append(el("section", { class: "sev", aria: { label: `${SEV_LABEL[sev]} findings` } },
      el("h2", {}, SEV_LABEL[sev], el("span", { class: "chip" + (sev === "high" ? " alert" : "") }, String(group.length))),
      el("div", { class: "fgrid" }, group.map((f) => card(f, act, false)))));
  }
  if (done.length) {
    root.append(el("details", { class: "dismissed-box disclosure" }, el("summary", {}, `Dismissed (${done.length})`),
      el("div", { class: "fgrid", style: { marginTop: "16px" } }, done.map((f) => card(f, act, true))),
      el("div", { style: { marginTop: "14px" } }, el("button", { class: "btn btn-sm btn-quiet", type: "button", on: { click: async () => { try { const r = await fetch("/api/dismissals", { method: "DELETE" }); ctx.refresh(await r.json()); rerender(); } catch (e) { toast("Couldn't reset your decisions. Please try again."); } } } }, "Restore all"))));
  }
}
