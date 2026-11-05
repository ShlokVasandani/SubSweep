## What this changes

<!-- One or two sentences. Link the issue if there is one. -->

## Checklist

- [ ] `python -m pytest -q -m "not ui"` passes (and `python -m pytest -q -m ui` if the interface changed)
- [ ] New behaviour has a test, and I confirmed it fails without the change
- [ ] Nothing here can cancel a subscription or send a message
- [ ] Nothing sensitive (statements, keys, `.env`) is committed, and anything sent to a model is still redacted
