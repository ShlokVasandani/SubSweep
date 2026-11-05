# Changelog

All notable changes. The project follows [Semantic Versioning](https://semver.org/) loosely: it was built during the PayPal AI Hackathon, 4 to 5 November 2026.

## [Unreleased]

### Added
- **Live demo** at https://subsweep-chi.vercel.app, deployed from this repository on Vercel (every push redeploys). Verified as an anonymous visitor from outside: real upload, history, explain, draft, ask, dismiss and friendly errors.
- Browser-side size check, so an oversized file gets a clear message before anything is sent (a host's own limit would otherwise return a bare error).
- **Hosted build** for serverless platforms such as Vercel, with real CSV uploads and no server-side state: the browser keeps the statement and re-sends it with each action, the server analyses it in memory and keeps nothing. Tested against three separate server processes behind a round-robin proxy in a real browser.
- `uploads=False` / `SUBSWEEP_HOSTED_UPLOADS=off` for a sample-data-only hosted demo.
- Continuous integration (Python 3.11 to 3.13, plus Chromium browser tests), Dependabot, issue and pull-request templates, contributing and security guides.

### Changed
- `requirements.txt` now lists runtime dependencies only; test dependencies moved to `requirements-dev.txt`.

## [0.6.0] - 2026-11-05

### Added
- Web app: a cinematic scroll-driven landing page, glass interface with night and day themes, and five screens (landing and upload, overview, subscriptions, findings, ask), with loading, empty and error states and full keyboard and reduced-motion support.
- Reproducible 24-month sample statement (an annual renewal can only be detected from two yearly charges).
- Submission assets: judge-facing README with an architecture diagram, Devpost text, a 2:30 demo script, recording and caption tooling, and a checker that keeps the limits honest.

### Security
- Fixed a regular-expression denial of service in merchant cleaning and redaction (10 slow patterns; a 6,000-character description ran for over ten seconds). Patterns are now linear and descriptions are capped.
- Email addresses inside bank descriptions are removed before cleaning can mangle them into something the redactor no longer recognises.
- Over-limit request bodies are cut off while streaming, before anything is written to disk.
- `PAYPAL_MANAGE_URL` is only honoured when it is an `https:` link.

### Fixed
- Copying a drafted message no longer throws when the browser denies clipboard access, and tells the user instead.

## [0.5.0] - 2026-11-04
- AI layer: finding explainer, message drafter (never sent) and spending Q&A over whitelisted analyses, with redaction, a grounding check, guardrails, an audit log and template fallbacks when no key is set.

## [0.4.0] - 2026-11-04
- PayPal integration (sandbox, read-only): OAuth, Transaction Search, Subscriptions, merging with an uploaded statement, the `PAYPAL_MISMATCH` finding, and a mock mode with bundled demo data.

## [0.3.0] - 2026-11-04
- Findings engine: duplicates, price hikes, possibly forgotten, annual renewals, savings and a health score with its formula.

## [0.2.0] - 2026-11-04
- Explainable recurring-charge detection with every threshold in `config/rules.yaml`.

## [0.1.0] - 2026-11-04
- Statement ingestion and cleaning for bank, card and PayPal CSV formats.
