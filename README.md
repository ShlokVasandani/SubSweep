# SubSweep

Find and clean up forgotten, duplicated, and price-hiked subscriptions from a bank or PayPal statement.
SubSweep never cancels anything or sends any message on your behalf.

> Work in progress for the PayPal AI Hackathon. Full README arrives in Phase 8.

## Quick start

```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
python -m subsweep.ingest sample_data/card_split.csv     # parse + clean a statement
python -m subsweep.detect my_statement.csv --excluded    # find recurring charges, show what was excluded
python -m pytest
```

## How detection works

Recurring-charge detection is plain, explainable Python (no ML). Every threshold and keyword list lives in
[`config/rules.yaml`](config/rules.yaml); edit it and re-run. Anything the detector drops is returned in an
`excluded` table with the rule that dropped it, so no decision is a black box.

Input is the clean transactions table from the importer. Only debits (negative amounts) are considered;
refunds and credits are ignored.

| # | Rule | What it does | Config |
|---|------|--------------|--------|
| 1 | **Merchant grouping** | Names are lower-cased, geography/legal noise (`india`, `inc`, `.com`...) is dropped, aliases are applied (`spotifyab` -> `spotify`), then names join a group when rapidfuzz `token_set_ratio` >= the threshold. `Netflix.com`, `Netflix India` and `Netflx` become one group. Note: a name that is a token subset of another scores 100 (`Amazon Prime` vs `Amazon`), so groups are split into plans in step 3. | `grouping` |
| 2 | **Cycle detection** | Gaps between distinct charge dates are matched to weekly (6-8 days), monthly (28-33), quarterly (85-95) or yearly (355-375). A gap of k cycles counts as k-1 *missed* cycles (up to 2, monthly and longer only; extra tolerance of 3 days per missed cycle). A gap just outside a window (up to 3 days) counts as *shifted* and earns partial credit. The cycle with the best regularity wins and at least 60% of gaps must fit. Each cycle has a minimum charge count (weekly 4, monthly 3, quarterly 3, yearly 2). | `cycles` |
| 3 | **Plans and streams** | A merchant is split into separate subscriptions by payment channel (PayPal vs direct), currency, and amount tier (a monthly 10.99 and a yearly 109.99 plan), but only when at least two tiers each form their own cycle *and overlap in time*. A one-time big price jump is therefore one subscription, not two. A second identical charge on the same date each month is a second stream (duplicate billing); a series that fits no cycle may be split into two interleaved accounts. | `cycles.amount_split_ratio`, `try_split_interleaved` |
| 4 | **Amount stability and price changes** | A charge is *stable* if it is strictly less than 5% from the median of its price run. A price change is recorded only when the new level is >= 5% away and holds for at least 2 charges; a single odd charge is not a change, and a lone charge at a new price is reported as `pending_price_change`. Each change includes old price, new price, percent, date and annual impact. A price hike does not reduce stability. | `amounts` |
| 5 | **Confidence (0-1)** | `0.40 x timing regularity + 0.25 x amount stability + 0.35 x history`, where history = charges seen / charges for full credit (weekly 8, monthly 6, quarterly 4, yearly 3). With only 2 charges the score is capped at 0.65, with 3 at 0.85. Below 0.5 it is not reported. Every row carries a plain-English `confidence_reason`. | `confidence` |
| 6 | **False-positive exclusions** | (a) Category keywords (groceries, fuel, dining and delivery, rideshare, marketplaces) match whole words only, so `Shellfish Club` is not fuel; `never_exclude` phrases such as `Amazon Prime` and `Uber One` are protected. (b) Weekly series need stable amounts, otherwise they are habit spending. (c) For merchants that are not subscription-like, amounts that keep changing mean usage or shopping, not a fixed plan. (d) A burst of 3+ purchases inside 7 days with varying amounts is one-off repeat buying; it is removed first so it cannot hide a real monthly charge. | `exclusions`, `categories` |

Output (`detect_recurring(transactions)`): a `subscriptions` table with merchant, cycle, average and current
amount, first and last charge date, next expected date, active flag, confidence and reason, price changes,
payment channel, category hint and the transaction IDs behind each row; plus an `excluded` table with the
rule and reason for everything that was dropped.

Known limits: biweekly plans are not recognised, a single yearly charge cannot be detected, and the merchant
cleaner only strips a curated list of cities, so unusual place names can survive into the merchant name.

## License

MIT, see [LICENSE](LICENSE).
