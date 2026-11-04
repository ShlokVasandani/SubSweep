// The cinematic landing page. Everything is scrubbed by scroll position (no libraries):
//   hero      : headline words rise in, the hero parallaxes away as you scroll;
//   unravel   : a pinned stage where messy statement lines straighten, clean up, snap onto monthly rails, then get flagged;
//   findings  : five glass cards arrive from depth, one after another;
//   decide    : principles + call to action.
// The page background blends between moods (js/moods.js) according to where you are on the page.
// With prefers-reduced-motion everything is drawn in its finished state and nothing is pinned or scrubbed.
import { $, $$, el, clamp, lerp, ease, smooth, icon, reduceMotion, observeReveals } from "./util.js";
import { blendByScroll } from "./theme.js";

// ------------------------------------------------------------------ data for the unravel animation (illustrative)
const MONTHS = ["Jun", "Jul", "Aug", "Sep", "Oct"];
const ROWS = [
  { name: "Netflix", pick: [1, 3, 4], cells: [
    { raw: "NETFLIX.COM LOS GATOS CA REF 884732", amt: 15.49 }, { raw: "NETFLIX.COM LOS GATOS CA REF 991204", amt: 15.49 },
    { raw: "NETFLIX 08/12 REF NO 100233 CA", amt: 15.49 }, { raw: "NETFLIX.COM MUMBAI IN 7742", amt: 17.99, hot: true },
    { raw: "NETFLIX.COM LOS GATOS CA 66120", amt: 17.99, hot: true }] },
  { name: "Spotify", pick: [0, 2, 4], cells: [
    { raw: "SPOTIFY P3A8E1 STOCKHOLM SE", amt: 10.99 }, { raw: "SPOTIFY AB 402-935-7733", amt: 10.99 }, { raw: "Spotify Premium STOCKHOLM", amt: 10.99 },
    { raw: "SPOTIFY P3A8E1 STOCKHOLM SE", amt: 10.99 }, { raw: "SPOTIFY AB 402-935-7733", amt: 10.99 }] },
  { name: "Streamly", pick: [0, 1, 4], cells: [
    { raw: "PAYPAL *STREAMLY 402-935-7733", amt: 9.99 }, { raw: "PAYPAL *STREAMLY 4029357733", amt: 9.99 }, { raw: "PAYPAL *STREAMLY 402-935-7733", amt: 9.99 },
    { raw: "PAYPAL *STREAMLY 4029357733", amt: 9.99 }, { raw: "PAYPAL *STREAMLY ANNUAL 4029357733", amt: 99.99, hot: true }] },
];
const NOISE = [["KROGER #4411 CINCINNATI OH", 54.2], ["SHELL OIL 57444 HOUSTON TX", 38.0], ["STARBUCKS STORE 12044 SEATTLE WA", 5.75],
  ["UBER *TRIP HELP.UBER.COM", 17.4], ["AMZN Mktp US*2K4LM9", 42.1], ["SQ *CORNER CAFE", 6.2]];
const DECK = [
  { type: "DUPLICATE", label: "Duplicate", icon: "copy", alert: true, title: "Streamly: two active subscriptions", body: "$9.99 per month and $99.99 per year, both through PayPal. If you only need one, you could save about", big: "$99.99", unit: "a year" },
  { type: "PRICE_HIKE", label: "Price hike", icon: "warn", alert: true, title: "Netflix went up 16%", body: "From $15.49 to $17.99 per month since April 2026, and it has held for six charges. That adds about", big: "+$30.00", unit: "a year" },
  { type: "POSSIBLY_FORGOTTEN", label: "Possibly forgotten", icon: "search", title: "Hulu: still using it?", body: "$7.99 a month for 24 months, never changing. This is a guess from the statement alone, so you can tell us you still use it. Cancelling would save", big: "$95.88", unit: "a year" },
  { type: "ANNUAL_RENEWAL_SOON", label: "Renewal soon", icon: "flag", title: "A yearly charge is coming up", body: "Your domain renews around 12 Nov 2026 for $13.98. Cancel or switch before then if you don't want it.", big: "$13.98", unit: "on 12 Nov" },
  { type: "PAYPAL_MISMATCH", label: "PayPal mismatch", icon: "shield", alert: true, title: "Charged after PayPal says cancelled", body: "PayPal shows FitPass Gym as cancelled since 11 Aug 2026, but two more charges have hit the statement:", big: "$48.00", unit: "since then" },
];

let stage, chips = [], noiseChips = [], rails = [], railLabels = [], callouts = [], caps = [], cards = [];
let geo = { W: 0, H: 0 }, tops = {}, moodStops = [], visible = true, raf = 0, ticking = false, lastCap = -1;
const money = (v) => "$" + v.toFixed(2);

// mulberry32: deterministic scatter so the animation looks the same on every visit
function rng(seed) { return () => { seed |= 0; seed = (seed + 0x6d2b79f5) | 0; let t = Math.imul(seed ^ (seed >>> 15), 1 | seed); t = (t + Math.imul(t ^ (t >>> 7), 61 | t)) ^ t; return ((t ^ (t >>> 14)) >>> 0) / 4294967296; }; }

// ------------------------------------------------------------------ headline: words rise in
function splitWords(root) {
  let i = 0;
  const walk = (node) => {
    for (const child of [...node.childNodes]) {
      if (child.nodeType === 3) {
        const frag = document.createDocumentFragment();
        child.textContent.split(/(\s+)/).forEach((part) => {
          if (!part) return;
          if (/^\s+$/.test(part)) frag.append(" ");
          else frag.append(el("span", { class: "w", style: { "--i": String(i++) } }, part));
        });
        child.replaceWith(frag);
      } else if (child.nodeType === 1) walk(child);
    }
  };
  walk(root);
}

// ------------------------------------------------------------------ unravel stage
function buildStage() {
  stage = $("#stage");
  stage.replaceChildren();
  const rand = rng(7);
  chips = []; noiseChips = []; rails = []; railLabels = []; callouts = [];
  ROWS.forEach((row, r) => {
    rails.push(stage.appendChild(el("i", { class: "rail" })));
    railLabels.push(stage.appendChild(el("span", { class: "rail-label" }, row.name)));
    row.cells.forEach((cell, c) => {
      const chip = el("div", { class: "tx" },
        el("span", { class: "lab" }, el("span", { class: "raw" }, cell.raw), el("span", { class: "clean", style: { opacity: "0" } }, `${row.name} · ${MONTHS[c]}`)),
        el("span", { class: "amt" }, money(cell.amt)));
      stage.append(chip);
      chips.push({ chip, r, c, cell, raw: chip.querySelector(".raw"), clean: chip.querySelector(".clean"),
        a: rand(), b: rand(), rot: (rand() - .5) * 50, sc: .88 + rand() * .26 });
    });
  });
  NOISE.forEach(([label, amt], i) => {
    const chip = el("div", { class: "tx noise" }, el("span", { class: "lab" }, el("span", { class: "raw" }, label)), el("span", { class: "amt" }, money(amt)));
    stage.append(chip);
    noiseChips.push({ chip, a: rand(), b: rand(), rot: (rand() - .5) * 60, dx: (rand() - .5) * 240, dy: (rand() > .5 ? 1 : -1) * (120 + rand() * 120), i });
  });
  callouts = [
    { node: el("span", { class: "callout" }, "Price up 16% · about $30 more a year"), target: [0, 3] },
    { node: el("span", { class: "callout" }, "Duplicate plan · same service twice"), target: [2, 4] },
  ];
  callouts.forEach((c) => stage.append(c.node));
  caps = $$(".cap-title .cap", $("#scene-unravel"));
  layoutStage();
}

function layoutStage() {
  if (!stage) return;
  const W = stage.clientWidth, H = stage.clientHeight;
  const mobile = W < 640, cols = mobile ? 3 : 5, gap = mobile ? 8 : 14, labelW = mobile ? 0 : 92;
  const cw = mobile ? Math.min(132, (W - (cols - 1) * gap) / cols) : clamp((W - labelW - (cols - 1) * gap) / cols, 138, 192);
  const total = labelW + cols * cw + (cols - 1) * gap, x0 = Math.max(0, (W - total) / 2);
  const rowY = [0.2, 0.46, 0.72].map((f) => H * f);
  geo = { W, H, cw, cols, gap, labelW, x0, rowY, mobile };
  chips.forEach((o) => {
    const col = o.cell && ROWS[o.r].pick ? (mobile ? ROWS[o.r].pick.indexOf(o.c) : o.c) : o.c;
    o.visible = mobile ? col !== -1 : true;
    o.col = col;
    o.tx = x0 + labelW + col * (cw + gap); o.ty = rowY[o.r];
    o.sx = o.a * Math.max(0, W - cw); o.sy = o.b * Math.max(0, H - 46);
    o.chip.style.setProperty("--cw", cw + "px");
    o.chip.style.display = o.visible ? "" : "none";
  });
  noiseChips.forEach((o) => { o.chip.style.setProperty("--cw", cw + "px"); o.sx = o.a * Math.max(0, W - cw); o.sy = o.b * Math.max(0, H - 46); });
  rails.forEach((r, i) => { r.style.top = rowY[i] + 23 + "px"; r.style.left = x0 + labelW - 8 + "px"; r.style.width = total - labelW + 16 + "px"; });
  railLabels.forEach((l, i) => { l.style.top = rowY[i] + 14 + "px"; l.style.left = x0 + "px"; l.style.display = mobile ? "none" : ""; });
  tops = measure();
}

function updateStage(p) {
  if (!stage) return;
  const { rowY, cw, W, H } = geo;
  const t1 = ease((p - .04) / .4), t2 = smooth((p - .28) / .24), t3 = smooth((p - .56) / .2), t4 = smooth((p - .8) / .14);
  for (const o of chips) {
    if (!o.visible) continue;
    const x = lerp(o.sx, o.tx, t1), y = lerp(o.sy, o.ty, t1), rot = lerp(o.rot, 0, t1), sc = lerp(o.sc, 1, t1);
    o.chip.style.transform = `translate3d(${x}px,${y}px,0) rotate(${rot}deg) scale(${sc})`;
    o.chip.style.filter = t1 < .99 ? `blur(${lerp(2.6, 0, t1)}px)` : "none";
    o.chip.style.opacity = String(lerp(.5, 1, t1));
    o.raw.style.opacity = String(1 - t2); o.clean.style.opacity = String(t2);
    o.chip.classList.toggle("hot", !!o.cell.hot && t4 > .05);
  }
  for (const o of noiseChips) {
    const f = smooth((p - .26) / .26);
    o.chip.style.transform = `translate3d(${o.sx + o.dx * f}px,${o.sy + o.dy * f}px,0) rotate(${o.rot * (1 + f)}deg) scale(${1 - .25 * f})`;
    o.chip.style.opacity = String(Math.max(0, (1 - f) * .85));
    o.chip.style.filter = `blur(${lerp(2.2, 7, f)}px)`;
  }
  rails.forEach((r, i) => { r.style.opacity = String(t3 * .8); r.style.transform = `scaleX(${t3})`; });
  railLabels.forEach((l) => { l.style.opacity = String(t3); });
  callouts.forEach((c) => {
    const o = chips.find((k) => k.r === c.target[0] && k.c === c.target[1]);
    if (!o || !o.visible) { c.node.style.opacity = "0"; return; }
    const w = c.node.offsetWidth || 220;
    const x = clamp(o.tx + cw / 2 - w / 2, 0, Math.max(0, W - w)), y = o.ty - 46 + (1 - t4) * 14;
    c.node.style.transform = `translate3d(${x}px,${y}px,0)`;
    c.node.style.opacity = String(t4);
  });
  const idx = p < .26 ? 0 : p < .54 ? 1 : p < .79 ? 2 : 3;
  if (idx !== lastCap) { caps.forEach((c, i) => c.classList.toggle("on", i === idx)); lastCap = idx; }
}

// ------------------------------------------------------------------ card deck
function buildDeck() {
  const deck = $("#deck");
  deck.replaceChildren();
  cards = DECK.map((d, i) => {
    const c = el("article", { class: "fcard glass" + (d.alert ? " alert" : ""), aria: { label: `${d.label}: ${d.title}` } },
      el("div", { class: "row" }, el("span", { class: "ico" }, icon(d.icon)), el("span", { class: "chip" + (d.alert ? " alert" : "") }, d.label)),
      el("h3", {}, d.title), el("p", {}, d.body),
      el("div", { class: "big" }, d.big, " ", el("small", {}, d.unit)));
    deck.append(c);
    return c;
  });
  deck.append(el("p", { class: "vh" }, "Illustrative figures from the built-in sample statement."));
}
function updateDeck(p) {
  const N = cards.length, s = clamp(p) * (N - 1);
  cards.forEach((c, i) => {
    const q = s - i;
    let tx = 0, ty, tz, rx, op, sc = 1, blur = 0;
    if (q < 0) { const u = clamp(-q, 0, 2); ty = 70 * u; tz = -620 * u; rx = 26 * u; op = clamp(1 - u * 1.15, 0, 1); blur = u * 5; }
    else { const v = clamp(q, 0, 3); ty = -74 * v; tz = -300 * v; rx = -10 * v; op = clamp(1 - .46 * v, 0, 1); sc = 1 - .05 * v; tx = -110 * v; blur = v * 2.4; }
    c.style.transform = `translate3d(${tx}px,${ty}px,${tz}px) rotateX(${rx}deg) scale(${sc})`;
    c.style.opacity = String(op);
    c.style.filter = blur > .1 ? `blur(${blur}px)` : "none";
    c.style.zIndex = String(Math.round(100 - Math.abs(q) * 20));
    c.style.pointerEvents = Math.abs(q) < .5 ? "auto" : "none";
    c.setAttribute("aria-hidden", Math.abs(q) < 1.5 ? "false" : "true");
  });
}

// ------------------------------------------------------------------ scroll loop
function measure() {
  const top = (n) => (n ? n.getBoundingClientRect().top + scrollY : 0);
  const sceneUn = $("#scene-unravel"), sceneFi = $("#scene-findings");
  return { un: top(sceneUn), unH: sceneUn?.offsetHeight || 1, fi: top(sceneFi), fiH: sceneFi?.offsetHeight || 1 };
}
function computeMoodStops() {
  moodStops = $$("#view-landing [data-mood]").map((s) => ({ y: s.getBoundingClientRect().top + scrollY + s.offsetHeight / 2, mood: s.dataset.mood }));
}
function frame() {
  ticking = false;
  if (!visible) return;
  const y = scrollY, vh = innerHeight;
  $(".hero-inner")?.style.setProperty("--hero-p", clamp(y / (vh * .9)).toFixed(3));
  if (!reduceMotion()) {
    updateStage(clamp((y - tops.un) / Math.max(1, tops.unH - vh)));
    updateDeck(clamp((y - tops.fi) / Math.max(1, tops.fiH - vh)));
    blendByScroll(moodStops, y + vh / 2);
  }
}
function request() { if (!ticking) { ticking = true; raf = requestAnimationFrame(frame); } }

export function setLandingVisible(v) {
  visible = v;
  if (v) { setTimeout(() => { layoutStage(); computeMoodStops(); request(); }, 30); }
}

export function initLanding() {
  splitWords($("[data-split]"));
  buildStage();
  buildDeck();
  if (reduceMotion()) { updateStage(1); updateDeck(1); cards.forEach((c) => { c.style.transform = ""; c.style.opacity = ""; c.style.filter = ""; c.style.zIndex = ""; }); }
  computeMoodStops();
  addEventListener("scroll", request, { passive: true });
  document.addEventListener("themechange", () => { computeMoodStops(); request(); });
  addEventListener("resize", () => { layoutStage(); computeMoodStops(); request(); });
  addEventListener("load", () => { layoutStage(); computeMoodStops(); request(); });
  document.fonts?.ready.then(() => { layoutStage(); computeMoodStops(); request(); });
  observeReveals($("#view-landing"));
  request();
}
