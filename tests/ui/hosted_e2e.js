// Real Chromium against several server processes behind a round-robin proxy (see rr_proxy.py): the closest local model of a serverless host.
// Run by tests/ui/test_hosted_ui.py:  node hosted_e2e.js <baseUrl> <fixturesDir> <sampleStatement.csv>
const { chromium } = require("playwright");
const path = require("path");
const BASE = process.argv[2], FX = process.argv[3], SAMPLE = process.argv[4];
const out = []; const rec = (name, ok, detail = "") => { out.push({ name, ok, detail }); console.log(`${ok ? "PASS" : "FAIL"}  ${name}${detail ? "  — " + detail : ""}`); };
(async () => {
  const browser = await chromium.launch();
  const ctx = await browser.newContext({ viewport: { width: 1440, height: 900 }, permissions: ["clipboard-read", "clipboard-write"] });
  const page = await ctx.newPage();
  const problems = [], api = [];
  page.on("pageerror", (e) => problems.push("pageerror: " + e.message));
  page.on("console", (m) => { if (m.type() === "error" && !/Failed to load resource/.test(m.text())) problems.push("console: " + m.text()); });
  page.on("response", (r) => { const u = new URL(r.url()); if (u.pathname.startsWith("/api/")) api.push({ path: u.pathname, status: r.status(), stateless: r.request().headers()["x-subsweep-stateless"] === "1" }); });
  const go = async (route) => { await page.click(`#tabs a[data-route="${route}"]`); await page.waitForSelector(`#view-${route} h1`); await page.waitForTimeout(500); };

  // ---------- 1. a real upload of a real statement file
  await page.goto(BASE, { waitUntil: "networkidle" }); await page.waitForTimeout(1200);
  const privacy = await page.locator(".privacy-line").innerText();
  rec("landing says what is true for the hosted build", /stays in this browser tab/.test(privacy) && /never stores or logs/.test(privacy) && !/processed on this computer/.test(privacy), privacy.slice(0, 60));
  rec("upload button is still there", await page.locator("#btn-choose").isVisible());
  await page.setInputFiles("#file", SAMPLE);
  await page.waitForURL("**/#/overview", { timeout: 30000 }); await page.waitForSelector(".ring-score"); await page.waitForTimeout(2200);
  const ov = await page.locator("#view-overview").innerText();
  rec("uploaded statement analysed", /10 active subscriptions/.test(ov) && /\$1,697/.test(ov), "10 subscriptions, $1,697 a year");
  const fnd = await (async () => { await go("findings"); return page.locator("#view-findings").innerText(); })();
  rec("no fictional PayPal data in your own statement", !/Demo data|PAYPAL_MODE/i.test(ov) && !/PayPal mismatch|charged after PayPal cancelled/i.test(fnd), "no 'Demo data' label, no PayPal-mismatch finding (6 findings, not 7)");
  await go("overview");
  rec("source badge shows the uploaded file name", /demo_statement\.csv/.test(await page.locator("#badge-source").innerText()));

  await go("subscriptions");
  await page.getByRole("button", { name: "Details for Netflix" }).click();
  await page.waitForSelector("#view-subscriptions canvas", { timeout: 20000 });
  rec("history chart loads (a stateless call)", /charge history/i.test(await page.locator("#view-subscriptions").innerText()));

  await go("findings");
  const dup = page.locator("article.fc", { hasText: "Streamly: 2 active subscriptions" }); await dup.waitFor({ timeout: 15000 });
  await dup.getByText("Show data used").click();
  rec("explanation + data used", /99\.99/.test(await dup.innerText()));
  await dup.getByRole("button", { name: "Draft a message for me" }).click();
  const body = dup.locator('textarea[aria-label="Message body"]'); await body.waitFor({ timeout: 20000 });
  rec("draft is generated (and never sent)", /\[ACCOUNT NAME\]/.test(await body.inputValue()));
  const countBefore = await page.locator("#count-findings").innerText();
  await dup.getByRole("button", { name: /Dismiss/ }).click(); await page.waitForTimeout(1500);
  rec("dismiss updates the page", (await page.locator("#view-findings").innerText()).toLowerCase().includes("dismissed"), `findings badge ${countBefore} -> ${await page.locator("#count-findings").innerText().catch(() => "?")}`);
  await page.getByRole("button", { name: /Undo/ }).first().click(); await page.waitForTimeout(1500);
  rec("undo restores it", /Draft a message for me|Dismiss/.test(await dup.innerText()));

  await go("ask");
  await page.getByRole("button", { name: "What do I spend on streaming?" }).click();
  await page.waitForFunction(() => /\$\d/.test(document.querySelector("#view-ask").innerText.split("What do I spend on streaming?").pop()), null, { timeout: 25000 });
  rec("ask a question about your own data", /Hulu/.test(await page.locator("#view-ask").innerText()));

  // ---------- 2. a reload loses the statement (it lived only in the tab), and the page says so cleanly
  await page.reload({ waitUntil: "networkidle" }); await page.waitForTimeout(1500);
  rec("after a reload the statement is gone (nothing stored server-side)", await page.locator("#btn-demo").isVisible() && /#\/?$/.test(page.url()));

  // ---------- 3. the sample statement
  await page.click("#btn-demo"); await page.waitForURL("**/#/overview", { timeout: 30000 }); await page.waitForSelector(".ring-score"); await page.waitForTimeout(1800);
  const sample = await page.locator("#view-overview").innerText();
  rec("sample statement works, PayPal panel labelled demo data", /10 active subscriptions/.test(sample) && /Demo data/.test(sample));
  await go("findings");
  rec("sample has its seven findings incl. the PayPal mismatch", /FitPass Gym: charged after PayPal cancelled/.test(await page.locator("#view-findings").innerText()));

  // ---------- 4. the column mapper (the mapping must survive being re-sent with every action)
  await page.goto(BASE, { waitUntil: "networkidle" }); await page.waitForTimeout(800);
  await page.setInputFiles("#file", path.join(FX, "odd_headers.csv"));
  await page.waitForSelector("#mapper[open]", { timeout: 15000 });
  await page.selectOption("#map-date", "When"); await page.selectOption("#map-description", "What"); await page.selectOption("#map-amount", "HowMuch");
  await page.click("#mapper-go"); await page.waitForURL("**/#/overview", { timeout: 30000 }); await page.waitForSelector(".ring-score");
  await go("subscriptions");
  await page.getByRole("button", { name: "Details for Netflix" }).click(); await page.waitForSelector("#view-subscriptions canvas", { timeout: 20000 });
  rec("mapped statement keeps working on later actions", /charge history/i.test(await page.locator("#view-subscriptions").innerText()));

  // ---------- 5. friendly errors
  await page.goto(BASE, { waitUntil: "networkidle" }); await page.waitForTimeout(800);
  await page.setInputFiles("#file", path.join(FX, "notes.pdf.csv")); await page.waitForSelector("#drop-error:not([hidden])", { timeout: 10000 });
  rec("a non-CSV file gets a friendly message", /binary|PDF|CSV/i.test(await page.locator("#drop-error").innerText()));

  await page.goto(BASE, { waitUntil: "networkidle" }); await page.waitForTimeout(500);
  const before = api.length;
  await page.setInputFiles("#file", path.join(FX, "big.csv")); await page.waitForSelector("#drop-error:not([hidden])", { timeout: 10000 });
  rec("a file over the limit is stopped in the browser with a clear message", /4 MB/.test(await page.locator("#drop-error").innerText()) && api.filter((a) => a.path === "/api/analyze").length === api.slice(0, before).filter((a) => a.path === "/api/analyze").length, "no request was sent");

  // ---------- 6. nothing went wrong in the console, and every API call succeeded
  const bad = api.filter((a) => a.status >= 400 && !(a.path === "/api/analysis" && a.status === 404) && !(a.path === "/api/analyze" && [415, 422].includes(a.status)));
  rec("no console errors or page errors", problems.length === 0, problems.slice(0, 3).join(" | "));
  rec("every API call succeeded (except the expected 404/415/422)", bad.length === 0, bad.slice(0, 4).map((b) => `${b.status} ${b.path}`).join(", "));
  rec("stateless requests were really used", api.filter((a) => a.stateless).length >= 8, `${api.filter((a) => a.stateless).length} stateless calls`);
  await browser.close();
  console.log("RESULT " + JSON.stringify({ passed: out.filter((o) => o.ok).length, failed: out.filter((o) => !o.ok).length }));
  process.exit(out.some((o) => !o.ok) ? 1 : 0);
})().catch((e) => { console.error("CRASH", e.message); process.exit(2); });
