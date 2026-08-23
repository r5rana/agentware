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

#: THE ROSTER. Nine tables, nine obligations, one per file:
#:   shimretirement  (task 56/91) — the rows the deleted monolith used to pay for
#:   loopobs         (task 99 w4) — the loop-observability split's receivers
#:   ceilingpaydown  (task 99 w7) — the rows a CEILING PAYDOWN created, waves 7-10
#:   skilllifecycle  (task 99 w11) — wave 11's, split off when ceilingpaydown hit
#:                                  its OWN 600-line tests/** cap; a wave that
#:                                  grew it past that would worsen one shrink-only
#:                                  ratchet in the commit paying six others down,
#:                                  which is the exact fight this roster exists to
#:                                  end.
#:   curationwrite   (task 99 w13) — wave 13's, split off for the same reason at
#:                                  the same seam: ceilingpaydown is AT its cap.
#:   flagverbs       (task 99 w15) — wave 15's three flag-dispatch verb handlers,
#:                                  and the one row that MOVED out of the host:
#:                                  `cmd_update` gained six receivers and a lower
#:                                  floor, which the host's shrink-only
#:                                  `file_over_cap` row could not absorb.
#:   fence           (task 99 w16) — the ONE row wave 16's frontmatter-fence
#:                                  unification created: `split_frontmatter` shed
#:                                  the four-line fence search to the helper the
#:                                  package now publishes once instead of nine
#:                                  times.
#:   verbemit        (task 99 w17) — wave 17's three verb-emit handlers, and the
#:                                  second row to MOVE out of the host:
#:                                  `cmd_audit` gained three receivers and a
#:                                  lower floor, which the host's shrink-only
#:                                  `file_over_cap` row could not absorb.
#:   derivation      (task 99 w18) — wave 18's two multi-tally folds, whose
#:                                  receivers are the tallies themselves: the
#:                                  rows exist because a parent that sheds a fold
#:                                  falls below the length the differ adjudicated
#:                                  it at, and an unnamed receiver reads as a
#:                                  lost body.
#:                                  The next wave adds the next file here.
from _body_adjudication_ceilingpaydown import CEILING_PAYDOWN_ROWS
from _body_adjudication_curationwrite import CURATION_WRITE_ROWS
from _body_adjudication_derivation import DERIVATION_ROWS
from _body_adjudication_fence import FENCE_ROWS
from _body_adjudication_flagverbs import FLAG_VERB_ROWS
from _body_adjudication_loopobs import LOOP_OBSERVABILITY_ROWS
from _body_adjudication_shimretirement import SHIM_RETIREMENT_ROWS
from _body_adjudication_skilllifecycle import SKILL_LIFECYCLE_ROWS
from _body_adjudication_verbemit import VERB_EMIT_ROWS

#: Merged in declaration order; the tables are disjoint by construction (each
#: names bodies its own wave split), and a collision would silently drop an
#: adjudication, so it is asserted rather than assumed.
_TABLES = (SHIM_RETIREMENT_ROWS, LOOP_OBSERVABILITY_ROWS,
           CEILING_PAYDOWN_ROWS, SKILL_LIFECYCLE_ROWS, CURATION_WRITE_ROWS,
           FLAG_VERB_ROWS, FENCE_ROWS, VERB_EMIT_ROWS, DERIVATION_ROWS)
_seen = set()
for _t in _TABLES:
    _clash = _seen & set(_t)
    assert not _clash, "two sibling tables adjudicate the same body: %r" % (
        sorted(_clash),)
    _seen |= set(_t)

SIBLING_ROWS = {k: v for _t in _TABLES for k, v in _t.items()}
