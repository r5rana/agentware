"""ADJUDICATIONS — the rows task 99 wave 15's FLAG-DISPATCH VERB paydown created.

The sixth sibling of `tests/_body_adjudication.py`, split off at the same seam as
the five before it and for the same mechanical reason: `_body_adjudication` and
`_body_adjudication_ceilingpaydown` each carry a shrink-only `file_over_cap` row,
so a wave that grew either of them would be WORSENING one ratchet in the very
commit whose purpose is to pay four others down. The roster in
`_body_adjudication_siblings` is the only wiring that changes.

THE SHARED REASON, which is what makes this a seam rather than a bucket: all
three bodies are the same shape stated three times — one verb handler, N mutually
exclusive flag modes, every arm re-implementing its own result construction and
its own renderer. Paying the ceiling row down means moving whole ARMS out of the
parent, so each parent shrinks below the length the body differ adjudicated it
at, and the split has to NAME its receivers or the differ reads a decomposition
as a lost body.

`cmd_update` MOVED here from the host rather than being re-adjudicated in place.
Its task-41 row already named one receiver (`_update_self_heal`); wave 15 adds
six more and lowers the host floor 6229 -> 2501, and doing that in the host would
have grown a file whose declared value may only fall. The task-41 rationale is
preserved below verbatim in substance — nothing is forgiven in a move.

Nothing here is forgiven. Each row still carries TWO obligations the gate
enforces on every run: the host plus its named receivers must clear the winner's
length, AND the host alone must clear its own measured floor — so a parent
cannot be hollowed out behind bulky helpers.
"""

_UPDATECMD = "scripts/agentware_lib/features/updatecmd.py"
_GRAPH = "scripts/agentware_lib/knowledge/graph.py"
_INDEXCMD = "scripts/agentware_lib/features/indexcmd.py"

#: Seven receivers, one per MODE plus the two the catalog diff splits into. The
#: two catalog helpers are receivers of `cmd_update` and not of
#: `_update_upstream_info`, because the differ tracks the body the DONOR carried
#: and the donor carried all of this inside `cmd_update`.
_UPDATE_RECEIVERS = [_UPDATECMD + "::" + n for n in (
    "_update_self_heal",        # task 41: the one mode that MUTATES
    "_update_status_line",      # the no-network SessionStart one-liner
    "_update_upstream_info",    # the informational @{upstream} view
    "_upstream_catalog_diff",   # the authored-catalog delta, or None
    "_catalog_diff_lines",      # its render; an empty side stays silent
    "_update_check_report",     # the signed-channel report, always rc 0
    "_update_apply_result")]    # the locked apply + the reconciled- normalisation

#: Four receivers: the two lookup maps, the four direct modes, the adjacency
#: direction table, and the three graph modes.
_QUERY_RECEIVERS = [_GRAPH + "::" + n for n in (
    "_lookup_maps",             # by_id / by_path, first-wins, poison-tolerant
    "_direct_results",          # id/path/tag/category, None only when unselected
    "_graph_adjacency",         # forward / reverse / the sorted union
    "_traverse_results")]       # the three graph modes, in parser order

#: Four receivers: the dry-run render, the per-candidate prompt row, the
#: in-memory apply, and the --fix render.
_SYNC_RECEIVERS = [_INDEXCMD + "::" + n for n in (
    "_sync_dry_report",         # the read-only half; nothing-to-do is SUCCESS
    "_sync_prompt_row",         # derived values, never blocking on stdin
    "_sync_apply",              # mutates `data` in memory ONLY
    "_sync_fix_report")]        # failures to stderr, non-zero on any

FLAG_VERB_ROWS = {
    "cmd_update": {
        "adjudication": "decomposed",
        "host": _UPDATECMD,
        "floor_chars": 2501,
        "absorbs": list(_UPDATE_RECEIVERS),
        "rationale": "Task 41 ported the signed-tag update channel. The handler "
                     "arrived at 166 lines against a DECLARED debt of 163 (the "
                     "ceiling fixture had already named this module by path), so "
                     "it was decomposed rather than having its allowance widened: "
                     "`--self-heal` became `_update_self_heal`, the one mode that "
                     "MUTATES and the one whose exit code carries a non-obvious "
                     "rule (a migrate-error must exit non-zero even though it "
                     "sets no needs_manual_recovery flag). The other four flags "
                     "stayed inline, and that left the handler at 32 branches / "
                     "153 lines -- TWO ceiling rows, the widest body left in the "
                     "lib at wave 15. Task 99 wave 15 finished the job the task-41 "
                     "split started: each remaining mode became its own receiver, "
                     "leaving the parent as the flag dispatch, the emit closure "
                     "and the not-a-work-tree guard (32/153 -> 6/64). The floor "
                     "falls 6229 -> 2501 with the parent, and the host-floor "
                     "obligation is what stops the parent being hollowed out "
                     "further behind seven bulky helpers.",
    },
    "cmd_query": {
        "adjudication": "decomposed",
        "host": _GRAPH,
        "floor_chars": 573,
        "absorbs": list(_QUERY_RECEIVERS),
        "rationale": "The `query` handler fused four O(1) lookups with three "
                     "graph traversals in one if/elif chain, and the three "
                     "traversals were near-verbatim copies of each other -- build "
                     "the adjacency, walk it, map ids back to entries -- differing "
                     "only in WHICH direction they walk. That is three places for "
                     "the union merge and the sorted-dedup to drift apart, and it "
                     "is why `_graph_adjacency` takes the direction as an argument "
                     "instead of being written out three times. The split makes "
                     "ONE previously-implicit contract explicit and testable: "
                     "`_direct_results` returns None only when no direct mode was "
                     "SELECTED, and `[]` when a selected mode matched nothing, so "
                     "`query --id nope` can never fall through to a traversal and "
                     "answer a different question. 29 branches -> 5.",
    },
    "cmd_index_sync": {
        "adjudication": "decomposed",
        "host": _INDEXCMD,
        "floor_chars": 566,
        "absorbs": list(_SYNC_RECEIVERS),
        "rationale": "Two disjoint modes shared one body and each re-implemented "
                     "its own renderer, with the interactive prompts inlined in "
                     "the middle of the --fix loop. The split follows the "
                     "obligation rather than the flag: `_sync_apply` MUTATES the "
                     "in-memory index and nothing else, which is what lets the "
                     "handler keep the rule that matters -- persist ONLY when "
                     "something was added, so a run where every add failed leaves "
                     "index.json untouched. `_sync_prompt_row` isolates the one "
                     "place that can block on stdin (R-SHELL-01). 18 branches -> "
                     "4, and the lock, the load guard and the persist decision "
                     "stay in the parent where the B2 lost-update comment is.",
    },
}
