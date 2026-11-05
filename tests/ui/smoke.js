// Browser checks for the SubSweep UI. Run by tests/ui/test_ui.py:  node smoke.js <baseUrl> <fixturesDir>
// Prints one JSON document: { results: [{ name, ok, detail }] }.  Every check gets a fresh page so they cannot affect each other.
const { chromium, request } = require("playwright");
const path = require("path");
const [BASE, FIX] = process.argv.slice(2);
const results = [];

async function resetDecisions() {            // decisions persist in SQLite on purpose; start every check from a clean slate
  const api = await request.newContext({ baseURL: BASE });
  await api.post("api/analyze", { multipart: { demo: "true" } });
  await api.delete("api/dismissals");
  await api.dispose();
}

async function check(browser, name, fn, ctxOpts = {}) {
  await resetDecisions();
  const ctx = await browser.newContext({ viewport: { width: 1280, height: 860 }, ...ctxOpts });
  const page = await ctx.newPage();
  const problems = [];
  page.on("console", (m) => { if (["error", "warning"].includes(m.type())) problems.push(`${m.type()}: ${m.text()}`); });
  page.on("pageerror", (e) => problems.push("pageerror: " + e.message));
  page.on("requestfailed", (r) => { if (!r.failure()?.errorText?.includes("ABORTED")) problems.push("requestfailed: " + r.url()); });
  try {
    const detail = await fn(page, ctx, problems);
    const bad = problems.filter((p) => !(ctxOpts.allowProblems || []).some((a) => p.includes(a)));
    results.push({ name, ok: bad.length === 0, detail: bad.length ? bad.join(" | ") : detail ?? "" });
  } catch (e) {
    results.push({ name, ok: false, detail: String(e.message || e).split("\n")[0] });
  } finally { await ctx.close(); }
}
const eq = (a, b, what) => { if (a !== b) throw new Error(`${what}: expected ${JSON.stringify(b)}, got ${JSON.stringify(a)}`); };
const ok = (c, what) => { if (!c) throw new Error(what); };
const demo = async (page) => { await page.goto(BASE, { waitUntil: "networkidle" }); await page.click("#btn-demo"); await page.waitForURL("**/#/overview", { timeout: 15000 }); await page.waitForSelector(".ring-score"); };
const go = async (page, route) => { await page.click(`#tabs a[data-route="${route}"]`); await page.waitForSelector(`#view-${route} h1`); };

(async () => {
  const browser = await chromium.launch();

  await check(browser, "landing_loads_clean", async (page) => {
    await page.goto(BASE, { waitUntil: "networkidle" });
    ok((await page.title()).startsWith("SubSweep"), "title");
    ok((await page.textContent("h1")).replace(/\s+/g, " ").includes("Your money is leaving quietly"), "headline");
    eq(await page.evaluate(() => document.fonts.check('16px "Fraunces"')), true, "Fraunces font loaded");
    eq(await page.evaluate(() => typeof window.Chart), "function", "vendored Chart.js loaded");
  });

  await check(browser, "skip_link_and_keyboard", async (page) => {
    await page.goto(BASE, { waitUntil: "networkidle" });
    await page.keyboard.press("Tab");
    eq(await page.evaluate(() => document.activeElement.className), "skip", "first tab stop");
    await page.keyboard.press("Enter");
    eq(await page.evaluate(() => document.activeElement.id), "main", "skip link moves focus to main");
    // keep tabbing until the upload buttons are reached, checking that each stop has a visible focus ring
    const seen = [];
    for (let i = 0; i < 14; i++) {
      await page.keyboard.press("Tab");
      const info = await page.evaluate(() => { const a = document.activeElement; const cs = getComputedStyle(a); return { id: a.id, text: (a.innerText || a.getAttribute("aria-label") || "").slice(0, 30), outline: cs.outlineStyle !== "none" && parseFloat(cs.outlineWidth) >= 2 }; });
      seen.push(info);
      if (info.id === "btn-demo") break;
    }
    ok(seen.some((s) => s.id === "btn-choose") && seen.some((s) => s.id === "btn-demo"), "upload buttons reachable by keyboard: " + JSON.stringify(seen.map((s) => s.id || s.text)));
    ok(seen.filter((s) => s.id === "btn-choose" || s.id === "btn-demo").every((s) => s.outline), "visible focus ring on the upload buttons");
  });

  await check(browser, "theme_toggle_persists", async (page) => {
    await page.goto(BASE, { waitUntil: "networkidle" });
    eq(await page.evaluate(() => document.documentElement.dataset.theme), "night", "default theme");
    await page.click("[data-theme-toggle]");
    eq(await page.evaluate(() => document.documentElement.dataset.theme), "day", "after toggle");
    eq(await page.getAttribute("[data-theme-toggle]", "aria-pressed"), "true", "aria-pressed");
    // the background must actually be re-painted in the day palette (light), with dark text on it, and back again
    const bgLum = () => page.evaluate(() => { const [r, g, b] = getComputedStyle(document.documentElement).getPropertyValue("--bg-a").match(/\d+/g).map(Number); return (0.2126 * r + 0.7152 * g + 0.0722 * b) / 255; });
    const inkLum = () => page.evaluate(() => { const [r, g, b] = getComputedStyle(document.body).color.match(/\d+/g).map(Number); return (0.2126 * r + 0.7152 * g + 0.0722 * b) / 255; });
    ok((await bgLum()) > 0.7, "day background is light, got " + (await bgLum()));
    ok((await inkLum()) < 0.2, "day text is dark");
    await page.reload({ waitUntil: "networkidle" });
    ok((await bgLum()) > 0.7, "day background is light after reload");
    eq(await page.evaluate(() => document.documentElement.dataset.theme), "day", "persisted");
    await page.click("[data-theme-toggle]");
    eq(await page.evaluate(() => document.documentElement.dataset.theme), "night", "toggled back");
    ok((await bgLum()) < 0.15, "night background is dark again, got " + (await bgLum()));
    ok((await inkLum()) > 0.8, "night text is light again");
  });

  await check(browser, "scroll_changes_the_theme_and_unravels", async (page) => {
    await page.goto(BASE, { waitUntil: "networkidle" });
    await page.waitForTimeout(500);
    const moodAt = async (y) => { await page.evaluate((y) => scrollTo(0, y), y); await page.waitForTimeout(450); return page.evaluate(() => getComputedStyle(document.documentElement).getPropertyValue("--bg-b").trim() + "|" + getComputedStyle(document.documentElement).getPropertyValue("--glow-1").trim()); };
    const geo = await page.evaluate(() => { const g = (s) => { const e = document.querySelector(s); return [e.getBoundingClientRect().top + scrollY, e.offsetHeight]; }; return { un: g("#scene-unravel"), fi: g("#scene-findings"), vh: innerHeight }; });
    const top = await moodAt(0), mid = await moodAt(geo.un[0] + 0.5 * (geo.un[1] - geo.vh)), ember = await moodAt(geo.fi[0] + 0.5 * (geo.fi[1] - geo.vh)), end = await moodAt(1e6);
    ok(new Set([top, mid, ember, end]).size === 4, "the background mood differs at each scroll position: " + JSON.stringify([top, mid, ember, end]));
    await page.evaluate(([t, h, vh]) => scrollTo(0, t + 0.97 * (h - vh)), [geo.un[0], geo.un[1], geo.vh]); await page.waitForTimeout(500);
    ok((await page.locator(".tx.hot").count()) >= 2, "price hike and duplicate chips light up at the end of the unravel");
    eq(await page.locator('.cap.on[data-cap="3"]').count(), 1, "final caption active");
    await page.evaluate(() => scrollTo(0, 0)); await page.waitForTimeout(500);
    eq(await page.locator(".tx.hot").count(), 0, "scrubbing back un-flags them (the animation is driven by scroll, not time)");
  });

  await check(browser, "reduced_motion_shows_finished_states", async (page) => {
    await page.goto(BASE, { waitUntil: "networkidle" });
    eq(await page.evaluate(() => getComputedStyle(document.querySelector(".pin")).position), "relative", "no pinning");
    eq(await page.evaluate(() => getComputedStyle(document.querySelector("#scene-unravel")).height !== "0px"), true, "scene is laid out");
    ok(await page.evaluate(() => [...document.querySelectorAll("[data-split] .w")].every((w) => getComputedStyle(w).opacity === "1")), "headline words are visible without animation");
    ok(await page.evaluate(() => [...document.querySelectorAll(".reveal")].every((r) => getComputedStyle(r).opacity === "1")), "reveal elements are visible");
    ok((await page.locator(".tx.hot").count()) >= 2, "stage is drawn in its finished state");
    ok(await page.evaluate(() => [...document.querySelectorAll(".fcard")].every((c) => getComputedStyle(c).position === "relative")), "finding cards are a plain list");
  }, { reducedMotion: "reduce" });

  await check(browser, "demo_flow_and_overview", async (page) => {
    await demo(page);
    await page.waitForFunction(() => document.querySelector(".ring-score").textContent === "45", null, { timeout: 8000 });
    eq(await page.textContent("#badge-source"), "Sample statement", "source badge");
    eq(await page.textContent("#count-findings"), "7", "open findings badge");
    ok((await page.textContent(".ov-sub")).includes("10 active subscriptions"), "summary line");
    eq(await page.getAttribute(".ring", "role"), "img", "ring has a role");
    ok((await page.getAttribute(".ring", "aria-label")).includes("45 out of 100"), "ring label");
    await page.locator(".panel", { hasText: "PayPal status" }).scrollIntoViewIfNeeded();
    ok((await page.locator(".pp-row").count()) === 4 && (await page.textContent(".pp-row.alert")).includes("Still charged"), "PayPal panel flags the mismatch");
    ok((await page.locator(".demo-badge", { hasText: "Demo data" }).count()) === 1, "demo data is labelled");
  });

  await check(browser, "no_horizontal_scroll_at_any_width", async (page) => {
    const bad = [];
    for (const w of [375, 768, 1440]) {
      await page.setViewportSize({ width: w, height: 800 });
      await page.goto(BASE, { waitUntil: "networkidle" });
      const wide = async (label) => { const [sw, iw] = await page.evaluate(() => [document.documentElement.scrollWidth, innerWidth]); if (sw > iw + 1) bad.push(`${label}@${w}: ${sw}>${iw}`); };
      await wide("landing");
      if (!(await page.locator("#view-overview h1").count())) { await page.click("#btn-demo"); await page.waitForURL("**/#/overview"); await page.waitForSelector(".ring-score"); }
      await wide("overview");
      for (const r of ["subscriptions", "findings", "ask"]) { await page.evaluate((r) => { location.hash = "#/" + r; }, r); await page.waitForSelector(`#view-${r} h1`); await page.waitForTimeout(300); await wide(r); }
    }
    ok(bad.length === 0, bad.join(", "));
  });

  // Display type has tight line-height, so a word's box is shorter than its ink (descenders hang below it). That is only safe while nothing in the
  // headline's chain clips. A future "overflow: hidden" line mask for the reveal would cut the g in "leaving" and the y in "quietly".
  await check(browser, "hero_descenders_not_clipped", async (page) => {
    const bad = [];
    for (const w of [375, 768, 1440]) {
      await page.setViewportSize({ width: w, height: 800 });
      await page.goto(BASE, { waitUntil: "networkidle" });
      await page.waitForFunction(() => [...document.querySelectorAll("#hero-title .w")].every((n) => getComputedStyle(n).opacity === "1"), null, { timeout: 8000 });
      const r = await page.evaluate(async () => {
        const h = document.querySelector("#hero-title");
        await document.fonts.ready;
        const clippers = [];
        for (let n = h; n && n !== document.body; n = n.parentElement) {   // body is the page scroller (overflow-x:hidden makes its overflow-y compute to auto) and cannot clip the headline
          const cs = getComputedStyle(n);
          if (cs.overflowY !== "visible" || cs.clipPath !== "none" || /paint|content|strict/.test(cs.contain)) clippers.push(n.tagName + "." + n.className);
        }
        for (const n of h.querySelectorAll(".w, em")) {
          const cs = getComputedStyle(n);
          if (cs.overflowY !== "visible" || cs.clipPath !== "none") clippers.push("word:" + n.textContent);
        }
        const words = [...h.querySelectorAll(".w")].map((n) => ({ t: n.textContent, h: n.getBoundingClientRect().height, lh: parseFloat(getComputedStyle(n).lineHeight) || 0 }));
        const lines = new Set([...h.querySelectorAll(".w")].map((n) => Math.round(n.getBoundingClientRect().top)));
        return { clippers, words, lines: lines.size };
      });
      if (r.clippers.length) bad.push(`${w}px clipped by ${r.clippers.join(", ")}`);
      for (const word of r.words) if (word.h + 1 < word.lh) bad.push(`${w}px "${word.t}" box ${word.h.toFixed(1)} < line-height ${word.lh.toFixed(1)}`);
      // the pixels just under the baseline of the descender words must actually contain ink (the g and y are drawn, not cut flat)
      const box = await page.evaluate(() => { const ws = [...document.querySelectorAll("#hero-title .w")]; const out = {}; for (const n of ws) if (/leaving|quietly/.test(n.textContent)) { const b = n.getBoundingClientRect(); out[n.textContent] = { x: b.left, y: b.top, w: b.width, h: b.height, fs: parseFloat(getComputedStyle(n).fontSize) }; } return out; });
      for (const [word, b] of Object.entries(box)) {
        const y0 = Math.max(0, b.y + b.h - b.fs * 0.02);           // from just inside the box bottom downwards: the hanging part of g / y
        const shot = await page.screenshot({ clip: { x: Math.max(0, b.x), y: y0, width: Math.min(b.w, w - Math.max(0, b.x)), height: Math.max(2, b.fs * 0.2) } });
        const flat = await page.evaluate(async (b64) => {
          const img = new Image(); img.src = "data:image/png;base64," + b64; await img.decode();
          const c = document.createElement("canvas"); c.width = img.width; c.height = img.height; const g = c.getContext("2d"); g.drawImage(img, 0, 0);
          const d = g.getImageData(0, 0, c.width, c.height).data; let lo = 255, hi = 0;
          for (let i = 0; i < d.length; i += 4) { const l = (d[i] + d[i + 1] + d[i + 2]) / 3; lo = Math.min(lo, l); hi = Math.max(hi, l); }
          return hi - lo < 40;                                     // no contrast at all in the band = nothing drawn there
        }, shot.toString("base64"));
        if (flat) bad.push(`${w}px no descender ink under "${word}"`);
      }
    }
    ok(bad.length === 0, bad.join(" | "));
  });

  // The 2:30 demo video script (docs/DEMO_SCRIPT.md) is only useful if every click and every figure it mentions is really on screen.
  await check(browser, "demo_video_script_walkthrough", async (page) => {
    await page.goto(BASE, { waitUntil: "networkidle" });
    await page.setInputFiles("#file", path.join(FIX, "demo_statement.csv"));                          // beat 2: upload the statement
    await page.waitForURL("**/#/overview", { timeout: 15000 }); await page.waitForSelector(".ring-score");
    await page.waitForTimeout(2200);
    const ov = await page.locator("#view-overview").innerText();                                    // beat 3: the picture
    ok(/\$1,697/.test(ov) && /10 active subscriptions/.test(ov) && /\b45\b/.test(ov), "overview shows $1,697, 10 active subscriptions and a score of 45");
    await page.getByText("How is this calculated?").click();
    await go(page, "subscriptions");
    await page.getByRole("button", { name: "Details for Netflix" }).click();
    await page.waitForSelector("#view-subscriptions canvas", { timeout: 8000 });
    ok(/charge history/i.test(await page.locator("#view-subscriptions").innerText()) && /confidence/i.test(await page.locator("#view-subscriptions .detail:not([hidden])").innerText()), "Netflix row shows the chart and the confidence reason");
    await go(page, "findings");                                                                      // beat 4: the duplicate
    const dup = page.locator("article.fc", { hasText: "Streamly: 2 active subscriptions" });
    await dup.waitFor({ timeout: 8000 });
    await dup.getByText("Show data used").click();
    ok(/99\.99/.test(await dup.innerText()), "the duplicate card shows its data, including $99.99");
    const fit = page.locator("article.fc", { hasText: "FitPass Gym: charged after PayPal cancelled" });  // beat 5: PayPal check
    ok(await fit.getByRole("link", { name: /Manage in PayPal/ }).isVisible(), "the PayPal mismatch card links to PayPal");
    ok(/\$48\.00/.test(await fit.innerText()) && /11 Aug 2026/.test(await fit.innerText()), "the mismatch card names the 11 Aug cancellation and $48.00 charged since");
    await dup.getByRole("button", { name: "Draft a message for me" }).click();                       // beat 6: the draft
    const body = dup.locator('textarea[aria-label="Message body"]'); await body.waitFor({ timeout: 8000 });
    ok(/\[ACCOUNT NAME\]/.test(await body.inputValue()), "the draft keeps the [ACCOUNT NAME] placeholder");
    await dup.getByRole("button", { name: "Copy message" }).click();
    await dup.getByRole("button", { name: /Copied/ }).waitFor({ timeout: 4000 });
    await go(page, "ask");                                                                           // beat 7: one question
    await page.getByRole("button", { name: "What do I spend on streaming?" }).click();
    await page.waitForFunction(() => /25\.98/.test(document.querySelector("#view-ask").innerText), null, { timeout: 15000 });
    const ask = await page.locator("#view-ask").innerText();
    ok(/Hulu/.test(ask) && /Netflix/.test(ask) && /Show data used/.test(ask), "the answer names Hulu and Netflix and offers Show data used");
    return "every beat of the script is on screen";
  }, { permissions: ["clipboard-read", "clipboard-write"] });

  // Browsers can refuse clipboard access (permission denied, or an insecure page). That must never be an unhandled error or a silent no-op.
  await check(browser, "copy_message_degrades_gracefully", async (page) => {
    await demo(page); await go(page, "findings");
    const dup = page.locator("article.fc", { hasText: "Streamly: 2 active subscriptions" });
    await dup.getByRole("button", { name: "Draft a message for me" }).click();
    await dup.locator('textarea[aria-label="Message body"]').waitFor({ timeout: 8000 });
    await dup.getByRole("button", { name: "Copy message" }).click();
    await page.waitForFunction(() => /Copied/.test(document.querySelector("#view-findings").innerText) || /Couldn.t copy/.test(document.querySelector("#toasts").innerText), null, { timeout: 4000 });
    return "either copied or told the user";          // check() itself fails the test on any page error
  });

  await check(browser, "subscriptions_sort_filter_expand", async (page) => {
    await demo(page); await go(page, "subscriptions");
    eq(await page.locator("tbody tr.row").count(), 10, "ten rows");
    const price = page.locator("th button", { hasText: "Price" });
    await price.click();
    eq(await page.locator("th", { hasText: "Price" }).getAttribute("aria-sort"), "descending", "aria-sort after click");
    eq(await page.locator("tbody tr.row .merchant b").first().textContent(), "Streamly Annual", "highest price first");
    await price.click();
    eq(await page.locator("th", { hasText: "Price" }).getAttribute("aria-sort"), "ascending", "toggles");
    await page.fill("#subs-q", "netflix");
    eq((await page.textContent(".count-line")).trim(), "Showing 1 of 10", "search narrows");
    await page.fill("#subs-q", "zzzz");
    ok((await page.textContent("tbody")).includes("Nothing matches those filters"), "empty filter state");
    await page.click("text=Clear filters");
    eq(await page.locator("tbody tr.row").count(), 10, "filters cleared");
    await page.fill("#subs-q", "netflix");
    const btn = page.locator('button[aria-label="Details for Netflix"]');
    await btn.focus(); await page.keyboard.press("Enter");
    eq(await btn.getAttribute("aria-expanded"), "true", "keyboard expands the row");
    await page.waitForSelector("tr.detail canvas");
    ok((await page.getAttribute("tr.detail canvas", "aria-label")).includes("Netflix"), "chart has alt text");
    ok((await page.locator("tr.detail summary", { hasText: "View chart data" }).count()) === 1, "chart data table offered");
  });

  await check(browser, "findings_ai_dismiss_undo_draft", async (page) => {
    await demo(page); await go(page, "findings");
    await page.waitForSelector(".ai-text");
    await page.waitForFunction(() => document.querySelectorAll(".ai .chip").length > 0, null, { timeout: 15000 });
    ok((await page.locator("details.disclosure summary", { hasText: "Show data used" }).count()) > 0, "Show data used toggle");
    eq(await page.locator(".sev").count() >= 2, true, "grouped by severity");
    const first = page.locator(".fc").first();
    const title = await first.locator("h3").textContent();
    await first.locator("button", { hasText: "Dismiss" }).click();
    await page.waitForSelector(".toast");
    ok((await page.textContent(".toast")).includes("Undo"), "undo offered");
    eq(await page.textContent("#count-findings"), "6", "badge decremented");
    ok((await page.locator(".dismissed-box").count()) === 1, "dismissed section appears");
    await page.click(".toast button");
    await page.waitForFunction(() => document.querySelector("#count-findings").textContent === "7");
    eq(await page.locator(".fc h3").first().textContent(), title, "restored to its place");
    const dup = page.locator(".fc", { hasText: "Streamly" }).first();
    await dup.locator("button", { hasText: "Draft a message" }).click();
    await page.waitForSelector(".draft textarea");
    const body = await page.inputValue(".draft textarea");
    ok(body.includes("[ACCOUNT NAME]") && body.includes("[ORDER / TRANSACTION DETAILS]") && body.includes("[YOUR NAME]"), "draft has placeholders");
    ok((await page.textContent(".draft .note")).includes("never sends"), "never-sent notice");
    ok(!(await page.locator("button", { hasText: /^Send/i }).count()), "there is no send button anywhere");
  });

  await check(browser, "still_using_only_on_forgotten", async (page) => {
    await demo(page); await go(page, "findings");
    const forgotten = page.locator(".fc", { hasText: "Hulu" }).first();
    ok((await forgotten.locator("button", { hasText: "Still using it" }).count()) === 1, "forgotten has Still using");
    ok((await forgotten.textContent()).includes("A guess"), "labelled as a guess");
    ok((await page.locator(".fc", { hasText: "price up" }).first().locator("button", { hasText: "Still using it" }).count()) === 0, "price hike does not");
    await forgotten.locator("button", { hasText: "Still using it" }).click();
    await page.waitForSelector(".toast");
    ok((await page.textContent(".toast")).includes("Hulu"), "confirmation names it");
  });

  await check(browser, "ask_flow_and_guardrails", async (page) => {
    await demo(page); await go(page, "ask");
    await page.click('button:has-text("Which subscription went up the most this year?")');
    await page.waitForSelector(".msg.bot .answer");
    ok((await page.textContent(".msg.bot .answer")).includes("Netflix"), "answered from data");
    ok((await page.locator(".msg.bot summary", { hasText: "Show data used" }).count()) === 1, "Show data used on answers");
    await page.fill('textarea[aria-label="Your question"]', "Which stocks should I buy?");
    await page.keyboard.press("Enter");
    await page.waitForFunction(() => document.querySelectorAll(".msg.bot").length === 2);
    ok((await page.locator(".msg.bot .answer").nth(1).textContent()).includes("can't give investment advice"), "investment advice refused");
    await page.fill('textarea[aria-label="Your question"]', "What do I spend on gaming?");
    await page.keyboard.press("Enter");
    await page.waitForFunction(() => document.querySelectorAll(".msg.bot").length === 3);
    ok((await page.locator(".msg.bot .answer").nth(2).textContent()).includes("can't find anything"), "no-data answer");
    eq(await page.getAttribute(".chat", "role"), "log", "chat is a live log");
  });

  await check(browser, "upload_real_file_and_error_states", async (page) => {
    await page.goto(BASE, { waitUntil: "networkidle" });
    await page.setInputFiles("#file", path.join(FIX, "notes.pdf"));
    await page.waitForSelector("#drop-error:not([hidden])");
    ok((await page.textContent("#drop-error")).includes(".csv"), "wrong type: friendly message");
    await page.setInputFiles("#file", path.join(FIX, "fake.csv"));               // PDF bytes with a .csv name
    await page.waitForFunction(() => /binary/.test(document.querySelector("#drop-error").textContent), null, { timeout: 8000 });
    await page.route("**/api/analyze", (r) => r.abort());
    await page.click("#btn-demo");
    await page.waitForFunction(() => /couldn't reach SubSweep/.test(document.querySelector("#drop-error").textContent), null, { timeout: 8000 });
    await page.unroute("**/api/analyze");
    await page.setInputFiles("#file", path.join(FIX, "demo_statement.csv"));
    await page.waitForURL("**/#/overview", { timeout: 15000 });
    eq(await page.textContent("#badge-source"), "demo_statement.csv", "filename shown");
  }, { allowProblems: ["Failed to load resource", "net::ERR_FAILED", "/api/analyze"] });

  await check(browser, "column_mapper_dialog", async (page) => {
    await page.goto(BASE, { waitUntil: "networkidle" });
    await page.setInputFiles("#file", path.join(FIX, "odd_headers.csv"));
    await page.waitForSelector("#mapper[open]", { timeout: 8000 });
    ok((await page.textContent("#mapper-msg")).includes("Pick them below"), "explains what to do");
    await page.selectOption("#map-date", "When"); await page.selectOption("#map-description", "What"); await page.selectOption("#map-amount", "HowMuch");
    await page.click("#mapper-go");
    await page.waitForURL("**/#/overview", { timeout: 15000 });
    await page.waitForSelector(".ring-score");
    ok((await page.textContent(".ov-sub")).includes("active subscription"), "analysis completed");
  }, { allowProblems: ["Failed to load resource"] });

  await check(browser, "empty_state_for_short_statements", async (page) => {
    await page.goto(BASE, { waitUntil: "networkidle" });
    await page.setInputFiles("#file", path.join(FIX, "tiny.csv"));
    await page.waitForURL("**/#/overview", { timeout: 15000 });
    await page.waitForSelector("#ov-title");
    ok((await page.textContent("#view-overview")).includes("No repeat charges found"), "friendly empty state");
    ok((await page.locator("#view-overview button", { hasText: "Try another statement" }).count()) === 1, "offers a way forward");
  }, { allowProblems: ["Failed to load resource"] });

  await check(browser, "accessible_names_and_headings", async (page) => {
    await demo(page);
    const audit = async (view) => page.evaluate((view) => {
      const root = document.querySelector("#view-" + view), bad = [];
      for (const n of root.querySelectorAll("button, a[href], input, select, textarea")) {
        if (n.getClientRects().length === 0) continue;                       // not rendered (e.g. inside a closed <details>)
        const name = (n.getAttribute("aria-label") || n.innerText || n.labels?.[0]?.innerText || n.getAttribute("title") || n.getAttribute("placeholder") || "").trim();
        if (!name) bad.push(n.outerHTML.slice(0, 80));
      }
      for (const c of root.querySelectorAll("canvas")) if (c.getAttribute("role") !== "img" || !c.getAttribute("aria-label")) bad.push("canvas without alt");
      const h1 = root.querySelectorAll("h1").length;
      if (h1 !== 1) bad.push("h1 count " + h1);
      return bad;
    }, view);
    const bad = [];
    for (const v of ["overview", "subscriptions", "findings", "ask"]) { if (v !== "overview") await go(page, v); await page.waitForTimeout(400); bad.push(...(await audit(v)).map((b) => v + ": " + b)); }
    await go(page, "subscriptions"); await page.click('button[aria-label="Details for Netflix"]'); await page.waitForSelector("tr.detail canvas");
    bad.push(...(await audit("subscriptions")).map((b) => "subs-expanded: " + b));
    ok(bad.length === 0, bad.join(" | "));
    eq(await page.evaluate(() => document.querySelectorAll("main, header, nav").length >= 3), true, "landmarks");
    eq(await page.evaluate(() => document.documentElement.lang), "en", "lang");
  });

  await check(browser, "hostile_text_cannot_run_script", async (page) => {
    await page.goto(BASE, { waitUntil: "networkidle" });
    await page.setInputFiles("#file", path.join(FIX, "<img src=x onerror=window.__pwned=1>.csv"));
    await page.waitForURL("**/#/overview", { timeout: 15000 });
    await page.waitForSelector(".ring-score");
    await go(page, "subscriptions");
    await page.waitForSelector("tbody tr.row");
    eq(await page.evaluate(() => window.__pwned), undefined, "no script ran");
    ok(await page.evaluate(() => document.body.innerText.includes("onerror")), "the hostile text is shown as plain text");
    eq(await page.locator("tbody img").count(), 0, "no injected elements");
    ok((await page.textContent("#badge-source")).includes("<img"), "hostile filename shown as text too");
  }, { allowProblems: ["Failed to load resource"] });

  await check(browser, "mobile_navigation", async (page) => {
    await page.setViewportSize({ width: 390, height: 844 });
    await demo(page);
    eq(await page.isVisible("#tabbar"), true, "bottom tab bar visible");
    eq(await page.isVisible("#tabs"), false, "top tabs hidden");
    await page.click('#tabbar a[data-route="findings"]'); await page.waitForSelector("#view-findings h1");
    eq(await page.getAttribute('#tabbar a[data-route="findings"]', "aria-current"), "page", "current tab marked");
    await page.click('#tabbar a[data-route="subscriptions"]'); await page.waitForSelector("#view-subscriptions h1");
    ok(await page.evaluate(() => getComputedStyle(document.querySelector("table.subs tbody")).display === "block"), "table becomes cards on phones");
  }, { viewport: { width: 390, height: 844 }, isMobile: true, hasTouch: true });

  await check(browser, "forget_statement", async (page) => {
    await demo(page);
    await page.click("text=Remove this statement from memory");
    await page.waitForLoadState("networkidle");
    await page.goto(BASE + "#/overview");
    await page.waitForFunction(() => document.querySelector("#view-landing").hidden === false, null, { timeout: 8000 });
  }, { allowProblems: ["Failed to load resource"] });

  await browser.close();
  console.log(JSON.stringify({ results }));
})().catch((e) => { console.log(JSON.stringify({ fatal: String(e.stack || e) })); process.exit(1); });
