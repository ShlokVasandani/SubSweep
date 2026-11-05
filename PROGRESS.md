# SubSweep progress

Build window: started 2026-11-04 (after the Oct 1 start). Submission closes 2026-11-12 12:00 PT.

## Done
- **Phase 1 – ingestion & cleaning**: three auto-detected formats (generic bank, PayPal activity,
  credit-card debit/credit split) plus manual column mapping; date/amount/currency normalisation;
  merchant cleaning; ID-aware dedupe that keeps genuine same-day repeats; deterministic txn IDs;
  CLI (`python -m subsweep.ingest file.csv`); 175 tests.

- **Phase 2 – recurring detection**: `subsweep/detect/` (grouping, cycles, pricing, confidence, exclusions,
  detector), editable `config/rules.yaml`, `python -m subsweep.detect`, README rule table. 75 new tests;
  every rule was mutation-checked (25 code mutations, all caught by the suite).

- **Phase 3 – findings engine**: `subsweep/findings/` (DUPLICATE, PRICE_HIKE, POSSIBLY_FORGOTTEN, ZOMBIE,
  ANNUAL_RENEWAL_SOON), spend/savings totals, health score with returned formula, dismissal support,
  `python -m subsweep.findings`, README tables. 61 new tests; 34 findings mutations all caught.

- **Phase 4 – PayPal sandbox integration (code complete, NOT yet run live)**: `subsweep/paypal/` (OAuth client with
  token cache and backoff, Transaction Search, Subscriptions status, source merge and subscription matching, seed
  logic), `scripts/seed_paypal_sandbox.py`, `python -m subsweep.paypal`, `PAYPAL_MISMATCH` finding, skipped-without-
  credentials integration tests. 129 new tests with all HTTP mocked; 53 PayPal-side mutations all caught.

- **PayPal mock mode**: `PAYPAL_MODE=mock` or automatic fallback when a seeded sandbox subscription is
  `APPROVAL_PENDING`; bundled `data/paypal_mock.json` (4 subscriptions, 18 charges; FitPass cancelled-but-charged);
  `subsweep/paypal/provider.py`; labelled `paypal_mock` / `data_source="mock"`. 44 new tests, 34 mutations all caught.
  Live approval is optional and manual. `seed.py` untouched.

- **Phase 5 – AI layer (code complete, NOT yet run against the live Anthropic API)**: `subsweep/ai/` (finding
  explainer, message drafter that never sends, spending Q&A over whitelisted pandas analyses, redaction, grounding check,
  guardrails, JSONL audit log, `data_used` on every answer), `python -m subsweep.ai`. Works without a key via templates.
  260 new tests with a scripted fake model; 93 mutations of the AI layer all caught.

- **Phase 6 – frontend + API**: FastAPI app (`subsweep/api/`) serving a no-build vanilla-JS frontend (`web/`): cinematic scroll-driven
  landing (mood blends by scroll, pinned "unravel" scene, 3D card deck), glass UI, night/day themes, five screens with loading /
  empty / error states, Chart.js (vendored) charts, mapper dialog, SQLite decisions store, reproducible 24-month demo statement.
  Contrast, icons, CSP-safety and escaping are tested statically; 18 real-browser checks ran through Playwright at the end of this phase (21 now, after Phases 7 and 8).

- **Phase 7 – testing, demo data and hardening**: the planted-findings end-to-end tests and the seeded demo generator were already in place
  from Phase 6 (24-month, see Decisions). New this phase, found by attacking the running app rather than by reading code:
  - **Real leak fixed**: an email address inside a bank description (`john.doe@gmail.com`) was mangled by merchant cleaning into
    `john.doe@ gmail`, which no email pattern recognised, so `john.doe` reached the model and the audit log. Cleaning now drops full
    addresses first, and the redactor has a backstop for a dangling `name@`.
  - **Body guard**: an over-limit upload used to be spooled to disk by the multipart parser before the size check ran. A streaming
    guard now refuses it with a 413 (declared or chunked) without reading it; 60 MB is refused in about 0.1 s.
  - **One 500 removed**: a CSV with a single enormous cell raised `csv.Error` out of ingestion; it is now a friendly 422.
  - `PAYPAL_MANAGE_URL` (rendered as a link) is only honoured when it is `https:`.
  - `tests/test_security.py` (29 tests): identifiers in statements and questions never reach a model, the audit log or the UI; fake secrets
    in the environment never appear in logs, console output, responses or the audit log (including on error paths); hostile uploads;
    link schemes; headers. Five hardening changes were reverted one at a time to confirm each has a test that fails without it.

- **Phase 8 – submission assets**: README rewritten for judges (problem, audience, what it does, screenshots, quick start, demo, a Mermaid
  architecture diagram checked with the real Mermaid parser, how PayPal and AI are used, a status table that says what has and has not been
  run live, test summary, layout, build dates). `docs/DEVPOST.md` (name 53 characters, pitch 194), `docs/DEMO_SCRIPT.md` (2:30, seven beats,
  342 words), `docs/MANUAL_STEPS.md` (the checklist for the parts only you can do), four screenshots in `docs/screenshots/`.
  - `scripts/check_submission.py` plus `tests/test_submission_docs.py` keep the limits honest: name under 60, pitch under 200, timestamps
    contiguous and totalling 2:30, each beat speakable, the 24-month explanation present in both README and Devpost text, build dates
    after 1 Oct, no personal email in any document. Each rule was confirmed to fail on a deliberately broken document.
  - `demo_video_script_walkthrough` (browser) performs every beat of the script against the real app with the script's own labels and
    figures, so the script cannot drift from the UI.
  - Found while doing that: copying a drafted message threw an unhandled error when the browser denied clipboard access, with no feedback.
    `copyText` now falls back and the user is told if copying fails; `copy_message_degrades_gracefully` covers it (reverted to confirm it fails).
  - **Regex denial of service, found by a flaky test** (`test_pathological_inputs_do_not_hang` passed alone at 1.5s against a 2s budget and failed under
    load). It was real: `clean_merchant("x" * 6000)` ran for over ten seconds and `redact_text` took 17s for 20,000 characters, and a CSV cell can be about
    128 KB, so one small upload could pin a CPU for minutes. A fuzz of every compiled regex in the package found 10 slow patterns in the merchant cleaner
    and the redactor, **two of them the email patterns I added in Phase 7**. Fixed with linear patterns (possessive quantifiers, start-of-run
    lookbehinds) plus hard caps (descriptions 1,000 characters, text sent for redaction 20,000, the rest dropped, never forwarded).
    `tests/test_redos.py` (132 tests): every redaction pattern fuzzed at 20,000 characters, a scaling check (4x input must cost about 4x, so it does not
    depend on machine speed), the cleaner and redactor driven with 130 to 200 KB of hostile input, and a crafted upload through the real API. Five
    reversions confirmed each fix and each cap has a failing test. The Phase 7 security suite had missed this class of bug.
  - Found while checking the README: uploading `sample_data/demo_statement.csv` without `PAYPAL_MODE=mock` gives 6 findings and no PayPal panel
    (7 with it, and with the sample-data button). The README and the video script now say to start with `PAYPAL_MODE=mock`.

- **Phase 9 – hosted build, live demo, repo polish**: a Vercel deployment with real CSV uploads and no server-side state. A serverless host
  answers each request from a different instance with no shared memory or writable disk, so the browser keeps the statement and re-sends it
  with each action (`StatelessMiddleware`); the server rebuilds the analysis in memory, keeps two warm per instance, and each visitor's
  decisions live in a cookie. Uploads are capped at 4 MB (Vercel's request limit is 4.5 MB) and are never merged with the fictional PayPal
  demo data. 38 new API tests, each property mutation-checked, plus a real-browser run (20 steps) against three server processes behind a
  round-robin proxy where API calls provably switch instance. Deployed via the Vercel connection and verified as an anonymous visitor from a
  Vercel sandbox (23 of 23). Also: CI on Python 3.11 to 3.13 plus a Chromium job (green), Dependabot (pip version bumps off), issue and PR
  templates, CONTRIBUTING, SECURITY, CHANGELOG, `requirements-dev.txt`.
  - Found while testing: the PayPal panel told an uploader to "Set PAYPAL_MODE=mock", an instruction for running locally; and a file over the
    host's body limit got a bare platform error. Both now have friendly, accurate messages.
  - Vercel Authentication was on for every `vercel.app` URL by default; it was turned off (with the owner's approval) so the demo is public.

## Next
- Add `ANTHROPIC_API_KEY` and run the AI layer live once (`python -m subsweep.ai mock --explain --ask "..."`): the request
  shape and error mapping are tested against a fake client, but no real API call has been made from the cloud session.
- Run the PayPal parts live (optional now: the demo no longer depends on it) (needs your sandbox credentials; PayPal hosts are not reachable from the cloud session).
- Work through `docs/MANUAL_STEPS.md`: live PayPal and live Claude runs, record and upload the video, make the repository public, submit on Devpost.

## Decisions recorded
- Account label for dedupe: **off** (placeholder was unfilled; default applied). Known limit: two accounts in the
  same export format with an identical same-day charge are merged when uploads are combined.
- `invert_sign` in the UI: **on** (default applied); the importer already warns when amounts are almost all positive.
- Git identity: the placeholders `{{AUTHOR_NAME}}`/`{{AUTHOR_EMAIL}}` were never filled in, so commits keep
  using the repo-local identity set in Phase 1. No email is written to any file in the repo.
- Phase 1 was not modified.
- Savings are conservative: a duplicate keeps the priciest stream (by yearly cost); that stream is not also counted
  as "forgotten"; each subscription counts once; renewal reminders count nothing.
- POSSIBLY_FORGOTTEN is explicitly a guess (statement-only heuristics shown to the user). ZOMBIE only fires from a
  user-supplied usage signal (last used / trial end); the Phase 4 PayPal status can feed it later.
- Phase 2 touched: the rules loader now requires the `findings` section, and category matching also looks at the
  alias-normalised group key so `Spotifyab` is classed as music.
- PayPal APIs are merchant-side. Sandbox data is shown from the subscriber's point of view (sign flipped,
  `perspective="payer"`); documented in the README. The main product path stays CSV upload.
- PayPal has no list-my-subscriptions call here, so subscription IDs come from the seed registry
  (`data/paypal_seed.json`, git-ignored) and from `SUB` references on transactions.
- Sandbox-only guard: a non-sandbox `PAYPAL_API_BASE` is refused unless `SUBSWEEP_ALLOW_LIVE=1`.
- Phase 3 touched: new `PAYPAL_MISMATCH` finding and an optional `paypal_status` argument; new `findings.paypal` config.
- Mock fallback is all-or-nothing (never a live/demo mix) and clearly labelled; lookup errors are not masked.
- Phase 1 touched (tiny fix): the merchant cleaner no longer strips a balanced closing parenthesis, so
  "Streamly Plus (Annual)" keeps its name; capitalisation also works after a leading "(".
- Model: `claude-opus-5-5` by default (per Anthropic's current guidance); `SUBSWEEP_MODEL` switches to sonnet/haiku for cost.
  Never sends `temperature`; sets `output_config.effort="low"`; requests server-side refusal `fallbacks: "default"` and
  silently drops it once if the API rejects it.
- Every model output is verified: amounts/percentages/dates must appear in the data sent, else a template (built from the
  same facts) is shown. The check covers figures, not prose.
- The model only routes a question when keyword rules fail AND the question looks financial; unrelated and investment
  questions are refused locally with no model call.
- Fixed in passing: a catastrophic-backtracking (ReDoS) bug in the first redaction pattern, found by the test run;
  a key-fragment leak in the audit log ordering, found by mutation testing.
- Demo statement spans 24 months (not 12): yearly renewals need two yearly charges to be detected.
- Charts use vendored Chart.js (the stack named Plotly or Chart.js); fonts are vendored Fraunces + Hanken Grotesk (OFL).
- The web app keeps analyses in memory per session cookie (max 12, 6h); only dismiss decisions go to SQLite (`data/subsweep.db`).
- Phase 1 touched again (cosmetic): known brand names keep their capitalisation (FitPass, CloudBox, YouTube, iCloud...).
- Bugs found by tests this phase and fixed: day-theme background not repainted on toggle; non-object `mapping` JSON caused a 500;
  `el()` ignored camelCase inline styles; chart y-axis rounded cents away.
- Weekly cycles do not allow missed weeks (a 14-day gap is not "a skipped week"); biweekly plans are unsupported.
- A merchant is split into plans (monthly vs yearly) only when amount tiers form concurrent cycles; otherwise one
  subscription with irregular amounts or a sustained price change.

## Blockers / open questions
- **Live PayPal verification is blocked in the cloud session**: `api-m.sandbox.paypal.com` and `developer.paypal.com`
  do not resolve there (outside the allowed network domains) and no sandbox credentials are set. Everything is
  mock-tested; the live integration tests and the seed script must be run on your machine.
- Unconfirmed against PayPal docs: how Transaction Search is enabled for a sandbox app; the sandbox
  "manage subscriptions" URL (derived from the documented live URL).
- Anthropic key needed for Phase 5 (`ANTHROPIC_API_KEY`).
- Merchant location stripping uses a curated city/state list; unusual cities survive cleaning and are
  left for the fuzzy matcher in Phase 2.
