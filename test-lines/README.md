# test-lines

Controlled numbers to call during a demo. They are entered through `/onboarding` into the
`vendors` table (`is_demo = true`) and are never committed.

`scripts/smoke.py` places one real call to `TEST_CALL_TO` and prints the transcript. That is the
gate before any multi-call run.
