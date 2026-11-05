import { $, $$, el, clear, toast, initPointerFx, observeReveals, reduceMotion } from "./util.js";
import { api, ApiError } from "./api.js";
import { initTheme, snap, tweenTo, setProgress } from "./theme.js";
import { initLanding, setLandingVisible } from "./scenes.js";
import { renderOverview } from "./overview.js";
import { renderSubscriptions } from "./subscriptions.js";
import { renderFindings } from "./findings.js";
import { renderAsk } from "./ask.js";

export const state = { analysis: null, health: null, dirty: new Set(), view: "landing" };
const APP_VIEWS = { overview: renderOverview, subscriptions: renderSubscriptions, findings: renderFindings, ask: renderAsk };
const VIEW_MOOD = { subscriptions: "midnight", findings: "ember", ask: "dusk" };
let pendingFile = null;

// ---------------------------------------------------------------------------------------------------------- routing
export const goto = (name) => { location.hash = name === "landing" ? "#/" : `#/${name}`; };

function routeName() {
  const m = location.hash.match(/^#\/([a-z]+)/);
  return m && APP_VIEWS[m[1]] ? m[1] : "landing";
}

async function route() {
  let name = routeName();
  if (name !== "landing" && !state.analysis) {
    try { setAnalysis(await api.analysis()); }
    catch { name = "landing"; if (location.hash.startsWith("#/")) { history.replaceState(null, "", "#/"); toast("Upload a statement first, or try the sample data."); } }
  }
  showView(name);
}

function showView(name) {
  const prev = state.view;
  state.view = name;
  $("#view-landing").hidden = name !== "landing";
  Object.keys(APP_VIEWS).forEach((v) => { $(`#view-${v}`).hidden = v !== name; });
  setLandingVisible(name === "landing");
  document.body.dataset.view = name;
  $$("[data-route]").forEach((a) => (a.dataset.route === name ? a.setAttribute("aria-current", "page") : a.removeAttribute("aria-current")));
  const hasAnalysis = !!state.analysis;
  $("#tabs").hidden = !hasAnalysis;
  $("#tabbar").hidden = !hasAnalysis;
  $("#btn-new").hidden = !hasAnalysis || name === "landing";
  if (name === "landing") {
    snap("midnight");
    if (prev !== "landing") scrollTo(0, 0);
    updateResume();
    return;
  }
  const root = $(`#view-${name}`);
  if (!root.dataset.rendered || state.dirty.has(name)) {
    clear(root);
    APP_VIEWS[name](root, state, { goto, refresh });
    root.dataset.rendered = "1";
    state.dirty.delete(name);
  }
  root.classList.remove("entering"); void root.offsetWidth; root.classList.add("entering");
  if (VIEW_MOOD[name]) tweenTo(VIEW_MOOD[name]);
  scrollTo(0, 0);
  observeReveals(root);
  root.querySelector("h1")?.setAttribute("tabindex", "-1");
  root.querySelector("h1")?.focus({ preventScroll: true });
}

/** Called by views after they change data so that other views redraw next time they are opened. */
export function refresh(payload) {
  if (payload?.summary) state.analysis.summary = payload.summary;
  if (payload?.findings) state.analysis.findings = payload.findings;
  updateBadges();
  ["overview", "subscriptions", "findings"].forEach((v) => { if (v !== state.view) state.dirty.add(v); });
}

function setAnalysis(a) {
  state.analysis = a;
  $$(".view.app").forEach((v) => { clear(v); delete v.dataset.rendered; });
  updateBadges();
}

function updateBadges() {
  const a = state.analysis;
  const src = $("#badge-source"), ai = $("#badge-ai"), count = $("#count-findings");
  if (!a) { src.hidden = ai.hidden = count.hidden = true; return; }
  src.hidden = false;
  src.textContent = a.source.kind === "demo" ? "Sample statement" : (a.source.filename || "Your statement");
  if (state.health) { ai.hidden = false; ai.textContent = state.health.ai.mode === "model" ? `AI: ${state.health.ai.model}` : "AI: built-in text"; ai.title = state.health.ai.mode === "model" ? "Explanations and drafts come from Claude, checked against your numbers." : "No API key set, so explanations use built-in wording. Add ANTHROPIC_API_KEY to use Claude."; }
  const open = a.findings.filter((f) => f.status === "open").length;
  count.hidden = open === 0; count.textContent = String(open);
  count.setAttribute("aria-label", `${open} open findings`);
  $("#tabs").hidden = false;
}

function updateResume() {
  $(".resume")?.remove();
  if (!state.analysis) return;
  const a = state.analysis;
  const bar = el("div", { class: "resume banner" },
    el("span", {}, `Your ${a.source.kind === "demo" ? "sample" : ""} analysis is still open: ${a.subscriptions.length} subscriptions, ${a.findings.filter((f) => f.status === "open").length} open findings.`.replace("Your  ", "Your ")),
    el("button", { class: "btn btn-sm btn-primary", type: "button", on: { click: () => goto("overview") } }, "Continue"));
  $(".hero-inner").insertBefore(bar, $(".drop"));
}

// ---------------------------------------------------------------------------------------------------------- upload
const drop = $("#drop"), fileInput = $("#file");
let busyTimer = 0;

function setBusy(on, title, sub) {
  $("#drop-idle").hidden = on; $("#drop-busy").hidden = !on; $("#drop-error").hidden = true;
  drop.setAttribute("aria-busy", String(on));
  clearInterval(busyTimer);
  if (on) {
    $("#busy-title").textContent = title; $("#busy-sub").textContent = sub;
    const steps = ["Cleaning up merchant names…", "Spotting the rhythm of repeat charges…", "Checking for duplicates and price rises…", "Comparing with PayPal…"];
    let i = 0;
    busyTimer = setInterval(() => { $("#busy-sub").textContent = steps[i++ % steps.length]; }, 1100);
  }
}
function dropError(message) {
  const box = $("#drop-error");
  box.textContent = message; box.hidden = false;
  box.scrollIntoView({ block: "nearest", behavior: reduceMotion() ? "auto" : "smooth" });
}

async function submit({ file = null, demo = false, mapping = null, invert = false } = {}) {
  setBusy(true, demo ? "Opening the sample statement…" : "Reading your statement…", "Cleaning up merchant names…");
  const fd = new FormData();
  if (demo) fd.set("demo", "true"); else fd.set("file", file, file.name);
  if (mapping) fd.set("mapping", JSON.stringify(mapping));
  if (invert) fd.set("invert_sign", "true");
  try {
    const [data] = await Promise.all([api.analyze(fd), new Promise((r) => setTimeout(r, reduceMotion() ? 0 : 1400))]);   // a beat, so the progress isn't a flash
    setAnalysis(data);
    pendingFile = null;
    setBusy(false);
    goto("overview");
  } catch (e) {
    setBusy(false);
    if (e instanceof ApiError && e.code === "needs_mapping") { pendingFile = file; openMapper(e.message, e.details); return; }
    dropError(e instanceof ApiError ? e.message : "Something went wrong reading that file. Please try again.");
  }
}

const HOSTED_NOTE = "Uploads are switched off on this hosted demo, so nothing of yours is sent anywhere. Try the sample statement, or run SubSweep on your own computer to analyse your own statement.";

/** The hosted demo (a serverless build) takes the sample statement only: say so plainly instead of promising things that are not true there. */
function applyHostedMode(h) {
  if (!h?.hosted_demo) return;
  document.documentElement.dataset.hosted = "demo";
  fileInput.disabled = true;
  $("#btn-choose").hidden = true;
  $("#drop-idle .drop-title").textContent = "Try the sample statement";
  $("#drop-hint").textContent = "This hosted demo runs on a made-up statement. Uploads are switched off here.";
  $(".formats").hidden = true;
  $(".privacy-line span").replaceChildren(
    el("strong", {}, "This is a hosted demo of a made-up statement."),
    " Uploads are switched off here, so nothing of yours is sent anywhere. To analyse your own statement, run SubSweep on your own computer (see the README), where it stays.");
}

function pick(files) {
  const f = files?.[0];
  if (!f) return;
  if (state.health?.hosted_demo) { dropError(HOSTED_NOTE); return; }
  if (!/\.(csv|tsv|txt)$/i.test(f.name)) { dropError("That doesn't look like a statement export. Please choose a .csv file."); return; }
  submit({ file: f });
}

$("#btn-choose").addEventListener("click", () => fileInput.click());
$("#btn-demo").addEventListener("click", () => submit({ demo: true }));
fileInput.addEventListener("change", () => { pick(fileInput.files); fileInput.value = ""; });
["dragenter", "dragover"].forEach((ev) => drop.addEventListener(ev, (e) => { e.preventDefault(); drop.classList.add("over"); }));
["dragleave", "drop"].forEach((ev) => drop.addEventListener(ev, (e) => { e.preventDefault(); drop.classList.remove("over"); }));
drop.addEventListener("drop", (e) => pick(e.dataTransfer?.files));
["dragover", "drop"].forEach((ev) => addEventListener(ev, (e) => e.preventDefault()));       // never let the browser open a dropped file
$("#cta-top").addEventListener("click", (e) => {
  e.preventDefault();
  $("#drop").scrollIntoView({ behavior: reduceMotion() ? "auto" : "smooth", block: "center" });
  setTimeout(() => (state.health?.hosted_demo ? $("#btn-demo") : $("#btn-choose")).focus({ preventScroll: true }), 600);
});

// ---------------------------------------------------------------------------------------------------------- column mapper
const mapper = $("#mapper");
function openMapper(message, { headers = [], suggestions = {} }) {
  $("#mapper-msg").textContent = message;
  const grid = clear($("#mapper-grid"));
  const fields = [["date", "Date", true], ["description", "Description", true], ["amount", "Amount (one column)", false],
                  ["debit", "…or Debit column", false], ["credit", "…and Credit column", false], ["currency", "Currency (optional)", false]];
  for (const [key, label, req] of fields) {
    const id = `map-${key}`;
    const sel = el("select", { class: "field", id, name: key, required: req }, el("option", { value: "" }, req ? "Choose a column…" : "(none)"),
      headers.filter(Boolean).map((h) => el("option", { value: h, selected: suggestions[key] === h }, h)));
    grid.append(el("label", { for: id }, label + (req ? " *" : "")), sel);
  }
  $("#map-invert").checked = false;
  mapper.showModal();
}
$("#mapper-cancel").addEventListener("click", () => mapper.close());
$("#mapper-form").addEventListener("submit", (e) => {
  e.preventDefault();
  const v = (k) => $(`#map-${k}`).value || null;
  const mapping = { date: v("date"), description: v("description"), amount: v("amount"), debit: v("debit"), credit: v("credit"), currency: v("currency") };
  if (!mapping.date || !mapping.description || !(mapping.amount || mapping.debit || mapping.credit)) {
    toast("Please choose a date, a description, and an amount (or debit/credit) column."); return;
  }
  mapper.close();
  if (pendingFile) submit({ file: pendingFile, mapping, invert: $("#map-invert").checked });
});

// ---------------------------------------------------------------------------------------------------------- boot
$("#btn-new").addEventListener("click", () => goto("landing"));
addEventListener("hashchange", route);
addEventListener("scroll", setProgress, { passive: true });
addEventListener("unhandledrejection", () => toast("Something unexpected happened. Your data is safe; please try again."));

initTheme();
initPointerFx();
initLanding();
setProgress();
api.health().then((h) => { state.health = h; updateBadges(); applyHostedMode(h); }).catch(() => {});
route();
