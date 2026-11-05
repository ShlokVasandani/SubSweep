// Overview: a scroll-driven page. Each section carries a mood; the background tweens as you reach it.
import { $, $$, el, icon, money, fdate, countUp, observeReveals, reduceMotion, TYPE_LABEL, TYPE_ICON, CYCLE } from "./util.js";
import { api } from "./api.js";
import { tweenTo } from "./theme.js";
import { makeChart, dataTable, destroyIn } from "./charts.js";

let moodHandler = null;

function watchMoods(root) {
  if (moodHandler) removeEventListener("scroll", moodHandler);
  let last = "", ticking = false;
  const secs = $$("[data-mood]", root);
  moodHandler = () => {
    if (ticking || document.body.dataset.view !== "overview") return;
    ticking = true;
    requestAnimationFrame(() => {
      ticking = false;
      const mid = innerHeight * 0.5;
      let best = null, bestD = Infinity;
      for (const s of secs) { const r = s.getBoundingClientRect(); const d = r.top <= mid && r.bottom >= mid ? 0 : Math.min(Math.abs(r.top - mid), Math.abs(r.bottom - mid)); if (d < bestD) { bestD = d; best = s; } }
      if (best && best.dataset.mood !== last) { last = best.dataset.mood; tweenTo(last, 1400); }
    });
  };
  addEventListener("scroll", moodHandler, { passive: true });
  moodHandler();
}

function ring(score) {
  const NS = "http://www.w3.org/2000/svg", len = 2 * Math.PI * 100;
  const svg = document.createElementNS(NS, "svg"); svg.setAttribute("viewBox", "0 0 240 240"); svg.setAttribute("aria-hidden", "true");
  svg.innerHTML = `<defs><linearGradient id="ring-grad" x1="0" y1="0" x2="1" y2="1"><stop offset="0" stop-color="#9ec5ff"/><stop offset="1" stop-color="#82e6c8"/></linearGradient>
    <linearGradient id="ring-grad-low" x1="0" y1="0" x2="1" y2="1"><stop offset="0" stop-color="#ffb08a"/><stop offset="1" stop-color="#ff7a66"/></linearGradient></defs>
    <circle class="track" cx="120" cy="120" r="100"/><circle class="bar" cx="120" cy="120" r="100"/>`;
  const bar = svg.querySelector(".bar");
  if (score < 55) bar.style.stroke = "url(#ring-grad-low)";
  requestAnimationFrame(() => requestAnimationFrame(() => { bar.style.strokeDasharray = `${(len * score) / 100} 999`; }));
  if (reduceMotion()) bar.style.transition = "none";
  return svg;
}

function findingCard(f, goto) {
  return el("article", { class: "attn-card glass reveal" + (f.severity === "high" ? " alert" : ""), style: { "--i": "0" } },
    el("span", { class: "ico" }, icon(TYPE_ICON[f.type] || "flag")),
    el("div", {}, el("h3", {}, f.title), el("p", {}, f.reason)),
    f.potential_savings_annual > 0 ? el("div", { class: "save" }, el("b", {}, money(f.potential_savings_annual, f.currency, 0)), el("span", {}, "could be saved a year")) : el("div", { class: "save" }, el("span", {}, TYPE_LABEL[f.type])));
}

function paypalPanel(a) {
  const p = a.paypal, panel = el("section", { class: "panel glass reveal", style: { "--i": "1" }, aria: { labelledby: "pp-title" } });
  const badge = p.mode === "mock" ? el("span", { class: "chip demo-badge", title: p.notice || "" }, "Demo data") : p.mode === "live" ? el("span", { class: "chip good demo-badge" }, "Live sandbox") : null;
  panel.append(el("h3", { id: "pp-title" }, "PayPal status", badge),
    el("p", { class: "sub" }, "Live status for the subscriptions billed through PayPal. SubSweep only reads this; to change anything you go to PayPal yourself."));
  if (p.mode === "off") { panel.append(el("div", { class: "banner" }, icon("shield"), el("span", {}, p.notice || (document.documentElement.dataset.hosted ? "PayPal status isn't available for your own statement on this hosted build. Run SubSweep locally with PayPal sandbox credentials (see the README) to add it." : "PayPal isn't connected. Set PAYPAL_MODE=mock for demo data, or add sandbox credentials.")))); return panel; }
  if (p.mode === "mock") panel.append(el("div", { class: "banner" }, icon("sparkle"), el("span", {}, p.notice || "Showing bundled demo data. Nothing here comes from a live PayPal account.")));
  const mismatch = new Set(a.findings.filter((f) => f.status === "open" && f.type === "PAYPAL_MISMATCH").flatMap((f) => f.subscription_ids));
  const list = el("div", { class: "pp-list", role: "list" });
  if (!p.rows.length) list.append(el("p", { class: "sub" }, "None of your subscriptions are billed through PayPal in this statement."));
  for (const r of p.rows) {
    const ended = ["CANCELLED", "SUSPENDED", "EXPIRED"].includes(r.status), bad = mismatch.has(r.subscription_id);
    list.append(el("div", { class: "pp-row" + (bad ? " alert" : ""), role: "listitem" },
      el("span", { class: "name" }, r.merchant),
      el("span", { class: "chip" + (ended ? " alert" : r.status === "ACTIVE" ? " good" : "") }, el("i", { class: "dot" }), r.status.charAt(0) + r.status.slice(1).toLowerCase().replace("_", " ")),
      el("div", { class: "meta" },
        el("span", {}, ended ? `${r.status === "CANCELLED" ? "Cancelled" : "Ended"} ${fdate(r.status_update_time)}` : `Next billing ${fdate(r.next_billing_time)}`),
        r.last_payment_amount != null ? el("span", {}, `Last payment ${money(r.last_payment_amount, r.last_payment_currency || "USD")} on ${fdate(r.last_payment_time)}`) : null,
        bad ? el("span", { style: { color: "var(--alert-ink)", fontWeight: "650" } }, "Still charged on your statement") : null,
        el("a", { href: r.manage_url, target: "_blank", rel: "noopener noreferrer" }, "Manage in PayPal ↗"))));
  }
  panel.append(list);
  const s = p.sources;
  panel.append(el("p", { class: "sub", style: { marginTop: "14px", marginBottom: "0", fontSize: ".84rem" } },
    `${s.upload} rows from your upload · ${s.paypal_api + s.paypal_mock} from PayPal${p.mode === "mock" ? " (demo)" : ""} · ${s.confirmed_by_paypal} upload rows confirmed by PayPal`));
  return panel;
}

export function renderOverview(root, state, ctx) {
  destroyIn(root);
  const a = state.analysis, S = a.summary, cur = S.currency || "USD", H = S.health;
  const open = a.findings.filter((f) => f.status === "open");
  const active = a.subscriptions.filter((s) => s.active);

  if (!a.subscriptions.length) {
    root.append(el("div", { class: "page-head" }, el("p", { class: "eyebrow" }, "Overview"), el("h1", { id: "ov-title" }, "No repeat charges found")),
      el("div", { class: "empty glass panel" }, icon("search", "art"), el("h3", {}, "We didn't spot any subscriptions"),
        el("p", {}, "That can mean the statement is short (we need at least three months to see a monthly pattern) or it's mostly one-off purchases. Try a longer date range."),
        el("button", { class: "btn btn-primary", type: "button", on: { click: () => ctx.goto("landing") } }, "Try another statement")));
    return;
  }

  // ---- hero: the big number + health ring
  const yearly = el("span", { class: "num" }, money(0, cur, 0));
  const score = el("span", { class: "ring-score num" }, "0");
  root.append(el("section", { class: "ov-hero", "data-mood": "deepsea", aria: { labelledby: "ov-title" } },
    el("div", { class: "reveal" },
      el("p", { class: "eyebrow" }, "Your subscriptions cost"),
      el("h1", { id: "ov-title", class: "vh" }, "Overview"),
      el("div", { class: "big-number num", "aria-hidden": "true" }, yearly, el("small", {}, " a year")),
      el("p", { class: "ov-sub" }, el("b", {}, money(S.monthly_spend, cur)), ` a month across ${S.active_subscriptions} active subscription${S.active_subscriptions === 1 ? "" : "s"}. `,
        open.length ? `We found ${open.length} thing${open.length === 1 ? "" : "s"} worth a look.` : "Nothing needs your attention right now.")),
    el("div", { class: "ring-card glass reveal" + (H.score < 55 ? " low" : ""), style: { "--i": "2" } },
      el("div", { class: "ring", role: "img", "aria-label": `Subscription health score: ${H.score} out of 100, ${H.label}` }, ring(H.score),
        el("div", { class: "ring-center" }, score, el("span", { class: "ring-of" }, "out of 100"))),
      el("div", { class: "ring-label" }, H.label),
      el("details", { class: "formula disclosure" }, el("summary", {}, "How is this calculated?"),
        el("p", {}, H.formula),
        el("div", { class: "parts" }, el("span", { class: "chip" }, `Money at risk −${H.breakdown.money_at_risk}`), el("span", { class: "chip" }, `Open findings −${H.breakdown.open_findings}`), el("span", { class: "chip" }, `Sprawl −${H.breakdown.subscription_sprawl}`))))));
  countUp(yearly, S.annual_spend, { format: (v) => money(v, cur, 0), ms: 1900 });
  countUp(score, H.score, { ms: 1900 });

  // ---- metrics
  const metric = (k, v, d, cls = "") => { const n = el("div", { class: "v num" }, "0"); return { node: el("article", { class: `metric glass reveal ${cls}`.trim() }, el("div", { class: "k" }, k), n, el("div", { class: "d" }, d)), n, v }; };
  const ms = [metric("Monthly spend", S.monthly_spend, `${S.active_subscriptions} active subscriptions`), metric("Yearly spend", S.annual_spend, "at today's prices"),
    metric("Potential savings", S.potential_savings_annual, `about ${money(S.potential_savings_monthly, cur)} a month if you act on every open finding`, "save")];
  ms.forEach((m, i) => { m.node.style.setProperty("--i", String(i)); });
  root.append(el("section", { class: "section", "data-mood": "midnight", aria: { label: "Key figures" } }, el("div", { class: "metrics" }, ms.map((m) => m.node))));
  const io = new IntersectionObserver((es) => es.forEach((e) => { if (e.isIntersecting) { ms.forEach((m) => countUp(m.n, m.v, { format: (v) => money(v, cur, 0), ms: 1500 })); io.disconnect(); } }), { threshold: 0.4 });
  io.observe(ms[0].node);

  // ---- attention
  const top = open.slice(0, 3);
  root.append(el("section", { class: "section", "data-mood": "ember", aria: { labelledby: "attn-title" } },
    el("h2", { id: "attn-title", class: "reveal" }, top.length ? "What needs your attention" : "All clear"),
    el("p", { class: "sub reveal" }, top.length ? "The biggest items first. Each one is explained in plain English on the Findings page, with a message you can send yourself." : "No open findings. Nice. If you dismissed some earlier, you can bring them back on the Findings page."),
    el("div", { class: "attn" }, top.map((f) => findingCard(f, ctx.goto))),
    open.length ? el("div", { class: "reveal", style: { marginTop: "22px" } }, el("button", { class: "btn btn-primary", type: "button", on: { click: () => ctx.goto("findings") } }, `See all ${open.length} finding${open.length === 1 ? "" : "s"} →`)) : null));

  // ---- where it goes + PayPal
  const ranked = [...active].sort((x, y) => y.annual_cost - x.annual_cost).slice(0, 8);
  const flagged = new Set(open.flatMap((f) => f.subscription_ids));
  const canvas = el("canvas", { role: "img", "aria-label": `Bar chart of yearly cost for your ${ranked.length} biggest subscriptions. ${ranked.map((s) => `${s.merchant} ${money(s.annual_cost, s.currency, 0)}`).join(", ")}.` });
  const chartPanel = el("section", { class: "panel glass reveal", aria: { labelledby: "chart-title" } },
    el("h3", { id: "chart-title" }, "Where it goes"), el("p", { class: "sub" }, "Yearly cost of your biggest subscriptions. Coral bars have an open finding."),
    el("div", { class: "chart-box" }, canvas),
    el("details", { class: "disclosure", style: { marginTop: "12px" } }, el("summary", {}, "View chart data"),
      dataTable("Yearly cost by subscription", ["Subscription", "Yearly cost", "Flagged"], ranked.map((s) => [s.merchant, money(s.annual_cost, s.currency), flagged.has(s.id) ? "Yes" : "No"]))));
  root.append(el("section", { class: "section", "data-mood": "dawn" }, el("div", { class: "grid-2" }, chartPanel, paypalPanel(a))));
  const cio = new IntersectionObserver((es) => es.forEach((e) => {
    if (!e.isIntersecting) return;
    cio.disconnect();
    makeChart(canvas, (p) => ({
      type: "bar",
      data: { labels: ranked.map((s) => s.merchant), datasets: [{ data: ranked.map((s) => s.annual_cost), borderRadius: 8, borderSkipped: false, maxBarThickness: 26,
        backgroundColor: ranked.map((s) => (flagged.has(s.id) ? p.alert : p.ink)), hoverBackgroundColor: ranked.map((s) => (flagged.has(s.id) ? p.alert : p.ink)) }] },
      options: { indexAxis: "y", responsive: true, maintainAspectRatio: false, layout: { padding: { right: 12 } },
        plugins: { legend: { display: false }, tooltip: { callbacks: { label: (c) => ` ${money(c.parsed.x, ranked[c.dataIndex].currency)} a year` } } },
        scales: { x: { grid: { color: p.grid }, border: { display: false }, ticks: { color: p.ink3, callback: (v) => "$" + v } }, y: { grid: { display: false }, border: { display: false }, ticks: { color: p.ink2, font: { size: 13 } } } } },
    }));
  }), { threshold: 0.25 });
  cio.observe(canvas);

  // ---- privacy: forget
  root.append(el("section", { class: "section reveal", style: { textAlign: "center" } },
    el("p", { class: "sub", style: { margin: "0 auto 14px" } }, (document.documentElement.dataset.hosted ? "Your statement lives only in this browser tab. Remove it whenever you like." : "Your statement lives only in this app's memory. Remove it whenever you like.")),
    el("button", { class: "btn", type: "button", on: { click: async () => { await api.forget().catch(() => {}); location.reload(); } } }, "Remove this statement from memory")));
  watchMoods(root);
  observeReveals(root);
}
