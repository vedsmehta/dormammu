# Extraction accuracy

Generated 2026-10-11T21:54:09+00:00 by `scripts/eval_extract.py` against model `gpt-4.1`. Regenerate with `uv run python scripts/eval_extract.py`; do not edit by hand. The UI reads the same numbers from `public/eval-report.json`.

## Summary

| track | n | exact |
|---|---|---|
| all | 24 | 100% |
| quote | 8 | 100% |
| schedule | 8 | 100% |
| negotiate | 8 | 100% |

| track | field | rate |
|---|---|---|
| quote | amount_usd | 100% |
| quote | unit | 100% |
| schedule | slot_iso | 100% |
| schedule | confirmed | 100% |
| negotiate | action | 100% |
| negotiate | saved_usd | 100% |
| negotiate | effective_date | 100% |

Recap agreement: the agent's "Just to confirm" line named the extracted value on 13 of 15 calls where both existed (87%). Extraction errors (timeout, refusal, parse): 0.

## Cases

| id | track | exact | fields | recap |
|---|---|---|---|---|
| negotiate_01_reduced_effective | negotiate | yes | 3/3 | yes |
| negotiate_02_refused | negotiate | yes | 3/3 | n/a |
| negotiate_03_canceled | negotiate | yes | 3/3 | yes |
| negotiate_04_credit_only | negotiate | yes | 3/3 | yes |
| negotiate_05_promo_reverts | negotiate | yes | 3/3 | n/a |
| negotiate_06_no_recap_reduced | negotiate | yes | 3/3 | n/a |
| negotiate_07_voicemail | negotiate | yes | 3/3 | n/a |
| negotiate_08_empty | negotiate | yes | 3/3 | n/a |
| quote_01_flat_clean | quote | yes | 2/2 | yes |
| quote_02_hourly_clean | quote | yes | 2/2 | yes |
| quote_03_no_price | quote | yes | 2/2 | n/a |
| quote_04_rambling_two_numbers | quote | yes | 2/2 | yes |
| quote_05_rep_corrects_price | quote | yes | 2/2 | yes |
| quote_06_no_recap | quote | yes | 2/2 | n/a |
| quote_07_hourly_caveats | quote | yes | 2/2 | yes |
| quote_08_recap_disagrees | quote | yes | 2/2 | no |
| schedule_01_dentist_confirmed | schedule | yes | 2/2 | yes |
| schedule_02_clinic_tomorrow | schedule | yes | 2/2 | yes |
| schedule_03_pencil_in | schedule | yes | 2/2 | yes |
| schedule_04_rescheduled_later_wins | schedule | yes | 2/2 | yes |
| schedule_05_no_slot | schedule | yes | 2/2 | n/a |
| schedule_06_no_recap_confirmed | schedule | yes | 2/2 | n/a |
| schedule_07_recap_disagrees | schedule | yes | 2/2 | no |
| schedule_08_next_month | schedule | yes | 2/2 | yes |

## Misses

Every one of the 24 fixtures scored exact on every field within the tolerances below.

## Method

24 labeled transcripts live in `test/fixtures/transcripts`, eight per track. Each is written the way a real call runs: the agent opens with "Hi, this is Alex, am I speaking with <vendor>?", says once that it is an AI assistant calling for the client, and usually closes with a "Just to confirm" recap. 6 have no recap line at all. 2 have a recap that contradicts the body (the rep corrects it right after); their labels follow the body, since the correction is what was actually agreed.

Each fixture carries a `call_time`. The script freezes the extractor's clock to it per task, so relative phrases such as "Thursday at 2:30" or "the first of next month" resolve against the day of the call, not the day the eval ran.

Scoring is `aggregator/accuracy.py`. Tolerances:

| field | tolerance |
|---|---|
| amount_usd | within $1 |
| unit | same meaning (flat, per hour, per month) |
| slot_iso | same day and hour, any offset |
| confirmed | equal |
| action | equal |
| saved_usd | within $1 |
| effective_date | same date |
| caveats | not scored |
| note | not scored |

What this does not measure: speech-to-text quality on the live Dial transcript, and the phone agent's own conduct. These transcripts are typed, so they test the extractor's reading, not the microphone.
