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

## Next
- Phase 3 – findings engine (DUPLICATE, PRICE_HIKE, POSSIBLY_FORGOTTEN, ANNUAL_RENEWAL_SOON, health score).

## Decisions recorded
- Account label for dedupe: **off** (placeholder was unfilled; default applied). Known limit: two accounts in the
  same export format with an identical same-day charge are merged when uploads are combined.
- `invert_sign` in the UI: **on** (default applied); the importer already warns when amounts are almost all positive.
- Git identity: the placeholders `{{AUTHOR_NAME}}`/`{{AUTHOR_EMAIL}}` were never filled in, so commits keep
  using the repo-local identity set in Phase 1. No email is written to any file in the repo.
- Phase 1 was not modified.
- Weekly cycles do not allow missed weeks (a 14-day gap is not "a skipped week"); biweekly plans are unsupported.
- A merchant is split into plans (monthly vs yearly) only when amount tiers form concurrent cycles; otherwise one
  subscription with irregular amounts or a sustained price change.

## Blockers / open questions
- None yet. PayPal sandbox credentials will be needed for Phase 4 (`PAYPAL_CLIENT_ID`, `PAYPAL_CLIENT_SECRET`)
  and an Anthropic key for Phase 5 (`ANTHROPIC_API_KEY`).
- Merchant location stripping uses a curated city/state list; unusual cities survive cleaning and are
  left for the fuzzy matcher in Phase 2.
