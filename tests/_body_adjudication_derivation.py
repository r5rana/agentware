"""ADJUDICATIONS — the rows task 99 wave 18's DERIVATION paydown created.

The ninth sibling of `tests/_body_adjudication.py`, split off at the same seam as
the eight before it and for the same mechanical reason the roster documents:
`_body_adjudication` carries a shrink-only `file_over_cap` row, so a wave that
grew it would be WORSENING one ratchet in the very commit whose purpose is to pay
four others down. The roster in `_body_adjudication_siblings` is the only wiring
that changes.

THE SHARED REASON, which is what makes this a seam rather than a bucket: both
bodies are one shape stated twice — a fold that walks ONE input collection and
maintains several INDEPENDENT tallies, with every tally's accumulator, its guard
and its update written inline beside the others. The length is therefore the SUM
of the tallies rather than the length of any decision, and the tallies do not
interact: each one's removal changes exactly one output field. Paying the ceiling
row down means giving each fold its own name, so each parent falls below the
length the body differ adjudicated it at — and a split that does not NAME its
receivers is read by the differ as a lost body, which is precisely the silent
downgrade this gate exists to refuse.

WHY `_mcp_query_lookup` AND `_category_stems` HAVE NO ROW. They are the other two
bodies this wave split, and neither carries a divergence row at all: the differ
compares names present in BOTH trees, and the monolith never carried either name.
A row is declared where the gate MEASURES a shortfall, never pre-emptively — a
row describing a divergence the tree does not have is the stale-forgiveness shape
the whole ratchet refuses. (Wave 17 recorded the same reasoning for `cmd_team`.)

Nothing here is forgiven. Each row carries TWO obligations the gate enforces on
every run: the host plus its named receivers must clear the winner's length, AND
the host alone must clear its own measured floor — so a parent cannot be hollowed
out behind bulky helpers.
"""

_PRICING = "scripts/agentware_lib/ops/metrics/metricspricing.py"
_FAILURELADDER = "scripts/agentware_lib/ops/failureladder.py"

#: Three receivers. Two are the folds the parent kept inline — the per-model
#: pricing (which also feeds the shared by_model rollup) and the per-day anomaly
#: walk. The third is the get-or-create bucket the body wrote THREE times, once
#: per rollup; unifying it is what makes the day and feature folds two lines each
#: instead of five, and it is a receiver rather than an incidental helper because
#: its length is length the parent used to carry.
_PRICING_RECEIVERS = [_PRICING + "::" + n for n in (
    "_price_row_models",      # one row's models -> (row_cost, priced) + by_model
    "_flag_cost_anomalies",   # the chronological trailing-median walk
    "_rollup_bucket")]        # the get-or-create written once per rollup

#: Two receivers, and the split between them is the module's own distinction:
#: `_err_tallies` holds every tally that is a function of ONE step (the per-tool
#: ERR count, the web-escalation count, the KB-lookup count) and `_ladder_tally`
#: the single tally that reads a step's NEIGHBOUR. That is why the parent still
#: sorts the steps into global order before calling: the sort exists for the
#: second helper and for nothing else, which was invisible while the four folds
#: sat in one body.
_FAILURE_RECEIVERS = [_FAILURELADDER + "::" + n for n in (
    "_err_tallies",           # the three per-step tallies, in one pass
    "_ladder_tally")]         # the one tally that reads the NEXT step

DERIVATION_ROWS = {
    "apply_pricing": {
        "adjudication": "decomposed",
        "host": _PRICING,
        "floor_chars": 1482,
        "absorbs": list(_PRICING_RECEIVERS),
        "rationale": "One body carried four obligations over two collections: "
                     "price a row's models, fold three rollups that each wrote "
                     "their own get-or-create, finalize them, and then walk the "
                     "days looking for a cost spike. The anomaly walk is the "
                     "clearest case -- it shares nothing with the pricing pass "
                     "but the by_day dict, it runs AFTER the finalize pass on "
                     "purpose (it compares the ROUNDED day costs the report "
                     "shows), and that ordering was a comment in the middle of a "
                     "85-line body rather than a named boundary. The three "
                     "identical finalize loops collapsed into one loop over the "
                     "three rollups in the same change. 16 branches -> 7.",
    },
    "derive_failures": {
        "adjudication": "decomposed",
        "host": _FAILURELADDER,
        "floor_chars": 1742,
        "absorbs": list(_FAILURE_RECEIVERS),
        "rationale": "Four independent passes over one step list, each with its "
                     "classifier already extracted and named, and each written "
                     "inline beside the others so the body's length was the sum "
                     "of four tallies rather than the length of any decision. "
                     "The split is the module's own distinction and not a size "
                     "cut: three of the four are functions of ONE step and one "
                     "reads the step that FOLLOWED each error -- which is the "
                     "only reason the parent sorts by the trace's global index "
                     "at all. The five R-FAIL tiers stay where they already "
                     "live. 15 branches -> 6.",
    },
}
