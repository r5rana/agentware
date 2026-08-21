"""The ROSTER of adjudication tables that live outside `_body_adjudication`.

Owns exactly one thing: which sibling tables exist, merged into the single name
the host imports. It holds NO rows of its own and never will — a row here would
make this the grab-bag the split exists to prevent, and the boundary is checkable
by grep: this file contains no `"adjudication":` key.

Why a roster instead of one import per sibling in the host: `_body_adjudication`
carries a shrink-only `file_over_cap` row whose invariant says its length
measures the adjudicated-divergence COUNT. Every future wave that splits its rows
off at a shared reason — which is the seam that invariant itself names as the
legitimate one — would otherwise cost the host two more lines of wiring and
WORSEN that row, so the wave would be fighting the ceiling instead of paying it
down. With the roster the host's wiring is fixed at two lines forever and the
next sibling costs it nothing.

Nothing here is forgiven and nothing changes in a move: same names, same floors,
same absorbers, same rationales. `_body_adjudication` merges this into
`ADJUDICATIONS`, which stays the ONE name every consumer imports.
"""

from _body_adjudication_loopobs import LOOP_OBSERVABILITY_ROWS
from _body_adjudication_shimretirement import SHIM_RETIREMENT_ROWS

#: Merged in declaration order; the tables are disjoint by construction (each
#: names bodies its own wave split), and a collision would silently drop an
#: adjudication, so it is asserted rather than assumed.
_TABLES = (SHIM_RETIREMENT_ROWS, LOOP_OBSERVABILITY_ROWS)
_seen = set()
for _t in _TABLES:
    _clash = _seen & set(_t)
    assert not _clash, "two sibling tables adjudicate the same body: %r" % (
        sorted(_clash),)
    _seen |= set(_t)

SIBLING_ROWS = {k: v for _t in _TABLES for k, v in _t.items()}
