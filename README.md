# SubSweep

Find and clean up forgotten, duplicated, and price-hiked subscriptions from a bank or PayPal statement.
SubSweep never cancels anything or sends any message on your behalf.

> Work in progress for the PayPal AI Hackathon. Full README arrives in Phase 8.

## Quick start (Phase 1)

```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
python -m subsweep.ingest sample_data/card_split.csv
python -m pytest
```

## License

MIT, see [LICENSE](LICENSE).
