// Chart.js (vendored, loaded as a global) styled from the CSS variables so it follows the night / day theme.
import { reduceMotion } from "./util.js";

const live = new Set();
const css = (name) => getComputedStyle(document.documentElement).getPropertyValue(name).trim();

export function palette() {
  return { ink: css("--ink"), ink2: css("--ink-2"), ink3: css("--ink-3"), grid: css("--glass-border"), alert: css("--alert"), good: css("--good") };
}

function applyDefaults(Chart) {
  const p = palette();
  Chart.defaults.font.family = css("--font-ui") || "system-ui";
  Chart.defaults.font.size = 12;
  Chart.defaults.color = p.ink2;
  Chart.defaults.borderColor = p.grid;
  Chart.defaults.animation = reduceMotion() ? false : { duration: 1400, easing: "easeOutQuart" };
}

/** Create a chart. `build(palette)` returns the Chart.js config, and is called again when the theme changes. */
export function makeChart(canvas, build) {
  const Chart = window.Chart;
  if (!Chart) return null;                                      // vendored script failed to load: the data table below still works
  applyDefaults(Chart);
  const chart = new Chart(canvas, build(palette()));
  live.add({ chart, build });
  return chart;
}

export function destroyIn(root) {
  for (const entry of [...live]) {
    if (root.contains(entry.chart.canvas) || !entry.chart.canvas.isConnected) { entry.chart.destroy(); live.delete(entry); }
  }
}

document.addEventListener("themechange", () => {
  if (!window.Chart) return;
  requestAnimationFrame(() => {
    applyDefaults(window.Chart);
    for (const { chart, build } of live) {
      if (!chart.canvas.isConnected) continue;
      const cfg = build(palette());
      chart.options = cfg.options; chart.data = cfg.data;
      chart.update("none");
    }
  });
});

/** Accessible fallback for a chart: a <details> with the same data as a table. */
export function dataTable(caption, headers, rows) {
  const t = document.createElement("table");
  t.className = "mini";
  const cap = document.createElement("caption"); cap.className = "vh"; cap.textContent = caption; t.append(cap);
  const head = document.createElement("tr");
  headers.forEach((h) => { const th = document.createElement("th"); th.scope = "col"; th.textContent = h; head.append(th); });
  t.append(head);
  rows.forEach((r) => { const tr = document.createElement("tr"); r.forEach((c) => { const td = document.createElement("td"); td.textContent = c; tr.append(td); }); t.append(tr); });
  return t;
}
