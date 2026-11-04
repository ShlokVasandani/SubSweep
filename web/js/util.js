// Small helpers. Data from the server is ALWAYS inserted with textContent (never innerHTML), so user-supplied text such as
// merchant names or filenames can't inject markup.
const SVG_NS = "http://www.w3.org/2000/svg";
export const $ = (sel, root = document) => root.querySelector(sel);
export const $$ = (sel, root = document) => [...root.querySelectorAll(sel)];
export const reduceMotion = () => matchMedia("(prefers-reduced-motion: reduce)").matches;
export const clamp = (v, a = 0, b = 1) => Math.min(b, Math.max(a, v));
export const lerp = (a, b, t) => a + (b - a) * t;
export const ease = (t) => 1 - Math.pow(1 - clamp(t), 3);
export const smooth = (t) => { t = clamp(t); return t * t * (3 - 2 * t); };

/** el("div", { class: "x", dataset: {a: 1}, on: { click: fn }, aria: { label: "x" } }, "text", childNode, ...) */
export function el(tag, attrs = {}, ...kids) {
  const n = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs || {})) {
    if (v == null || v === false) continue;
    if (k === "class") n.className = v;
    else if (k === "dataset") Object.assign(n.dataset, v);
    else if (k === "aria") for (const [a, val] of Object.entries(v)) n.setAttribute("aria-" + a, val);
    else if (k === "on") for (const [ev, fn] of Object.entries(v)) n.addEventListener(ev, fn);
    else if (k === "style" && typeof v === "object") for (const [p, val] of Object.entries(v)) n.style.setProperty(p.startsWith("--") ? p : p.replace(/[A-Z]/g, (m) => "-" + m.toLowerCase()), val);
    else if (k === "text") n.textContent = v;
    else n.setAttribute(k, v === true ? "" : v);
  }
  append(n, kids);
  return n;
}
export function append(parent, kids) {
  for (const k of kids.flat(Infinity)) {
    if (k == null || k === false) continue;
    parent.append(k instanceof Node ? k : document.createTextNode(String(k)));
  }
  return parent;
}
export const clear = (n) => { n.replaceChildren(); return n; };
export function icon(name, cls = "") {
  const s = document.createElementNS(SVG_NS, "svg");
  s.setAttribute("class", ("icon " + cls).trim()); s.setAttribute("aria-hidden", "true"); s.setAttribute("focusable", "false");
  const u = document.createElementNS(SVG_NS, "use"); u.setAttribute("href", "#i-" + name); s.append(u);
  return s;
}

// ---------- formatting ----------
const fmtCache = new Map();
export function money(v, cur = "USD", frac = 2) {
  const key = cur + frac;
  if (!fmtCache.has(key)) fmtCache.set(key, new Intl.NumberFormat("en-US", { style: "currency", currency: cur, minimumFractionDigits: frac, maximumFractionDigits: frac }));
  return fmtCache.get(key).format(v);
}
const MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];
export function fdate(iso) {
  if (!iso) return "—";
  const d = new Date(String(iso).length <= 10 ? iso + "T00:00:00" : iso);
  return Number.isNaN(+d) ? "—" : `${d.getDate()} ${MONTHS[d.getMonth()]} ${d.getFullYear()}`;
}
export const CYCLE = { weekly: "week", monthly: "month", quarterly: "quarter", yearly: "year" };
export const TYPE_LABEL = { DUPLICATE: "Duplicate", PRICE_HIKE: "Price hike", POSSIBLY_FORGOTTEN: "Possibly forgotten", ZOMBIE: "Still charging",
  ANNUAL_RENEWAL_SOON: "Renewal soon", PAYPAL_MISMATCH: "PayPal mismatch" };
export const TYPE_ICON = { DUPLICATE: "copy", PRICE_HIKE: "warn", POSSIBLY_FORGOTTEN: "search", ZOMBIE: "warn", ANNUAL_RENEWAL_SOON: "flag", PAYPAL_MISMATCH: "shield" };
export const SEV_LABEL = { high: "Needs a look", medium: "Worth checking", low: "Keep an eye on" };

// ---------- toasts ----------
export function toast(message, { action, onAction, ms = 6000 } = {}) {
  const host = $("#toasts");
  const t = el("div", { class: "toast glass" }, el("span", {}, message));
  const close = () => { t.classList.add("out"); setTimeout(() => t.remove(), 300); };
  if (action) t.append(el("button", { class: "btn btn-sm", type: "button", on: { click: () => { onAction?.(); close(); } } }, action));
  host.append(t);
  setTimeout(close, ms);
  return close;
}

// ---------- count up ----------
export function countUp(node, to, { format = (v) => String(Math.round(v)), ms = 1600, from = 0 } = {}) {
  if (reduceMotion()) { node.textContent = format(to); return; }
  const t0 = performance.now();
  const step = (now) => {
    const t = clamp((now - t0) / ms);
    node.textContent = format(lerp(from, to, ease(t)));
    if (t < 1) requestAnimationFrame(step);
  };
  requestAnimationFrame(step);
}

// ---------- reveal on scroll ----------
let revealObs;
export function observeReveals(root = document) {
  if (reduceMotion() || !("IntersectionObserver" in window)) { $$(".reveal", root).forEach((n) => n.classList.add("in")); return; }
  revealObs ??= new IntersectionObserver((entries) => entries.forEach((e) => { if (e.isIntersecting) { e.target.classList.add("in"); revealObs.unobserve(e.target); } }), { threshold: 0.14, rootMargin: "0px 0px -6% 0px" });
  $$(".reveal:not(.in)", root).forEach((n) => revealObs.observe(n));
}

// ---------- pointer glow on glass + tilt ----------
export function initPointerFx() {
  let raf = 0, last = null;
  addEventListener("pointermove", (e) => {
    last = e;
    if (raf) return;
    raf = requestAnimationFrame(() => {
      raf = 0;
      const g = last.target.closest?.(".glass");
      if (g) { const r = g.getBoundingClientRect(); g.style.setProperty("--mx", `${last.clientX - r.left}px`); g.style.setProperty("--my", `${last.clientY - r.top}px`); }
      if (reduceMotion()) return;
      $$("[data-tilt]").forEach((t) => {
        const r = t.getBoundingClientRect();
        const inside = last.clientX > r.left - 80 && last.clientX < r.right + 80 && last.clientY > r.top - 80 && last.clientY < r.bottom + 80;
        t.style.setProperty("--ry", inside ? `${(((last.clientX - r.left) / r.width) - .5) * 7}deg` : "0deg");
        t.style.setProperty("--rx", inside ? `${-(((last.clientY - r.top) / r.height) - .5) * 6}deg` : "0deg");
      });
    });
  }, { passive: true });
}

export function copyText(text) {
  if (navigator.clipboard?.writeText) return navigator.clipboard.writeText(text);
  const ta = el("textarea", { style: { position: "fixed", opacity: "0" } });
  ta.value = text; document.body.append(ta); ta.select();
  try { document.execCommand("copy"); } finally { ta.remove(); }
  return Promise.resolve();
}
