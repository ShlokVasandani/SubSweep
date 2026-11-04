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

## Next
- Add `ANTHROPIC_API_KEY` and run the AI layer live once (`python -m subsweep.ai mock --explain --ask "..."`): the request
  shape and error mapping are tested against a fake client, but no real API call has been made from the cloud session.
- Run the PayPal parts live (optional now: the demo no longer depends on it) (needs your sandbox credentials; PayPal hosts are not reachable from the cloud session).
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
