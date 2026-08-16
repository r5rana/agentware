"""agentware_lib.core.dates — calendar date primitives (layer: core).

Owns: the pure, stdlib-only calendar-date and TIMESTAMP primitives with high
cross-domain fan-in — _today() (today's date as YYYY-MM-DD), _is_valid_date() (a
strict YYYY-MM-DD calendar-date validator), utc_now_iso() and _now_iso() (current
UTC as an ISO-8601 'Z' timestamp string; metadata only, never scoring) and
_iso_to_epoch() (the inverse: an ISO 'Z' or naive stamp back to epoch seconds).
Does NOT own: freshness / age math (entry_last_verified, date_age_days, freshness
live with the knowledge+ops freshness code, not here); any date FORMATTING for
human-facing reports (that stays with its report renderer); the plan CLAIM stamp
that consumes these (_short_host / _parse_claim / _claim_age_hours live with the
plan state machine in ops.planauthor.planstate).

_now_iso vs utc_now_iso — TWO NAMES, ONE COMPUTATION, DELIBERATELY KEPT APART
----------------------------------------------------------------------------
Their bodies are the same expression, and collapsing them into one name would be
the obvious tidy-up. It is refused here for two reasons that outlive the tidiness:
the body differ (task 30) floors every moved definition at its adjudicated
winner's character count, so deleting one reddens that gate by name; and the two
have DIFFERENT call-site populations (utc_now_iso in the knowledge/index stamping
path, _now_iso across the loop's claim + status-log path), so a single rename
would touch two unrelated blast radii in a port whose whole contract is "surface
unchanged". They converge when the monolith is gone and the names can be
reconciled deliberately, not as a side effect of a verb port.

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


def _now_iso():
    """Current UTC time as `YYYY-MM-DDTHH:MM:SSZ` (the `> Claimed-by:` / log ts)."""
    import datetime
    return datetime.datetime.now(datetime.timezone.utc).strftime(
        "%Y-%m-%dT%H:%M:%SZ")


def _iso_to_epoch(ts):
    """Parse an ISO `...Z` (or naive) timestamp to epoch seconds, else None."""
    import calendar
    import datetime
    if not ts:
        return None
    for fmt in ("%Y-%m-%dT%H:%M:%SZ", "%Y-%m-%dT%H:%M:%S"):
        try:
            return calendar.timegm(datetime.datetime.strptime(ts, fmt).timetuple())
        except (ValueError, TypeError):
            continue
    return None


# Explicit facade export list (public AND underscore-private) — the entrypoint
# re-export loop binds exactly these onto the CLI facade so SourceFileLoader-based
# tests still reach them as CLI.<name>. See the M3/_EXPORTS convention in the plan.
_EXPORTS = [
    "_today",
    "_is_valid_date",
    "utc_now_iso",
    "_now_iso",
    "_iso_to_epoch",
]
