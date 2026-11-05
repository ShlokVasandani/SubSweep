# Devpost submission text

Copy each block into the matching Devpost field. The character counts are checked by `python scripts/check_submission.py`.
Anything in [square brackets] is something only you can fill in; the checklist in
[MANUAL_STEPS.md](MANUAL_STEPS.md) says when.

---

## Project name (limit 60 characters)

```
SubSweep: find the subscriptions quietly draining you
```

## Elevator pitch (limit 200 characters)

```
Upload a bank or PayPal statement. SubSweep finds forgotten, duplicate and price-hiked subscriptions with rules you can read, checks PayPal, and drafts the message. You decide; it never cancels.
```

---

## Full description (paste into "About the project")

## Inspiration

Subscriptions are easy to start and hard to keep track of. A trial turns into a charge, a plan quietly goes up by a few dollars,
the same service ends up billed twice (once on a card, once through PayPal), and a yearly renewal arrives months after you last
thought about it. Each charge is small, so none of them stands out in a long statement. Together they add up, and the ones billed
through PayPal's automatic payments are the hardest to see because the billing lives somewhere other than your bank statement.

## What it does

You drop in a CSV from a bank, a card or PayPal (or click "Try the sample data"). SubSweep:

1. **Parses and cleans it.** It detects the format, handles day-first dates and different number styles, turns
   `SQ *NETFLIX.COM 08/12 LOS GATOS CA` into `Netflix`, and removes duplicate rows.
2. **Finds the recurring charges** with plain, explainable rules: weekly, monthly, quarterly and yearly cycles, missed or shifted
   payments, price changes, and a confidence score with a one-line reason. Groceries, fuel and coffee are not subscriptions and
   are excluded, and every exclusion lists the rule that caused it.
3. **Turns them into findings**: duplicate subscriptions, price hikes, subscriptions you may have forgotten (clearly labelled as
   a guess, with the evidence), annual renewals coming due, and charges that continue after PayPal says the subscription was
   cancelled. Each shows the yearly saving, and a 0 to 100 health score shows its formula.
4. **Explains and drafts.** Claude explains each finding in plain English and drafts a short, polite message (a cancellation, a
   price query or a refund request) with placeholders for your details. You can also ask questions about your own spending.

**You decide.** There is no code anywhere in SubSweep that cancels a subscription or sends a message. Drafts are for you to copy.

On the sample statement it finds 10 subscriptions costing $1,697 a year, a health score of 45, and 7 findings worth about $700 a
year: Streamly billed twice, Netflix up from $15.49 to $17.99, Hulu probably forgotten, a domain renewal due in 15 days, and a
gym still charging after PayPal shows it cancelled.

## How I built it

**Detection is deliberately not machine learning.** A finance tool that says "this looks like a subscription" has to be able to say
why. Merchant names are grouped with fuzzy matching (rapidfuzz), charge gaps are fitted to a cycle with tolerance for missed and
shifted payments, price changes must hold for at least two charges to count, and confidence is a published formula over timing
regularity, amount stability and history length. Every threshold lives in one YAML file, and each row of output carries its reason.
The stack is Python, pandas, FastAPI and SQLite, with a vanilla HTML, CSS and JavaScript frontend and no build step.

### How PayPal is used

SubSweep uses three PayPal APIs, all read-only and sandbox-only: OAuth 2.0 client credentials, Transaction Search, and
Subscriptions. They do three jobs.

- **Payments in.** PayPal payments are imported and merged with the uploaded statement. A bank row that mentions PayPal and
  matches a PayPal payment (same amount and currency, dates within three days, or the same transaction ID) is kept once and marked
  "confirmed by PayPal", so nothing is counted twice.
- **Live status.** Detected PayPal-billed subscriptions are matched to live PayPal subscriptions (by subscription ID, or by plan
  name and price) and show their status, next billing date and last payment.
- **The mismatch finding.** If PayPal reports a subscription as cancelled, suspended or expired but the statement keeps charging it,
  SubSweep raises a high-severity finding with the amount charged since. This is the check only PayPal data makes possible.

The client caches tokens, retries rate limits and server errors with backoff, splits date ranges into the 31-day windows the
Transaction Search API requires, and turns every error into a friendly message with no secrets in it. Any non-GET call is blocked
unless explicitly confirmed, and the user gets a link to PayPal's automatic-payments page to act for themselves.

One honest design note: these PayPal APIs are merchant-side, so they show the business account that owns the app. In the sandbox
demo the app's business account receives the buyer's subscription payments, and SubSweep flips them to show what the subscriber
pays. A consumer product needs the subscriber's own data, which these APIs do not expose, which is why the main path is uploading
a bank or PayPal activity CSV, with the sandbox integration demonstrating live status and cross-checking.

Approving a sandbox subscription is a manual step that only a person can do in a browser, so SubSweep also ships clearly labelled
demo data (four fictional subscriptions shaped like PayPal's real responses). It is used when no credentials are set or when a
seeded subscription is still awaiting approval. The panel says "Demo data" and states that nothing on it comes from a live PayPal
account.

### How AI is used

Claude does three things: explains a finding in two or three calm sentences, drafts a message, and answers questions about the
user's own spending by choosing one of a fixed set of pandas analyses and wording its small result table. The rule is that
**Python computes every number and the model only words it**, and that rule is enforced in code, not left to the prompt:

- **Facts first, minimal data.** The model receives a small fact sheet (merchant, amounts, dates, cycle), never transaction IDs
  or raw bank descriptions. A "Show data used" control reveals exactly what was sent.
- **Redaction.** Card numbers, account numbers, IBANs, phone numbers, email addresses and API keys are removed before any model
  call and again before anything is logged.
- **Grounding check.** Every amount, percentage and date in a reply must appear in the data the model was given. If anything is
  invented, the reply is discarded and a template built from the same facts is shown instead.
- **Guardrails.** Investment questions, prompt-injection attempts and unrelated questions are refused before any model call.
- **Audit log.** Every interaction, including refusals and template fallbacks, is logged in redacted form.
- **It works without AI.** With no key, or if the API is unavailable, every feature falls back to a built-in template and labels
  itself, so the product never depends on the model being up.

### Privacy and safety

Statements are processed in memory and never written to disk; the only thing stored is which findings you dismissed. Uploads are
limited to 5 MB and checked by type and content, oversized bodies are cut off while they stream in, the page is served with a
strict Content-Security-Policy, and text from a statement is only ever inserted into the page as text. A security test suite
attacks the running API with hostile files, oversized cells, secrets in the environment and statements full of personal
identifiers, and each new protection was confirmed to fail its test when removed.

## Challenges

- **Telling a subscription from a habit.** Coffee every morning has a rhythm too. The detector needed rules to separate fixed
  plans from usage: whole-word category matching (so "Shellfish Club" is not fuel), protected phrases (Amazon Prime is a
  subscription), and a check that varying amounts mean shopping, not a plan.
- **Real statements are messy.** Day-first versus month-first dates, thousands separators in different styles, debit and credit
  columns, PayPal descriptions, and the same payment appearing in both a bank file and PayPal.
- **Keeping the AI honest.** Instructions in a prompt are not a guarantee, so replies are verified against the data and thrown away
  if they do not match.
- **A bug the tests did not predict.** Probing the running app found that an email address in a bank description was mangled by
  name cleaning into something the redaction patterns no longer recognised, so part of it could reach the model. It is fixed at
  both layers and covered by an end-to-end test.

## Accomplishments

- Detection that can explain itself, with every exclusion and every confidence score traceable to a rule.
- A real use of PayPal that does something a bank statement alone cannot: catching a subscription that PayPal shows as cancelled
  but that is still being charged.
- An AI layer that is useful without being trusted: grounded, redacted, auditable and optional.
- 962 automated tests, including 21 real-browser checks and a security suite, and a polished interface with a night and day theme,
  full keyboard use, labelled controls, and text contrast checked against the real colour values.

## What I learned

The hard part of a money tool is not finding patterns, it is being able to defend each one. Designing every output to carry its
reason, and every AI answer to carry the data behind it, made the product more trustworthy and also easier to test.

## What's next

- Run the PayPal sandbox integration live end to end and the AI layer against the live Anthropic API (see the status table in the
  README for what has been verified so far). [Update this line after your live run.]
- Recognise biweekly plans and add multi-currency health scoring.
- Subscriber-side PayPal data if and when it becomes available, so no CSV is needed.
- Bank connections through an aggregator, with the same privacy rules.

## About the demo data

The sample statement spans **24 months, not 12**. An annual renewal can only be detected from two yearly charges about a year
apart, so a 12-month statement can never contain one and could never show a renewal coming due. The statement is generated from a
fixed seed (588 transactions, reproducible), plants a duplicate, a price hike, a forgotten subscription and an annual renewal,
and includes groceries, fuel, coffee, rideshare and marketplace purchases that must not be flagged. All names and numbers are
fictional.

---

## Built with

```
Python, pandas, NumPy, rapidfuzz, FastAPI, uvicorn, SQLite, httpx, PyYAML, JavaScript, HTML, CSS, Chart.js, PayPal OAuth 2.0 API, PayPal Transaction Search API, PayPal Subscriptions API, Anthropic Claude API, pytest, Playwright
```

## Try it out links

- Code: https://github.com/ShlokVasandani/SubSweep  [make the repository public first]
- Video: [your YouTube link, set to Public]

## Image gallery (upload from `docs/screenshots/`)

1. `01-landing.jpg`: the landing page
2. `02-unravel.jpg`: the scroll-driven "statement is noise" scene
3. `03-overview.jpg`: the overview with the health score
4. `04-findings.jpg`: a PayPal mismatch finding with plain-English explanation
