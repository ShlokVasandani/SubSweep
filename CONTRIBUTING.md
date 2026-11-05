# Contributing to SubSweep

Thanks for looking. SubSweep is small on purpose, so a few principles decide most questions:

1. **It never cancels anything and never sends anything.** There is no code that does either, and a change must not add any. Drafts are text for the person to copy.
2. **Detection stays explainable.** Rules live in [`config/rules.yaml`](config/rules.yaml), every exclusion records the rule that caused it, and every score carries its reason. No black boxes.
3. **Python computes every number; the model only words it.** AI output is checked against the data it was given, and anything personal is redacted before it reaches a model.
4. **Statements are sensitive.** Nothing from a statement is written to disk or logged, and nothing sensitive is committed.

## Setup

You need Python 3.11 or newer.

```bash
git clone https://github.com/ShlokVasandani/SubSweep.git && cd SubSweep
python -m venv .venv && source .venv/bin/activate      # Windows: .venv\Scripts\activate
pip install -r requirements-dev.txt
python -m subsweep                                      # http://127.0.0.1:8000
```

## Tests

```bash
python -m pytest -q -m "not ui"     # unit, API, security and documentation tests (no browser needed)
python -m pytest -q -m ui           # real-browser checks; needs Node and Playwright (npm i -g playwright)
```

The suite is hermetic: outside `tests/integration`, `PAYPAL_MODE=mock` is forced and `.env` is ignored, so no test reaches PayPal or the Anthropic API even when real keys are present. The live PayPal checks in `tests/integration` run only when sandbox credentials are set.

A change should come with a test, and the test should fail without the change (revert the fix and watch it fail). Two kinds of bug have bitten this project and now have their own guards: regular expressions that run for minutes on hostile input (`tests/test_redos.py`) and identifiers leaking to a model or a log (`tests/test_security.py`).

## Where things are

```
subsweep/ingest     statement formats, dates, amounts, merchant cleaning
subsweep/detect     grouping, cycles, price runs, confidence, exclusions
subsweep/findings   finding rules, savings, health score
subsweep/paypal     OAuth client, Transaction Search, Subscriptions, mock mode
subsweep/ai         redaction, guardrails, grounding check, audit log, explainer, drafter, Q&A
subsweep/api        the FastAPI app (and the stateless hosted build)
web/                the frontend: plain HTML, CSS and JS modules, no build step
```

## Pull requests

Keep each pull request to one idea, explain the why in the description, and make sure CI is green. Commit messages are short and in the imperative ("Fix the redactor on masked card numbers").

## Reporting a security problem

Please use the private route in [SECURITY.md](SECURITY.md) rather than a public issue.
