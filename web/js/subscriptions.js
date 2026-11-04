import { el, icon, money, fdate, TYPE_LABEL, CYCLE, clear } from "./util.js";
import { api } from "./api.js";
import { makeChart, dataTable, destroyIn } from "./charts.js";

const ALERT_FLAGS = new Set(["DUPLICATE", "PRICE_HIKE", "PAYPAL_MISMATCH", "ZOMBIE"]);
const COLS = [
  ["merchant", "Subscription", ""], ["cycle", "Cycle", ""], ["current_amount", "Price", "num"], ["annual_cost", "Per year", "num"],
  ["confidence", "Confidence", ""], ["status", "Status", ""],
];
const sortValue = (s, k) => (k === "status" ? (s.flags.length ? 2 : 0) + (s.active ? 1 : 0) : s[k]);

export function renderSubscriptions(root, state, ctx) {
  destroyIn(root);
  const subs = state.analysis.subscriptions;
  const view = { q: "", cycle: "all", status: "all", pp: false, key: "annual_cost", dir: -1 };

  root.append(el("div", { class: "page-head" }, el("p", { class: "eyebrow" }, "Subscriptions"),
    el("h1", { id: "subs-title" }, `${subs.filter((s) => s.active).length} active, ${subs.filter((s) => !s.active).length} ended`),
    el("p", {}, "Everything that repeats on your statement. Open a row to see its charge history, why we're confident, and what PayPal says.")));

  if (!subs.length) {
    root.append(el("div", { class: "empty glass panel" }, icon("search", "art"), el("h3", {}, "No subscriptions detected"),
      el("p", {}, "We look for the same merchant charging on a regular rhythm (weekly, monthly, quarterly or yearly). Groceries, fuel and one-off buying are deliberately ignored."),
      el("button", { class: "btn btn-primary", type: "button", on: { click: () => ctx.goto("landing") } }, "Try another statement")));
    return;
  }

  const q = el("input", { class: "field", type: "search", id: "subs-q", placeholder: "Search subscriptions", autocomplete: "off", "aria-label": "Search subscriptions" });
  const cyc = el("select", { class: "field", "aria-label": "Filter by billing cycle" }, el("option", { value: "all" }, "All cycles"), ["monthly", "quarterly", "yearly", "weekly"].map((c) => el("option", { value: c }, c[0].toUpperCase() + c.slice(1))));
  const st = el("select", { class: "field", "aria-label": "Filter by status" }, el("option", { value: "all" }, "Any status"), el("option", { value: "active" }, "Active"), el("option", { value: "ended" }, "Ended"), el("option", { value: "flagged" }, "Has an open finding"));
  const ppBox = el("input", { type: "checkbox" });
  const count = el("span", { class: "count-line", role: "status", "aria-live": "polite" });
  root.append(el("div", { class: "toolbar glass", role: "search" }, el("div", { class: "search" }, icon("search"), q), cyc, st, el("label", { class: "check" }, ppBox, "Billed via PayPal"), count));

  const tbody = el("tbody");
  const ths = COLS.map(([key, label, cls]) => el("th", { scope: "col", class: cls, "aria-sort": key === view.key ? "descending" : null, dataset: { key } }, el("button", { type: "button", on: { click: () => sortBy(key) } }, label)));
  ths.push(el("th", { scope: "col", class: "num" }, el("span", { class: "vh" }, "Actions")));
  const table = el("table", { class: "subs" }, el("caption", { class: "vh" }, "Detected subscriptions. Use the column headings to sort."), el("thead", {}, el("tr", {}, ths)), tbody);
  root.append(el("div", { class: "table-wrap glass" }, table));

  function sortBy(key) {
    if (view.key === key) view.dir *= -1; else { view.key = key; view.dir = key === "merchant" || key === "cycle" ? 1 : -1; }
    ths.forEach((th) => { if (th.dataset?.key) { if (th.dataset.key === view.key) th.setAttribute("aria-sort", view.dir > 0 ? "ascending" : "descending"); else th.removeAttribute("aria-sort"); } });
    draw();
  }
  function filtered() {
    const needle = view.q.trim().toLowerCase();
    return subs.filter((s) => (!needle || s.merchant.toLowerCase().includes(needle)) && (view.cycle === "all" || s.cycle === view.cycle)
      && (view.status === "all" || (view.status === "active" && s.active) || (view.status === "ended" && !s.active) || (view.status === "flagged" && s.flags.length))
      && (!view.pp || s.via_paypal)).sort((a, b) => {
        const x = sortValue(a, view.key), y = sortValue(b, view.key);
        return (typeof x === "string" ? x.localeCompare(y) : x - y) * view.dir || a.merchant.localeCompare(b.merchant);
      });
  }
  function draw() {
    const rows = filtered();
    destroyIn(tbody);
    clear(tbody);
    count.textContent = `Showing ${rows.length} of ${subs.length}`;
    if (!rows.length) {
      tbody.append(el("tr", {}, el("td", { colspan: "7" }, el("div", { class: "empty" }, icon("search", "art"), el("h3", {}, "Nothing matches those filters"), el("p", {}, "Try a different search, or clear the filters."),
        el("button", { class: "btn", type: "button", on: { click: reset } }, "Clear filters")))));
      return;
    }
    rows.forEach((s) => tbody.append(...row(s)));
  }
  function reset() { view.q = ""; view.cycle = view.status = "all"; view.pp = false; q.value = ""; cyc.value = st.value = "all"; ppBox.checked = false; draw(); }
  q.addEventListener("input", () => { view.q = q.value; draw(); });
  cyc.addEventListener("change", () => { view.cycle = cyc.value; draw(); });
  st.addEventListener("change", () => { view.status = st.value; draw(); });
  ppBox.addEventListener("change", () => { view.pp = ppBox.checked; draw(); });

  function row(s) {
    const detailId = `detail-${s.id}`;
    const flags = [...new Set(s.flags)].map((f) => el("span", { class: "chip" + (ALERT_FLAGS.has(f) ? " alert" : "") }, TYPE_LABEL[f] || f));
    const btn = el("button", { class: "exp", type: "button", aria: { expanded: "false", controls: detailId, label: `Details for ${s.merchant}` }, on: { click: () => toggle() } }, icon("chev"));
    const tr = el("tr", { class: "row" },
      el("td", { class: "cell-name" }, el("div", { class: "merchant" }, el("span", { class: "avatar", "aria-hidden": "true" }, s.merchant[0].toUpperCase()),
        el("span", {}, el("b", {}, s.merchant), el("small", {}, [s.category, s.via_paypal ? "via PayPal" : null].filter(Boolean).join(" · ") || `${s.n_charges} charges`)))),
      el("td", { dataset: { label: "Cycle" } }, s.cycle[0].toUpperCase() + s.cycle.slice(1)),
      el("td", { class: "num", dataset: { label: "Price" } }, money(s.current_amount, s.currency)),
      el("td", { class: "num", dataset: { label: "Per year" } }, money(s.annual_cost, s.currency, 0)),
      el("td", { dataset: { label: "Confidence" } }, el("span", { class: "meter", title: s.confidence_reason }, el("i", { style: { "--v": s.confidence } }), el("span", { class: "num" }, Math.round(s.confidence * 100) + "%"))),
      el("td", { dataset: { label: "Status" } }, el("div", { class: "flags" }, el("span", { class: "chip" + (s.active ? " good" : "") }, s.active ? "Active" : "Ended"), flags)),
      el("td", { class: "num cell-exp" }, btn));
    const detail = el("tr", { class: "detail", id: detailId, hidden: true }, el("td", { colspan: "7" }));
    let open = false, chart = null;
    function toggle() {
      open = !open;
      btn.setAttribute("aria-expanded", String(open));
      detail.hidden = !open;
      if (open) fillDetail(detail.firstChild, s, (c) => { chart = c; });
      else if (chart) { chart.destroy(); chart = null; }
    }
    return [tr, detail];
  }

  function fillDetail(cell, s, setChart) {
    clear(cell);
    const canvas = el("canvas", { role: "img", "aria-label": `Line chart of ${s.merchant} charges over time` });
    const left = el("div", {}, el("h4", {}, "Charge history"), el("div", { class: "small-chart" }, el("div", { class: "skeleton", style: { height: "100%" } })));
    const pp = s.paypal;
    const facts = el("dl", {},
      el("dt", {}, "First charge"), el("dd", {}, fdate(s.first_date)), el("dt", {}, "Last charge"), el("dd", {}, fdate(s.last_date)),
      s.active ? [el("dt", {}, "Next expected"), el("dd", {}, fdate(s.next_expected_date))] : null,
      el("dt", {}, "Charges"), el("dd", {}, String(s.n_charges)), el("dt", {}, "Average"), el("dd", {}, money(s.avg_amount, s.currency)),
      el("dt", {}, "Per month"), el("dd", {}, money(s.monthly_cost, s.currency)),
      el("dt", {}, "Confidence"), el("dd", {}, s.confidence_reason));
    const actions = el("div", { class: "actions" });
    if (pp) actions.append(el("a", { class: "btn btn-sm", href: pp.manage_url, target: "_blank", rel: "noopener noreferrer" }, "Manage in PayPal ", icon("ext")));
    if (s.flags.length) actions.append(el("button", { class: "btn btn-sm", type: "button", on: { click: () => ctx.goto("findings") } }, `See ${new Set(s.flags).size} finding${new Set(s.flags).size === 1 ? "" : "s"}`));
    const right = el("div", {}, el("h4", {}, "The details"), facts,
      pp ? [el("h4", { style: { marginTop: "18px" } }, "PayPal says"), el("dl", {}, el("dt", {}, "Status"), el("dd", {}, pp.status),
        el("dt", {}, ["CANCELLED", "SUSPENDED", "EXPIRED"].includes(pp.status) ? "Since" : "Next billing"), el("dd", {}, fdate(["CANCELLED", "SUSPENDED", "EXPIRED"].includes(pp.status) ? pp.status_update_time : pp.next_billing_time)))] : null,
      actions);
    cell.append(el("div", { class: "detail-inner" }, left, right));
    api.history(s.id).then((h) => {
      const box = left.querySelector(".small-chart");
      clear(box); box.append(canvas);
      const incDate = (h.price_changes.find((c) => c.kind === "increase") || {}).date;
      setChart(makeChart(canvas, (p) => ({
        type: "line",
        data: { labels: h.points.map((x) => fdate(x.date)), datasets: [{ data: h.points.map((x) => x.amount), stepped: "before", borderColor: p.ink, borderWidth: 2, tension: 0, fill: false,
          pointRadius: 4, pointHoverRadius: 6, pointBackgroundColor: h.points.map((x) => (incDate && x.date >= incDate ? p.alert : p.ink)),
          pointBorderColor: h.points.map((x) => (incDate && x.date >= incDate ? p.alert : p.ink)) }] },
        options: { responsive: true, maintainAspectRatio: false, plugins: { legend: { display: false },
          tooltip: { callbacks: { label: (c) => ` ${money(c.parsed.y, h.currency)}`, afterLabel: (c) => { const pt = h.points[c.dataIndex]; return pt.confirmed_by_paypal ? "Confirmed by PayPal" : pt.source.includes("paypal") ? "From PayPal" : ""; } } } },
          scales: { x: { grid: { display: false }, ticks: { color: p.ink3, maxTicksLimit: 6, maxRotation: 0 } }, y: { grid: { color: p.grid }, border: { display: false }, ticks: { color: p.ink3, maxTicksLimit: 5, callback: (v) => money(v, h.currency, v % 1 ? 2 : 0) } } } },
      })));
      const prices = [...new Set(h.points.map((x) => x.amount))];
      left.append(el("details", { class: "disclosure", style: { marginTop: "8px" } }, el("summary", {}, "View chart data"),
        dataTable(`${s.merchant} charges`, ["Date", "Amount", "Source"], h.points.map((x) => [fdate(x.date), money(x.amount, h.currency), x.confirmed_by_paypal ? "Statement, confirmed by PayPal" : x.source.includes("paypal") ? "PayPal" : "Statement"]))),
        incDate ? el("p", { class: "disclosure", style: { marginTop: "6px" } }, `Price rose to ${money(h.price_changes.find((c) => c.kind === "increase").new_amount, h.currency)} on ${fdate(incDate)}.`) : null);
    }).catch((e) => {
      const box = left.querySelector(".small-chart");
      clear(box);
      box.append(el("div", { class: "banner alert" }, e.message, " ", el("button", { class: "btn btn-sm", type: "button", on: { click: () => fillDetail(cell, s, setChart) } }, "Try again")));
    });
  }

  draw();
}
