# router

Takes a plain-language request and decomposes it into N per-target call scripts
to dispatch concurrently.

| File | What it does |
|---|---|
| `plan.py` | One request in, a capped list of `CallPlan` dicts out. Cached; never raises. |
| `instructions.py` | `CallPlan` + vendor + profile -> the prompt the phone agent runs on. Pure. |
| `cohorts.py` | Per-cohort eligibility the agent may assert and the asks it makes per track. |
| `negotiation.py` | Condensed bill-negotiation playbook and strategy variants (`negotiation_flow.md`). |
| `llm.py` | Shared OpenAI structured-output helper; returns `None` on failure. |
