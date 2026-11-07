# aggregator

Normalizes heterogeneous call outcomes (quote, confirmed slot, savings amount)
into one comparable result.

| File | What it does |
|---|---|
| `extract.py` | Transcript -> `Outcome` dict for one track. Never raises. |
| `aggregate.py` | Groups, ranks and times a run's calls. Pure. |
| `ledger.py` | Turns a finished call's outcome into dated ledger rows. Pure. |
| `accuracy.py` | Scores extractor output against labeled outcomes. Pure. |
