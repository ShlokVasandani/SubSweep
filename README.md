# SubSweep

Find and clean up forgotten, duplicated, and price-hiked subscriptions from a bank or PayPal statement.
SubSweep never cancels anything or sends any message on your behalf.

> Work in progress for the PayPal AI Hackathon. Full README arrives in Phase 8.

## Quick start

```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
python -m subsweep.ingest sample_data/card_split.csv     # parse + clean a statement
python -m subsweep.detect my_statement.csv --excluded    # find recurring charges, show what was excluded
python -m subsweep.findings my_statement.csv             # findings, savings and health score
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

### Sandbox setup (about 10 minutes)

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
   **buyer** and approve it. PayPal only allows approval in a browser. Then check:
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

Known limits: biweekly plans are not recognised, a single yearly charge cannot be detected, and the merchant
cleaner only strips a curated list of cities, so unusual place names can survive into the merchant name.

## License

MIT, see [LICENSE](LICENSE).
