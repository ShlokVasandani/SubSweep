# SubSweep progress

Build window: started 2026-11-04 (after the Oct 1 start). Submission closes 2026-11-12 12:00 PT.

## Done
- **Phase 1 – ingestion & cleaning**: three auto-detected formats (generic bank, PayPal activity,
  credit-card debit/credit split) plus manual column mapping; date/amount/currency normalisation;
  merchant cleaning; ID-aware dedupe that keeps genuine same-day repeats; deterministic txn IDs;
  CLI (`python -m subsweep.ingest file.csv`); 175 tests.

## Next
- Phase 2 – recurring-charge detection (rapidfuzz grouping, cycle/amount rules, `config/rules.yaml`).

## Blockers / open questions
- None yet. PayPal sandbox credentials will be needed for Phase 4 (`PAYPAL_CLIENT_ID`, `PAYPAL_CLIENT_SECRET`)
  and an Anthropic key for Phase 5 (`ANTHROPIC_API_KEY`).
- Merchant location stripping uses a curated city/state list; unusual cities survive cleaning and are
  left for the fuzzy matcher in Phase 2.
