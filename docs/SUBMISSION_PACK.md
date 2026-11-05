# Submission pack

Everything to paste or upload, in one place. **Generated** by `scripts/make_submission_pack.py` from `docs/DEVPOST.md` and the files on disk:
edit `docs/DEVPOST.md`, then re-run the script, rather than editing this file.

## Read this first: what is still open

| | Item | Why |
|---|---|---|
| 1 | **The video is silent.** | It paces every click to the 2:30 script. Record a voice-over from `docs/DEMO_SCRIPT.md`, or upload it with `subsweep-demo.srt` as captions. Say the word and I can generate a voice-over (it spends ElevenLabs credits, so I have not done it). |
| 2 | **The video's closing card shows `github.com/ShlokVasandani/SubSweep`.** | The repository must be public before anyone watches it. Making it public is your call; I have not changed it. |
| 3 | **PayPal beat = demo data.** | Recorded in `PAYPAL_MODE=mock`, so the panel carries the "Demo data" badge, and the captions say so. Nothing in the video presents it as live PayPal. |
| 4 | **AI beat = built-in wording.** | No Anthropic key here, so the top bar reads "AI: built-in text" and the captions use the script's "AI is not on" sentence instead of claiming Claude wrote it. |
| 5 | **Two blanks to fill in the pasted text.** | `[Update this line after your live run.]` at the end of the "What's next" bullet in the description (delete the bracket if you do not run live; keep the sentence above it, which is true as written), and the YouTube link in "Try it out links". |
| 6 | **PayPal sandbox and the Anthropic API have never run live from here.** | The README and Devpost text say so truthfully. If you run them on your machine, update those lines (see `docs/MANUAL_STEPS.md`, section 1). |

## Files to upload

| File | Where it goes | Details |
|---|---|---|
| `docs/video/subsweep-demo.mp4` | YouTube (visibility **Public**), then the Devpost video link | 2:29.4 long, 1920x1080, h264, 30 fps, 18.3 MB, no audio. sha256 `1ffea2575c5fc6ae...` |
| `docs/video/subsweep-demo.srt` | YouTube captions (optional) | 43 captions, matching the recorded variants |
| `docs/screenshots/*.jpg` | Devpost image gallery, in this order | see below |
| Repository | Devpost "Try it out" link | https://github.com/ShlokVasandani/SubSweep (**must be public by the time you submit**) |

The video is under 3:00 (the hackathon limit) and follows the seven beats of `docs/DEMO_SCRIPT.md`.

**Image gallery order**

| # | File | Shows |
|---|---|---|
| 1. | `docs/screenshots/01-landing.jpg` | the landing page |
| 2. | `docs/screenshots/02-unravel.jpg` | the scroll-driven "statement is noise" scene |
| 3. | `docs/screenshots/03-overview.jpg` | the overview with the health score |
| 4. | `docs/screenshots/04-findings.jpg` | a PayPal mismatch finding with its plain-English explanation |

## YouTube

Title:

```
SubSweep: find the subscriptions quietly draining you
```

Description:

```
SubSweep finds forgotten, duplicate and price-hiked subscriptions from a bank or PayPal statement, explains each one in plain English, checks PayPal, and drafts the message. You decide; it never cancels anything or sends anything.

Code: https://github.com/ShlokVasandani/SubSweep

Built for the PayPal AI Hackathon.
```

Visibility: **Public**. After uploading, open the link in a private window to confirm it plays without signing in.

## Devpost

### Project name (53 of 59 characters)

```
SubSweep: find the subscriptions quietly draining you
```

### Elevator pitch (194 of 199 characters)

```
Upload a bank or PayPal statement. SubSweep finds forgotten, duplicate and price-hiked subscriptions with rules you can read, checks PayPal, and drafts the message. You decide; it never cancels.
```

### About the project (paste as Markdown)

```
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
- 1,094 automated tests, including 21 real-browser checks and a security suite, and a polished interface with a night and day theme,
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
```

### Built with

```
Python, pandas, NumPy, rapidfuzz, FastAPI, uvicorn, SQLite, httpx, PyYAML, JavaScript, HTML, CSS, Chart.js, PayPal OAuth 2.0 API, PayPal Transaction Search API, PayPal Subscriptions API, Anthropic Claude API, pytest, Playwright
```

### Try it out links

```
https://github.com/ShlokVasandani/SubSweep
[YouTube link: fill in after uploading]
```

### Testing instructions (if the form has a field for it)

```
pip install -r requirements.txt
PAYPAL_MODE=mock python -m subsweep
Open http://127.0.0.1:8000 and click "Try the sample data". No account, key or network needed.
```

## Before you press Submit

- [ ] `docs/MANUAL_STEPS.md` sections 1 to 4 done (live checks if you want them, video recorded and uploaded, repository public).
- [ ] No `[square bracket]` placeholder is left in the text you pasted.
- [ ] Deadline: Thursday 12 November 2026, 12:00 pm Pacific. The project must show as **Submitted**, not Draft.
