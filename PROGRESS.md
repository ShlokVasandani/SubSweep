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

## Next
- Run the PayPal parts live (needs your sandbox credentials; PayPal hosts are not reachable from the cloud session).
- Phase 5 – AI layer (explainer, message drafter, Q&A, audit log, redaction). Needs `ANTHROPIC_API_KEY`.

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
