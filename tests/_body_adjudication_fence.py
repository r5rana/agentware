"""ADJUDICATIONS — the row task 99 wave 16's FRONTMATTER-FENCE unification created.

The seventh sibling of `tests/_body_adjudication.py`, split off at the same seam
as the six before it and for the same mechanical reason the roster documents:
`_body_adjudication` carries a shrink-only `file_over_cap` row, so a wave that
grew it would be WORSENING one ratchet in the very commit whose purpose is to pay
five others down. The roster in `_body_adjudication_siblings` is the only wiring
that changes.

THE SHARED REASON, which is what makes this a seam rather than a bucket: wave 16
publishes ONE fence finder in `core.frontmatter` and adopts it at every site that
was hand-writing the same four lines — the fence was written NINE times across
six modules. `split_frontmatter` is the body the differ measures, and it lost
those lines to `_frontmatter_block`, which is exactly what a decomposition looks
like from the differ's side: a shorter host and a named receiver.

WHY ONLY ONE ROW. The other adopters (`_fm_upsert_relates_line`,
`_promote_frontmatter`, `_insert_org_lines`, `_stamp_work_artifact`,
`_ensure_work_frontmatter`, `skillmanifest._frontmatter_block`) each shed the
same four lines, and none of them fell below its adjudicated floor — a row is
declared where the gate MEASURES a shortfall, never pre-emptively, because a row
declaring a divergence the tree does not have is the stale-forgiveness shape the
whole ratchet exists to refuse.

Nothing here is forgiven. The row carries TWO obligations the gate enforces on
every run: the host plus its named receiver must clear the winner's length, AND
the host alone must clear its own measured floor — so the parser cannot be
hollowed out behind a bulky helper.
"""

_FRONTMATTER = "scripts/agentware_lib/core/frontmatter.py"

#: One receiver: the fence itself. It is a receiver of `split_frontmatter` and
#: not of the five other adopters because the differ tracks the body the DONOR
#: carried, and the donor carried the fence search inside each caller —
#: `split_frontmatter` is the one whose measured body the loss shows up in.
_FENCE_RECEIVERS = [_FRONTMATTER + "::" + n for n in (
    "_frontmatter_block",)]      # (head, block, tail) or None; the FIRST close

FENCE_ROWS = {
    "split_frontmatter": {
        "adjudication": "decomposed",
        "host": _FRONTMATTER,
        "floor_chars": 891,
        "absorbs": list(_FENCE_RECEIVERS),
        "rationale": "The parser opened with four lines of fence search that "
                     "were written verbatim at eight other sites, two of them "
                     "in this same module. Publishing the search as "
                     "`_frontmatter_block` is what let two ceiling rows be paid "
                     "down in other files (`_promote_frontmatter` 13 -> 6, "
                     "`_stamp_work_artifact` 14 -> 7) without either of them "
                     "growing a private copy of it, and it removes the class of "
                     "bug the nine copies made possible: two readers of the same "
                     "document disagreeing about whether it HAS a block. The "
                     "helper is total and three-valued on purpose — None for no "
                     "opening fence AND for a fence never closed, a real empty "
                     "block when the fences touch — and the host keeps every "
                     "field-parsing decision it ever had, which is what its own "
                     "floor is there to hold. 12 branches -> 8.",
    },
}
