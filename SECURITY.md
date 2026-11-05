# Security policy

## Reporting a vulnerability

Please report security problems **privately** through GitHub: open the [Security tab](https://github.com/ShlokVasandani/SubSweep/security/advisories/new) and choose "Report a vulnerability". Do not open a public issue or include real bank statements, card numbers or API keys in a report; the sample statement (`Try the sample data`) reproduces nearly everything.

You will get an acknowledgement as soon as the report is seen. This is a small hackathon project, so there is no formal response-time guarantee, but reports are taken seriously.

## What SubSweep does to protect statements

- **In memory only.** A statement is processed in memory and never written to disk. The only thing stored is which findings you dismissed (a local SQLite file when you run it yourself; a cookie in the hosted build).
- **Hosted build.** Nothing about a visitor lives on the server: the browser keeps the statement and re-sends it with each action over HTTPS, the server analyses it in memory and keeps nothing, and request bodies are not logged. Uploads are capped at 4 MB.
- **Redaction before any model call.** Card numbers, account numbers, IBANs, phone numbers, email addresses and API keys are removed before text reaches a model and again before it is logged. Only a small fact sheet (merchant, amounts, dates, cycle) is sent, never raw bank descriptions or IDs.
- **Hardened inputs.** Uploads are checked by type and content, oversized request bodies are cut off while they stream in, description length is capped, and every regular expression that reads a description is tested for linear running time against hostile input.
- **Browser protections.** A strict Content-Security-Policy (`script-src 'self'`, no inline script, no external hosts), `HttpOnly` and `SameSite=Strict` cookies, and text from a statement is only ever inserted into the page as text.
- **Read-only PayPal.** The PayPal integration is sandbox-only and read-only; any non-`GET` call raises an error unless explicitly confirmed.

## Known limits

- The PayPal sandbox integration and the live Anthropic API have been tested with simulated responses and a scripted fake model, not yet against the live services (see the status table in the README).
- If an unexpected exception carried a secret in its message, the host's own error log could record it. The HTTP response never includes it, and no such code path is known.
