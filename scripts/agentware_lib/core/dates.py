"""agentware_lib.core.dates — calendar date primitives (layer: core).

Owns: the two pure, stdlib-only calendar-date primitives with high cross-domain
fan-in — _today() (today's date as YYYY-MM-DD), _is_valid_date() (a strict
YYYY-MM-DD calendar-date validator) and utc_now_iso() (current UTC as an
ISO-8601 'Z' timestamp string; metadata only, never scoring).
Does NOT own: freshness / age math (entry_last_verified, date_age_days, freshness
live with the knowledge+ops freshness code, not here); any date FORMATTING for
human-facing reports (that stays with its report renderer).
Layer deps: core -> stdlib only (imports nothing from agentware_lib).
Fan-in: 28 (modules that import this; see catalog/modules.json depended_on_by; regen: python3 tests/_manifest_gen.py --write).

Small, STABLE, feature-independent primitives with high fan-in — index, curation,
retrieval, evaluate, bench, fleet, plans and skills all stamp or validate dates —
so a single zero-dependency core leaf lets every consumer share ONE definition
without inverting the layer graph. Same rationale as core.paths / core.orgkeys:
a tiny high-fan-in primitive earns its own module rather than a grab-bag home.
"""

import datetime
import re


def _today():
    import datetime
    return datetime.date.today().isoformat()


def _is_valid_date(s):
    """True iff `s` is a strict YYYY-MM-DD calendar date (stdlib only)."""
    if not isinstance(s, str):
        return False
    import datetime
    try:
        datetime.date.fromisoformat(s)
    except ValueError:
        return False
    # fromisoformat is lenient about some forms across versions; pin the shape.
    return bool(re.match(r"^\d{4}-\d{2}-\d{2}$", s))


def utc_now_iso():
    """Current UTC timestamp as ISO-8601 'Z' string. Metadata only (not scoring)."""
    return datetime.datetime.now(datetime.timezone.utc).strftime(
        "%Y-%m-%dT%H:%M:%SZ")


# Explicit facade export list (public AND underscore-private) — the entrypoint
# re-export loop binds exactly these onto the CLI facade so SourceFileLoader-based
# tests still reach them as CLI.<name>. See the M3/_EXPORTS convention in the plan.
_EXPORTS = [
    "_today",
    "_is_valid_date",
    "utc_now_iso",
]
