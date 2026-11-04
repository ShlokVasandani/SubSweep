// Theme + mood engine. The page background is a blend of "moods" (see moods.js). On the landing page the blend follows
// the scroll position continuously; inside the app each section sets a target mood and we tween to it.
import { NIGHT, DAY } from "./moods.js";

const root = document.documentElement;
const reduce = () => matchMedia("(prefers-reduced-motion: reduce)").matches;
let theme = "night";
let cur = null;           // current blended colours { a, b, g1, g2, g3 } as float arrays
let tweenRaf = 0;
let lastMood = "midnight";       // the mood most recently asked for, so a theme switch can repaint it in the other palette

export const table = () => (theme === "day" ? DAY : NIGHT);
export const getTheme = () => theme;

const lerp = (a, b, t) => a + (b - a) * t;
const mix = (m1, m2, t) => Object.fromEntries(Object.keys(m1).map((k) => [k, m1[k].map((v, i) => lerp(v, m2[k][i], t))]));
const smooth = (t) => t * t * (3 - 2 * t);
const rgb = (c) => `rgb(${c.map((v) => Math.round(v)).join(" ")})`;

function paint(m) {
  cur = m;
  const s = root.style;
  s.setProperty("--bg-a", rgb(m.a)); s.setProperty("--bg-b", rgb(m.b));
  s.setProperty("--glow-1", rgb(m.g1)); s.setProperty("--glow-2", rgb(m.g2)); s.setProperty("--glow-3", rgb(m.g3));
}

export function snap(name) { cancelAnimationFrame(tweenRaf); lastMood = name; paint(table()[name]); }

/** Ease the background to a named mood (used by app sections). */
export function tweenTo(name, ms = 1100) {
  const to = table()[name];
  lastMood = name;
  if (!cur || reduce()) return paint(to);
  cancelAnimationFrame(tweenRaf);
  const from = cur, t0 = performance.now();
  const step = (now) => {
    const t = Math.min(1, (now - t0) / ms);
    paint(mix(from, to, smooth(t)));
    if (t < 1) tweenRaf = requestAnimationFrame(step);
  };
  tweenRaf = requestAnimationFrame(step);
}

/** Continuous blend by scroll: stops = [{ y, mood }] sorted by y; yNow = viewport centre in page coordinates. */
export function blendByScroll(stops, yNow) {
  if (reduce() || !stops.length) return;
  cancelAnimationFrame(tweenRaf);
  const T = table();
  if (yNow <= stops[0].y) return paint(T[stops[0].mood]);
  for (let i = 0; i < stops.length - 1; i++) {
    const a = stops[i], b = stops[i + 1];
    if (yNow <= b.y) return paint(mix(T[a.mood], T[b.mood], smooth((yNow - a.y) / (b.y - a.y))));
  }
  paint(T[stops[stops.length - 1].mood]);
}

export function setProgress() {
  const max = Math.max(1, document.documentElement.scrollHeight - innerHeight);
  root.style.setProperty("--p", (scrollY / max).toFixed(4));
}

export function setTheme(t, remember = true) {
  theme = t === "day" ? "day" : "night";
  root.dataset.theme = theme;
  if (remember) { try { localStorage.setItem("subsweep-theme", theme); } catch { /* private mode */ } }
  if (cur) snap(lastMood);          // repaint the background in the new palette straight away (scroll blending then refines it)
  document.querySelectorAll("[data-theme-toggle]").forEach((b) => {
    b.setAttribute("aria-pressed", String(theme === "day"));
    b.querySelector(".label").textContent = theme === "day" ? "Day" : "Night";
  });
  document.dispatchEvent(new CustomEvent("themechange", { detail: theme }));
}

export function initTheme() {
  let saved = null;
  try { saved = localStorage.getItem("subsweep-theme"); } catch { /* ignore */ }
  setTheme(saved || "night", false);
  snap("midnight");
  document.querySelectorAll("[data-theme-toggle]").forEach((b) => b.addEventListener("click", () => setTheme(theme === "day" ? "night" : "day")));
}
