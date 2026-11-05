// Records the demo video described in docs/DEMO_SCRIPT.md with Playwright, against a running SubSweep (start it with PAYPAL_MODE=mock).
//
//   PAYPAL_MODE=mock PORT=8765 python -m subsweep &
//   node scripts/record_demo.js http://127.0.0.1:8765/ sample_data/demo_statement.csv docs/video
//
// Writes <outDir>/subsweep-demo.webm (raw capture) and <outDir>/recording_log.json (when each beat really started and ended).
// The capture is silent: it paces every click to the script's timestamps so a voice-over (or captions) can be laid on top.
// A fake cursor, click ripples and a dragged-file chip are drawn inside the recorded page only, so the video shows what is happening.
const { chromium } = require("playwright");
const fs = require("fs");
const path = require("path");

const [BASE, CSV_PATH, OUT = "docs/video"] = process.argv.slice(2);
const W = 1920, H = 1080;
const BEATS = [
  { n: 1, label: "The problem", s: 0, e: 20 }, { n: 2, label: "Upload", s: 20, e: 40 }, { n: 3, label: "Detection", s: 40, e: 65 },
  { n: 4, label: "A flagged duplicate", s: 65, e: 90 }, { n: 5, label: "PayPal status check", s: 90, e: 115 },
  { n: 6, label: "A drafted message", s: 115, e: 135 }, { n: 7, label: "One question", s: 135, e: 150 },
];

(async () => {
  fs.mkdirSync(OUT, { recursive: true });
  const csv = fs.readFileSync(CSV_PATH, "utf8");
  const browser = await chromium.launch();
  const ctx = await browser.newContext({
    viewport: { width: W, height: H }, deviceScaleFactor: 1,
    recordVideo: { dir: path.join(OUT, "_raw"), size: { width: W, height: H } },
    permissions: ["clipboard-read", "clipboard-write"],
  });
  await ctx.addInitScript(() => {
    const build = () => {
      if (document.getElementById("__cur")) return;
      const st = document.createElement("style");
      // html: the recorder eases its own scrolling, so the page's smooth scrolling must not apply a second time.
      // .blob: the aurora blobs are radial gradients that already fade to transparent, then get a 90px blur and a blend mode on top. Without a GPU
      // (software rendering, as in the cloud container) that single filter drops the page to about 3 frames a second, which makes an unusable video.
      // Dropping only the extra blur looks the same (mean pixel difference about 1/255 in a side-by-side) and renders at 50+ fps.
      st.textContent = "html{scroll-behavior:auto !important} .blob{filter:none !important}";
      document.head.append(st);
      const cur = document.createElement("div");
      cur.id = "__cur";
      cur.style.cssText = "position:fixed;left:0;top:0;width:24px;height:24px;z-index:2147483647;pointer-events:none;transform:translate(-100px,-100px);filter:drop-shadow(0 2px 3px rgba(0,0,0,.6))";
      cur.innerHTML = '<svg width="24" height="24" viewBox="0 0 24 24"><path d="M4 2l15 9-6.5 1.6L9 19z" fill="#fff" stroke="#10131f" stroke-width="1.5" stroke-linejoin="round"/></svg>';
      const ghost = document.createElement("div");
      ghost.id = "__ghost";
      ghost.style.cssText = "position:fixed;left:0;top:0;z-index:2147483646;pointer-events:none;display:none;padding:8px 14px;border-radius:10px;background:rgba(255,255,255,.95);color:#10131f;font:600 15px system-ui,sans-serif;box-shadow:0 8px 24px rgba(0,0,0,.45)";
      ghost.textContent = "demo_statement.csv";
      document.documentElement.append(cur, ghost);
      let x = -100, y = -100;
      addEventListener("mousemove", (e) => {
        x = e.clientX; y = e.clientY;
        cur.style.transform = `translate(${x}px,${y}px)`;
        ghost.style.transform = `translate(${x + 14}px,${y + 18}px)`;
      }, true);
      addEventListener("mousedown", (e) => {
        const r = document.createElement("div");
        r.style.cssText = `position:fixed;left:${e.clientX - 20}px;top:${e.clientY - 20}px;width:40px;height:40px;border-radius:50%;border:3px solid rgba(255,154,134,.95);z-index:2147483645;pointer-events:none`;
        document.documentElement.append(r);
        r.animate([{ transform: "scale(.3)", opacity: 1 }, { transform: "scale(1.5)", opacity: 0 }], { duration: 520, easing: "ease-out" }).onfinish = () => r.remove();
      }, true);
      window.__ghost = (on) => { ghost.style.display = on ? "block" : "none"; };
    };
    if (document.readyState === "loading") addEventListener("DOMContentLoaded", build); else build();
  });

  const tBefore = Date.now();
  const page = await ctx.newPage();
  const t0 = Date.now();                                   // t = 0 of the script; the capture started a few ms earlier, trimmed later
  const problems = [];
  page.on("pageerror", (e) => problems.push("pageerror: " + e.message));
  page.on("console", (m) => { if (["error", "warning"].includes(m.type())) problems.push(`${m.type()}: ${m.text()}`); });
  page.on("requestfailed", (r) => { if (!(r.failure()?.errorText || "").includes("ABORTED")) problems.push("requestfailed: " + r.url()); });

  const now = () => (Date.now() - t0) / 1000;
  const log = { video_lead_ms: t0 - tBefore, beats: [], problems };
  const until = async (t) => { const d = t - now(); if (d > 0) await page.waitForTimeout(d * 1000); };
  const say = (m) => console.error(`[${now().toFixed(1).padStart(6)}s] ${m}`);
  const beat = async (b, fn) => {
    await until(b.s);
    const started = now();
    say(`beat ${b.n} start: ${b.label}`);
    await fn();
    const finishedWork = now();
    say(`beat ${b.n} work done (target end ${b.e}s)`);
    await until(b.e);
    log.beats.push({ ...b, started: +started.toFixed(2), work_done: +finishedWork.toFixed(2), ended: +now().toFixed(2), overran: finishedWork > b.e });
  };

  // ---------------------------------------------------------------- helpers
  let mx = 1500, my = 900;
  const ease = (t) => (t < 0.5 ? 2 * t * t : 1 - Math.pow(-2 * t + 2, 2) / 2);
  async function moveTo(x, y, ms = 700) {           // time-based: a move lasts `ms` however slow each mouse event is to process
    const x0 = mx, y0 = my, t0 = Date.now();
    for (;;) {
      const t = Math.min(1, (Date.now() - t0) / ms), e = ease(t);
      await page.mouse.move(x0 + (x - x0) * e, y0 + (y - y0) * e);
      if (t >= 1) break;
    }
    mx = x; my = y;
  }
  async function scrollTo(y, ms = 900) {
    await page.evaluate(([y, ms]) => new Promise((res) => {
      const y0 = scrollY, t0 = performance.now();
      const e = (t) => (t < 0.5 ? 2 * t * t : 1 - Math.pow(-2 * t + 2, 2) / 2);
      const step = (n) => { const t = Math.min(1, (n - t0) / ms); scrollTo(0, y0 + (y - y0) * e(t)); t < 1 ? requestAnimationFrame(step) : res(); };
      requestAnimationFrame(step);
    }), [Math.max(0, y), ms]);
  }
  async function absTop(loc) { return loc.evaluate((n) => n.getBoundingClientRect().top + scrollY); }
  async function scrollToEl(loc, offset = 170, ms = 1000) { await scrollTo((await absTop(loc)) - offset, ms); }
  async function center(loc) { const b = await loc.boundingBox(); return [b.x + b.width / 2, b.y + b.height / 2]; }
  async function hover(loc, ms = 700) { const [x, y] = await center(loc); await moveTo(x, y, ms); }
  async function click(loc, ms = 700) {
    await hover(loc, ms);
    const [x2, y2] = await center(loc);                       // the target may have moved while the cursor travelled (view transitions, smooth reveals)
    if (Math.hypot(x2 - mx, y2 - my) > 3) await moveTo(x2, y2, 160);
    await page.waitForTimeout(160);
    await page.mouse.down(); await page.waitForTimeout(70); await page.mouse.up(); await page.waitForTimeout(120);
  }
  const tab = (name) => page.locator(`#tabs a[data-route="${name}"]`);
  const settle = (ms = 800) => page.waitForTimeout(ms);       // a view fades and slides in for about 0.6s
  const card = (text) => page.locator("article.fc", { hasText: text });

  // ---------------------------------------------------------------- the script
  await page.goto(BASE, { waitUntil: "networkidle" });

  await beat(BEATS[0], async () => {                          // 0:00 the problem: headline, then the "noise" scene lines up
    await moveTo(1500, 800, 400);
    await until(4);                                           // let the headline words rise in
    const sc = await page.evaluate(() => { const s = document.querySelector("#scene-unravel"); return { top: s.offsetTop, h: s.offsetHeight }; });
    await scrollTo(sc.top - 60, 3000);                        // into the scene
    await until(8);
    await scrollTo(sc.top + sc.h * 0.55, 8500);               // slowly through it: noise drifts, then snaps onto rails
    await until(17.5);
  });

  await beat(BEATS[1], async () => {                          // 0:20 upload: drag the CSV onto the drop zone
    say('scroll to top'); await scrollTo(0, 1800); say('scrolled');
    await until(22.5);
    say('hover privacy'); await hover(page.locator(".privacy-line"), 900); say('hovered');          // the privacy note
    await until(25.5);
    say('to file source'); await moveTo(330, 940, 600);                              // "the file manager"
    await page.evaluate(() => window.__ghost(true));          // (no real button press: holding the mouse down starts a native drag that stalls the page)
    const drop = page.locator("#drop");
    const [dx, dy] = await center(drop);
    say('drag to zone'); await moveTo(dx, dy, 1300); say('over zone, dispatching drop');
    await page.evaluate(async (csv) => {
      const dz = document.querySelector("#drop");
      const dt = new DataTransfer(); dt.items.add(new File([csv], "demo_statement.csv", { type: "text/csv" }));
      for (const type of ["dragenter", "dragover"]) dz.dispatchEvent(new DragEvent(type, { dataTransfer: dt, bubbles: true, cancelable: true }));
      await new Promise((r) => setTimeout(r, 650));           // the zone lights up while hovering
      dz.dispatchEvent(new DragEvent("drop", { dataTransfer: dt, bubbles: true, cancelable: true }));
    }, csv);
    await page.evaluate(() => window.__ghost(false));
    say('dropped; waiting for overview'); await page.waitForURL("**/#/overview", { timeout: 20000 }); say('url ok');
    await page.waitForSelector(".ring-score");
    say('overview ready'); await moveTo(1250, 620, 800);
  });

  await beat(BEATS[2], async () => {                          // 0:40 detection: the picture, the formula, Netflix
    await until(46);
    await click(page.locator("#view-overview summary", { hasText: "How is this calculated?" }), 900);
    await until(54);
    await click(page.locator("#view-overview summary", { hasText: "How is this calculated?" }), 500);
    await until(55.5);
    await click(tab("subscriptions"), 900);
    await page.waitForSelector("#view-subscriptions table");
    const netflix = page.getByRole("button", { name: "Details for Netflix" });
    await scrollToEl(netflix, 260, 900);
    await click(netflix, 700);
    await page.waitForSelector("#view-subscriptions canvas");
    await scrollToEl(netflix, 200, 900);
  });

  await beat(BEATS[3], async () => {                          // 1:05 the flagged duplicate
    await click(tab("findings"), 900);
    await page.waitForSelector("article.fc");
    const dup = card("Streamly: 2 active subscriptions");
    await until(68);
    await scrollToEl(dup, 150, 1300);
    await until(77);
    await click(dup.getByText("Show data used"), 800);
    await until(80);
    await scrollTo((await absTop(dup)) + 120, 900);
  });

  await beat(BEATS[4], async () => {                          // 1:30 PayPal status: the panel, then the mismatch finding
    await click(tab("overview"), 900);
    await page.waitForSelector(".ring-score");
    const heading = page.getByRole("heading", { name: /PayPal status/ });
    await scrollToEl(heading, 150, 1500);
    await until(98);
    await hover(page.locator("#view-overview").locator("text=Still charged on your statement").first(), 900);
    await until(106);
    await click(tab("findings"), 900);
    await page.waitForSelector("article.fc");
    const fit = card("FitPass Gym: charged after PayPal cancelled");
    await scrollToEl(fit, 150, 900);
    await hover(fit.getByRole("link", { name: /Manage in PayPal/ }), 800);
  });

  await beat(BEATS[5], async () => {                          // 1:55 the drafted message
    const dup = card("Streamly: 2 active subscriptions");
    await scrollToEl(dup, 150, 1000);
    await click(dup.getByRole("button", { name: "Draft a message for me" }), 800);
    await dup.locator('textarea[aria-label="Message body"]').waitFor({ timeout: 8000 });
    await scrollTo((await absTop(dup.locator(".draft"))) - 130, 900);
    await until(125);
    await hover(dup.locator('textarea[aria-label="Message body"]'), 800);
    await until(128);
    await click(dup.getByRole("button", { name: "Copy message" }), 800);
    await dup.getByRole("button", { name: /Copied/ }).waitFor({ timeout: 4000 });
  });

  await beat(BEATS[6], async () => {                          // 2:15 one question, then the closing card
    await click(tab("ask"), 900);
    await page.waitForSelector("#view-ask h1");
    await settle();
    await click(page.getByRole("button", { name: "What do I spend on streaming?" }), 800);
    await page.waitForFunction(() => /25\.98/.test(document.querySelector("#view-ask").innerText), null, { timeout: 15000 });
    await until(141);
    const show = page.locator("#view-ask").getByText("Show data used").last();
    await click(show, 700);
    await until(146.2);
    await page.evaluate(() => {
      const c = document.createElement("div");
      c.style.cssText = "position:fixed;inset:0;z-index:2147483640;display:grid;place-content:center;gap:18px;text-align:center;background:radial-gradient(120% 90% at 50% 40%,#18234a,#070a18);color:#f4f6ff;opacity:0;transition:opacity .9s;font-family:Fraunces,Georgia,serif";
      c.innerHTML = '<div style="font-size:92px;letter-spacing:-.03em;font-weight:340">SubSweep</div>' +
        '<div style="font-size:34px;font-family:system-ui,sans-serif;color:#c4cbe8">Find what\'s quietly draining you. You decide.</div>' +
        '<div style="font-size:26px;font-family:system-ui,sans-serif;color:#ff9a86;margin-top:14px">github.com/ShlokVasandani/SubSweep</div>';
      document.documentElement.append(c);
      requestAnimationFrame(() => requestAnimationFrame(() => { c.style.opacity = "1"; }));
    });
  });

  log.total_seconds = +now().toFixed(2);
  const video = page.video();
  await page.close();
  await ctx.close();
  const raw = await video.path();
  const dest = path.join(OUT, "subsweep-demo.webm");
  fs.copyFileSync(raw, dest);
  fs.rmSync(path.join(OUT, "_raw"), { recursive: true, force: true });
  fs.writeFileSync(path.join(OUT, "recording_log.json"), JSON.stringify(log, null, 2));
  await browser.close();
  console.log(JSON.stringify({ video: dest, lead_ms: log.video_lead_ms, total_seconds: log.total_seconds, problems, overran: log.beats.filter((b) => b.overran).map((b) => b.label) }));
})().catch((e) => { console.error("RECORDING FAILED:", e.message); process.exit(1); });
