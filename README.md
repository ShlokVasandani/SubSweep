# SubSweep

Find and clean up forgotten, duplicated, and price-hiked subscriptions from a bank or PayPal statement.
SubSweep never cancels anything or sends any message on your behalf.

> Work in progress for the PayPal AI Hackathon. Full README arrives in Phase 8.

## Quick start

```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
python -m subsweep                                       # then open http://127.0.0.1:8000 and click "Try the sample data"
python -m subsweep.ingest sample_data/card_split.csv     # parse + clean a statement
python -m subsweep.detect my_statement.csv --excluded    # find recurring charges, show what was excluded
python -m subsweep.findings my_statement.csv             # findings, savings and health score
python -m subsweep.ai my_statement.csv --explain --ask "What do I spend on streaming?"   # AI layer (works without a key)
python -m pytest
```

PayPal sandbox commands are in [PayPal integration](#paypal-integration-sandbox) below.

## How detection works

Recurring-charge detection is plain, explainable Python (no ML). Every threshold and keyword list lives in
[`config/rules.yaml`](config/rules.yaml); edit it and re-run. Anything the detector drops is returned in an
`excluded` table with the rule that dropped it, so no decision is a black box.

Input is the clean transactions table from the importer. Only debits (negative amounts) are considered;
refunds and credits are ignored.

| # | Rule | What it does | Config |
|---|------|--------------|--------|
| 1 | **Merchant grouping** | Names are lower-cased, geography/legal noise (`india`, `inc`, `.com`...) is dropped, aliases are applied (`spotifyab` -> `spotify`), then names join a group when rapidfuzz `token_set_ratio` >= the threshold. `Netflix.com`, `Netflix India` and `Netflx` become one group. Note: a name that is a token subset of another scores 100 (`Amazon Prime` vs `Amazon`), so groups are split into plans in step 3. | `grouping` |
| 2 | **Cycle detection** | Gaps between distinct charge dates are matched to weekly (6-8 days), monthly (28-33), quarterly (85-95) or yearly (355-375). A gap of k cycles counts as k-1 *missed* cycles (up to 2, monthly and longer only; extra tolerance of 3 days per missed cycle). A gap just outside a window (up to 3 days) counts as *shifted* and earns partial credit. The cycle with the best regularity wins and at least 60% of gaps must fit. Each cycle has a minimum charge count (weekly 4, monthly 3, quarterly 3, yearly 2). | `cycles` |
| 3 | **Plans and streams** | A merchant is split into separate subscriptions by payment channel (PayPal vs direct), currency, and amount tier (a monthly 10.99 and a yearly 109.99 plan), but only when at least two tiers each form their own cycle *and overlap in time*. A one-time big price jump is therefore one subscription, not two. A second identical charge on the same date each month is a second stream (duplicate billing); a series that fits no cycle may be split into two interleaved accounts. | `cycles.amount_split_ratio`, `try_split_interleaved` |
| 4 | **Amount stability and price changes** | A charge is *stable* if it is strictly less than 5% from the median of its price run. A price change is recorded only when the new level is >= 5% away and holds for at least 2 charges; a single odd charge is not a change, and a lone charge at a new price is reported as `pending_price_change`. Each change includes old price, new price, percent, date and annual impact. A price hike does not reduce stability. | `amounts` |
| 5 | **Confidence (0-1)** | `0.40 x timing regularity + 0.25 x amount stability + 0.35 x history`, where history = charges seen / charges for full credit (weekly 8, monthly 6, quarterly 4, yearly 3). With only 2 charges the score is capped at 0.65, with 3 at 0.85. Below 0.5 it is not reported. Every row carries a plain-English `confidence_reason`. | `confidence` |
| 6 | **False-positive exclusions** | (a) Category keywords (groceries, fuel, dining and delivery, rideshare, marketplaces) match whole words only, so `Shellfish Club` is not fuel; `never_exclude` phrases such as `Amazon Prime` and `Uber One` are protected. (b) Weekly series need stable amounts, otherwise they are habit spending. (c) For merchants that are not subscription-like, amounts that keep changing mean usage or shopping, not a fixed plan. (d) A burst of 3+ purchases inside 7 days with varying amounts is one-off repeat buying; it is removed first so it cannot hide a real monthly charge. | `exclusions`, `categories` |

Output (`detect_recurring(transactions)`): a `subscriptions` table with merchant, cycle, average and current
amount, first and last charge date, next expected date, active flag, confidence and reason, price changes,
payment channel, category hint and the transaction IDs behind each row; plus an `excluded` table with the
rule and reason for everything that was dropped.

## Findings and the health score

`generate_findings(subscriptions, transactions)` turns detected subscriptions into findings. Each has a
severity, a plain-English reason containing the exact amounts and dates, and an `evidence` dict with the raw
figures (this is what the AI layer later receives; it never invents numbers). All thresholds are under
`findings:` in `config/rules.yaml`. Only **active** subscriptions (a charge within 1.5 cycles of the statement
end) produce findings.

| Finding | Fires when | Severity | Savings counted |
|---------|-----------|----------|-----------------|
| `DUPLICATE` | Two or more active streams for the same service and currency: monthly plus annual plans, PayPal plus direct billing, two accounts, or the same charge twice each month | high | Every stream except the one with the highest yearly cost (we assume you keep one) |
| `PRICE_HIKE` | A price increase of 5% or more that held for 2+ charges (latest increase shown, with old price, new price and annual impact) | high at +25% or more, else medium | The annual impact of the increase |
| `POSSIBLY_FORGOTTEN` | A visible points score reaches 4: discretionary category (+2), billed for 6+ months (+2), identical amount every time (+1), 3+ services in the same discretionary category (+1) | medium at 5+, else low | The full yearly cost. **This is a guess**; the finding says so, lists every heuristic, and "still using" dismisses it |
| `ZOMBIE` | You supply a usage signal: charges continue 45+ days after a last-used date, or after a trial end date | high | The full yearly cost |
| `ANNUAL_RENEWAL_SOON` | A yearly charge is due within 30 days of today | high within 7 days, else medium | None (informational) |

**Savings** are summed per currency over open findings and count each subscription once: a subscription flagged
for cancellation counts its full yearly cost, one flagged only for a price hike counts the hike's annual impact,
and the stream a duplicate finding tells you to keep is not also counted as forgotten. Dismissed findings stay
listed (as dismissed) but leave the totals and the health score.

**Subscription health score** (0-100, higher is better): `100 - A - B - C`

- **A** = 50 x (potential yearly savings / yearly subscription spend): how much of your spend is at risk.
- **B** = open findings, 10 points per high, 5 per medium, 2 per low, capped at 30.
- **C** = 2 points for each active subscription beyond 8, capped at 10 (sprawl).

80 and above reads "Healthy", 55-79 "Worth a look", below 55 "Needs attention". The formula, with your numbers
filled in, is returned with every score. Mixed-currency statements are scored on the main currency.

## PayPal integration (sandbox)

SubSweep uses three PayPal developer-platform APIs, all against the **sandbox** (`api-m.sandbox.paypal.com`).
Credentials come only from environment variables (`.env`, never committed). Endpoints and scopes below were
checked against PayPal's published OpenAPI schemas.

| Need | API | Endpoint | Scope |
|------|-----|----------|-------|
| Authentication | OAuth 2.0 client credentials | `POST /v1/oauth2/token` (Basic auth, `grant_type=client_credentials`) | n/a |
| Live subscription status | Subscriptions | `GET /v1/billing/subscriptions/{id}`, `GET /v1/billing/plans/{id}` | `https://uri.paypal.com/services/subscriptions` |
| Statement import and matching | Transaction Search | `GET /v1/reporting/transactions` (`fields=all`, max 31-day windows, up to 500 per page) | `https://uri.paypal.com/services/reporting/search/read` |

**How it behaves**

- The token is cached and refreshed 60 seconds before it expires. A 401 mid-session triggers one fresh token.
- 429, 5xx and network errors are retried with exponential backoff (0.5s, 1s, 2s, 4s, capped at 8s; `Retry-After` is honoured).
- Errors are friendly and secret-free. A 403 names the missing scope when the app's own token shows it lacks it.
- Date ranges longer than 31 days are split into windows automatically; pages are followed to the end.
- Transaction rows are filtered to payments (`T00xx` event codes) that are successful or pending. Every row keeps its
  source: `paypal_api` for PayPal data, `upload:...` for your file. A bank row that mentions PayPal and matches a PayPal
  payment (same amount and currency, dates within 3 days, or the same transaction ID) is kept once and marked
  *confirmed by PayPal*, so nothing is counted twice.
- Detected PayPal-billed subscriptions are matched to live PayPal subscriptions by subscription ID (PayPal marks these on
  transactions as reference type `SUB`) or, failing that, by plan name and price. You then see status, next billing
  date and last payment.
- **Mismatch finding:** if PayPal says a subscription is `CANCELLED`, `SUSPENDED` or `EXPIRED` but your statement
  keeps charging it, you get a high-severity `PAYPAL_MISMATCH` finding. A plan price that differs from the statement
  price gives a low-severity note.
- **SubSweep never cancels anything.** There is no cancel or suspend code. You get a link to PayPal's automatic
  payments page to manage it yourself. Any non-GET call raises `ConfirmationRequired` unless explicitly confirmed;
  the seed script asks you to type `yes`.

**Important: whose point of view?** PayPal's Transaction Search and Subscriptions APIs are merchant-side: they show the
business account that owns the app. In the sandbox demo, the app's business account *receives* the buyer's subscription
payments, so SubSweep flips the sign to show them as what the subscriber pays. A real consumer product would need the
subscriber's own PayPal data, which these APIs do not expose; that is why the main path is uploading a bank or PayPal
activity CSV, with the sandbox used to demonstrate live status and verification.

### Mock mode: the demo works without any PayPal setup

Live PayPal is **optional**. Approving sandbox subscriptions is a manual step only a person can do in a browser, so
SubSweep ships bundled demo data in [`data/paypal_mock.json`](data/paypal_mock.json): four fictional subscriptions
(Streamly Plus, Streamly Plus (Annual), CloudBox Storage and FitPass Gym) with a few charges each, shaped like the real
PayPal responses.

- Set `PAYPAL_MODE=mock` and nothing else: no credentials, no network, no approval step.
- In the default `PAYPAL_MODE=live`, if any of your seeded sandbox subscriptions is still `APPROVAL_PENDING` (not yet
  approved by the buyer), SubSweep switches the **whole** PayPal view to the demo data and tells you why, rather than
  mixing live statuses with demo charges. Once everything is approved, live data is used. A failed lookup is reported,
  not hidden by the fallback.
- Demo data is always labelled: transactions have source `paypal_mock` (live PayPal rows are `paypal_api`), the
  subscriptions carry `data_source="mock"`, and the view includes the notice "Showing bundled demo data". It cannot be
  mistaken for a live PayPal account.
- The demo story is built in: Streamly Plus is billed monthly **and** yearly (a `DUPLICATE`), and FitPass Gym is
  `CANCELLED` in PayPal on 11 Aug 2026 yet still charged on 10 Sep and 10 Oct (a `PAYPAL_MISMATCH`). The other three
  subscriptions are `ACTIVE`.
- `python -m subsweep.paypal status` and `transactions` honour the mode, and `PAYPAL_MOCK_FILE` points to your own file.
  The seed script and the approval flow are unchanged.

### Live sandbox setup (optional, about 10 minutes)

Skip this section entirely if you are happy with mock mode.

1. Log in to the [PayPal Developer Dashboard](https://developer.paypal.com/dashboard/), open **Apps & Credentials**, use
   the Default Application (or **Create App**) in the sandbox, and copy the client ID and secret.
2. Under **Testing Tools > Sandbox Accounts**, note your sandbox **personal** (buyer) account; **View/Edit Account**
   shows its email and password.
3. `cp .env.example .env` and fill in `PAYPAL_CLIENT_ID`, `PAYPAL_CLIENT_SECRET` (and optionally `PAYPAL_BUYER_EMAIL`).
4. Preview, then seed the demo plans and subscriptions:
   ```bash
   python scripts/seed_paypal_sandbox.py --dry-run
   python scripts/seed_paypal_sandbox.py
   ```
5. Open each printed approval link while signed in to [sandbox.paypal.com](https://sandbox.paypal.com/signin) as the
   **buyer** and approve it. This is manual and optional: PayPal only allows approval in a browser, and until you do,
   the app uses the demo data. Then check:
   ```bash
   python scripts/seed_paypal_sandbox.py --status
   python -m subsweep.paypal transactions --days 30
   python -m pytest tests/integration -v      # live read-only checks; skipped without credentials
   ```
6. To demo a mismatch, cancel *FitPass Gym* as the buyer in the sandbox, then analyse a statement that still charges it.

Things I could not confirm from PayPal's documentation, so check them when you first run live:
whether your sandbox app needs the **Transaction Search** feature switched on (if it does not have the scope you will
get a 403 that names `https://uri.paypal.com/services/reporting/search/read`), and that the sandbox "manage
subscriptions" page is `https://www.sandbox.paypal.com/myaccount/autopay/` (the live page is
`https://www.paypal.com/myaccount/autopay/`; set `PAYPAL_MANAGE_URL` to override). Sandbox accounts only generate the
first payment when you approve a subscription, so longer payment history comes from the demo statement, not PayPal.

## AI layer (Anthropic)

Three features use the Anthropic API (`claude-opus-5-5` by default; set `SUBSWEEP_MODEL=claude-sonnet-5-5` or
`claude-haiku-5-5` to trade capability for cost). The key comes only from `ANTHROPIC_API_KEY`.

| Feature | What it does | Where the facts come from |
|---------|--------------|---------------------------|
| **Finding explainer** | 2 to 3 calm, plain-English sentences per finding, with the exact amounts and dates | The findings engine (Python), as a small JSON fact sheet |
| **Message drafter** | A short, polite cancellation / price-query / refund message for `DUPLICATE`, `PRICE_HIKE` and `POSSIBLY_FORGOTTEN`, with `[ACCOUNT NAME]`, `[ORDER / TRANSACTION DETAILS]` and `[YOUR NAME]` placeholders. **It is never sent**: SubSweep has no code to send anything, and `sent` is always `False` | The same fact sheet plus the purpose of the message |
| **Spending Q&A** | Answers questions such as "What do I spend on streaming?" or "Which subscription went up the most this year?" | A whitelisted **pandas** analysis chosen for the question. Only its small result table goes to the model |

**How "the AI never invents amounts or dates" is enforced** (not just requested in the prompt):

1. **Facts first.** Every number comes from our own computation. The model only words it.
2. **Minimal data.** Only whitelisted fields are sent: type, severity, merchant name, amounts, pre-formatted dates, cycle and
   payment channel. No subscription or transaction IDs, no raw bank descriptions.
3. **Redaction.** Card numbers (spaced, dashed or masked), account numbers, IBANs, phone numbers, email addresses (including
   UPI handles) and API keys are removed from everything before any model call, and again before anything is logged.
4. **Grounding check.** Every amount, percentage and date in the model's reply must appear in the data it was given
   (whole-number rounding allowed, counts 1 to 3 always allowed). If anything is invented, the reply is discarded and a built-in
   template built from the same facts is shown instead. Limit: this checks figures, not prose or month names without a day.
5. **Guardrails.** Investment questions (stocks, funds, crypto, retirement accounts and similar), prompt-injection attempts and
   questions unrelated to your finances are refused **before** any model call, so they cost nothing and send nothing. The same
   advice filter runs on the model's output. If your question matches no analysis, the answer says so instead of guessing; an
   empty result says "I can't find anything in your data".
6. **Audit log.** Every interaction, including refusals and template fallbacks, is appended to `logs/ai_audit.jsonl`
   (git-ignored; override with `SUBSWEEP_AI_LOG`): the redacted request, the reply, model, token usage, latency, the grounding
   verdict and the final status.
7. **"Show data used".** Every answer carries `data_used`, exactly what was sent to the model after redaction, plus the result
   table for Q&A, so the interface can reveal it on demand.

**It works without a key.** With no `ANTHROPIC_API_KEY` (or `SUBSWEEP_AI=off`), if the API is unreachable, or if the model declines
or fails the grounding check, every feature falls back to a deterministic template and says so (`generated_by: "template"`).
When the model is used, the client sets `output_config.effort` to `low`, never sends `temperature` (not accepted by this model
family), and requests Anthropic's server-side refusal `fallbacks: "default"` option; if the API rejects that option the client
retries once without it.

Question routing is rule-based first. The model is only asked to pick an analysis when the rules cannot, and only for questions
that look financial; its answer must be a JSON intent from a fixed whitelist with validated parameters.

## The web app

`python -m subsweep` starts one FastAPI server that serves both the API and the frontend (plain HTML, CSS and vanilla JS
modules, no build step). Set `PORT`/`HOST` to change `127.0.0.1:8000`. Everything it needs, including Chart.js and the two
fonts, is vendored under `web/vendor/` (licences in `web/vendor/THIRD_PARTY.md`), so it runs fully offline apart from the
optional PayPal and Anthropic calls.

| Screen | What it does |
|--------|--------------|
| **Landing** | A scroll-driven story, then the upload. Drag and drop (or choose) a CSV, or click **Try the sample data**. The format is detected automatically; if it can't be, a dialog lets you pick the date, description and amount columns (and flip the sign for exports that show purchases as positive numbers). A privacy note states exactly what can leave your computer |
| **Overview** | Health score ring (with the formula), yearly and monthly spend, potential savings, the top findings, a yearly-cost chart, and the PayPal status panel (clearly marked "Demo data" in mock mode) |
| **Subscriptions** | Sortable, filterable table (search, cycle, status, PayPal only). Each row expands to a charge-history chart, the reason for the confidence score, what PayPal says, and a "Manage in PayPal" link. On phones the table becomes cards |
| **Findings** | Cards grouped by severity. Each shows the plain-English explanation (with **Show data used**), the "why we think so" points for guesses, a **Draft a message** panel (copy only, never sent), and **Dismiss**, **Still using it** and **Undo**. Decisions are remembered in a small local SQLite file |
| **Ask** | Chat over your own numbers, with suggested questions, a refusal for investment advice and off-topic requests, "nothing to report" for empty results, and **Show data used** on every answer |

Every screen has loading skeletons, empty states and friendly error messages (a stopped server, a wrong file type, a file
that is too large, columns that can't be read). Nothing in the interface can cancel a subscription or send a message.

**Design.** A calm, trustworthy finance look with a cinematic layer: real glass (`backdrop-filter` blur with a
pointer-following sheen), a drifting aurora background, Fraunces (a warm serif) for headlines and numbers, and Hanken
Grotesk for the interface. The background **mood changes with scroll** (midnight, deep sea, dusk, ember, dawn), the landing
page unravels a messy statement into monthly rails as you scroll, and finding cards arrive from depth. Coral is the one
alert colour; everything else is neutral. There is a night theme and a day theme.

**Accessibility.** Skip link, landmarks, full keyboard use with a visible focus ring, `aria-sort`, `aria-expanded` and live
regions, alt text and a data table for every chart, and `prefers-reduced-motion` support (no pinning, scrubbing, parallax or
count-ups; everything is drawn in its finished state). Text contrast is checked by tests against the real CSS tokens and
every mood palette at its worst case: body text clears WCAG AAA (7:1) on glass and AA (4.5:1) on the bare background.

**Security and privacy.** Statements are processed in memory and never written to disk (only your dismiss decisions are
stored). Uploads are limited to 5 MB, must be `.csv`, `.tsv` or `.txt`, and are rejected if they look binary. A strict
Content-Security-Policy (`script-src 'self'`, no inline script, no external hosts) and the usual hardening headers are sent on
every response; the session cookie is `HttpOnly` and `SameSite=Strict`; AI use is capped per statement. Oversized request bodies are
cut off while they stream in (a 413, never written to disk), card and account numbers, phone numbers, email addresses and API keys are
redacted from everything sent to the model and from the audit log, and a test suite attacks the running API to keep it that way
(`tests/test_security.py`). Text from your statement
is inserted into the page with `textContent` only, which a test enforces by scanning the code and by loading a statement with
a hostile merchant name and filename in a real browser.

**The demo statement.** `python scripts/make_demo_data.py` writes `sample_data/demo_statement.csv`: 24 months and 588
transactions generated from a fixed seed, with about ten subscriptions and these planted findings: a **duplicate** (Streamly
billed monthly and yearly), a **price hike** (Netflix $15.49 to $17.99), a **forgotten** subscription (Hulu), an **annual
renewal** coming due (a domain, about 12 Nov 2026), and, once the PayPal demo data is merged in, a **cancelled-in-PayPal but
still-charging** subscription (FitPass Gym). Groceries, fuel, coffee, rideshare and marketplace shopping are included and
must not be flagged. It covers 24 months rather than 12 because a yearly renewal can only be detected from two yearly charges
about a year apart, so a 12-month window could never contain a renewal and show it coming due.

**Browser tests.** `python -m pytest tests/ui` drives Chromium through Playwright against a live server: keyboard use, theme
persistence and repaint, the scroll-driven theme change, reduced motion, the full sample flow, error states, the column
mapper, three viewport widths with no horizontal scroll, accessible names, and hostile text. They need Node and Playwright
(`npm i -g playwright`) and are skipped automatically otherwise.

Known limits: biweekly plans are not recognised, a single yearly charge cannot be detected, and the merchant
cleaner only strips a curated list of cities, so unusual place names can survive into the merchant name.

## License

MIT, see [LICENSE](LICENSE).
