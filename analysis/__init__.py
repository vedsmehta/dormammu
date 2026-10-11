"""Analysis layer: patterns across many calls and runs for one user, computed from store rows.

Postgres `events` and the runs/calls tables are the system of record; Keewano is the secondary sink
and the external analysis surface. This package answers the same questions locally (pure Python over
store rows) and expresses them for Keewano (analysis.keewano_queries) so the two can be compared.

Phase-2 wiring points:
- `compute_insights(store, user_id)` -> JSON report for an insights route and page.
- `prior_context_from_insights(insights, vendor_key, track)` -> one or two sentences for dispatch.
- `keewano_insights(user_id)` -> the Keewano-side numbers, or None when Keewano cannot answer.
"""

from analysis.context import prior_context_for, prior_context_from_insights
from analysis.insights import compute_insights
from analysis.keewano_queries import KEEWANO_QUERIES, keewano_insights

__all__ = [
    "KEEWANO_QUERIES",
    "compute_insights",
    "keewano_insights",
    "prior_context_for",
    "prior_context_from_insights",
]
