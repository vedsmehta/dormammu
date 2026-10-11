"""Research engine: understand the user, go find the vendors, plans, discounts and rules, then propose.

Entry points (docs/RESEARCH.md):
    research.engine.research(profile, request, roster, *, max_items_per_category=3) -> proposal dict
    research.regulations.check(state, context) -> list of rule dicts
    research.phones.normalize / find_phones / verify_phone / mask
"""

from research.engine import research
from research.regulations import check

__all__ = ["check", "research"]
