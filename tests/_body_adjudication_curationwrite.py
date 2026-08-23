"""ADJUDICATIONS -- the rows task 99 WAVE 13's curation-write paydown created.

The FIFTH sibling of `tests/_body_adjudication.py`, split off for the same
mechanical reason as the four before it and at the same seam the roster's own
docstring anticipates: `_body_adjudication_ceilingpaydown` is at its 600-line
`tests/**` cap, and that cap is a SHRINK-ONLY ratchet, so a wave that grew the
file past it would be worsening one ceiling in the commit whose purpose is to
pay three others down. The roster makes a new sibling cost the host nothing.

The seam is a WAVE, and that is a real obligation rather than a convenience: the
three rows below share a boundary proof, a family, and a single claim -- that the
curation write path's three ceiling rows were paid down without changing one
observable byte, at two boundaries and across 64 captured rows.

`cmd_learn` arrives with a `decomposed` row ALREADY -- task 91's, naming the
shared `--topic` grammar, the near-duplicate policy and the probe assembly. It is
MOVED here (not copied: the sibling tables are asserted disjoint, and the host
declaration is gone) because this wave adds six more receivers and lowers the
host floor, and growing `_body_adjudication.py` is precisely what its own
shrink-only `file_over_cap` row forbids. Task 91's finding is carried forward
verbatim in the rationale, because a moved row that loses why it exists is a
deleted row with extra steps.

`cmd_decide` fails the floor from the OTHER side -- the DONOR's body is the
longer one -- and it had no row at all, because until this wave its body still
carried the write chain inline. It gets one here for the same split.

Nothing here is forgiven. Each row still carries the TWO obligations the gate
enforces on every run: the host plus its named receivers must clear the winner's
length, AND the host alone must clear its own measured floor -- so a parent
cannot be hollowed out behind bulky helpers.

`_body_adjudication_siblings` merges this into `SIBLING_ROWS`, which the host
merges into `ADJUDICATIONS` -- the ONE name every consumer imports.
"""

CURATION_WRITE_ROWS = {}

# --------------------------------------------------------------------------
# DECOMPOSED -- task 99 wave 13: the CURATION WRITE PATH
# --------------------------------------------------------------------------
# Three ceiling rows paid down in one wave, and the three functions are one
# obligation seen from three angles: turn an operator's assertion into a
# written, indexed knowledge entry. `cmd_learn` (21 branches) and `cmd_decide`
# (17) had each grown their OWN copy of the same write chain -- refuse a
# collision, register-or-roll-back, emit -- which is why both ceiling rows named
# the same two helpers as their seam; `cmd_ingest` (20) is the batch form of the
# same transaction and was four stages written as one run of statements.
#
# The shared chain landed in a NEW sibling module, `features/curationwrite`,
# rather than as three more functions in `features/curationcmd`: the obligation
# changes at the index lock, and the host was 87 lines from its own 600-line cap
# with the three helpers still to add. Every receiver is named below, in the file
# it actually lives in, so a later port that loses a step reddens this row by
# name instead of quietly returning the curation write path to a shorter
# implementation than the one adjudicated here.
#
# The floors are the MEASURED host bodies after the split.

_CURATIONCMD = "scripts/agentware_lib/features/curationcmd.py"
_CURATIONWRITE = "scripts/agentware_lib/features/curationwrite.py"
_CURATIONINGEST = "scripts/agentware_lib/knowledge/curation/curationingest.py"

#: The task-91 receivers: ONE input grammar for both curation verbs.
_TOPIC_GRAMMAR = [_CURATIONCMD + "::" + n for n in (
    "_curation_topic",          # the whole --topic slug grammar + its notice
    "_near_duplicate_refusal",  # force short-circuit, threshold, refusal text
    "_entry_probe_text")]       # the doc-shaped probe assembly

#: The wave-13 receivers BOTH verbs now share: the transaction they run once
#: they hold the index lock, in the sibling module that owns it.
_WRITE_CHAIN = [_CURATIONWRITE + "::" + n for n in (
    "_refuse_existing_entry",   # duplicate id / path already indexed / file exists
    "_finish_curation_write",   # _do_add + unlink-on-error rollback + save
    "_emit_curation_entry")]    # the json envelope and the text success line

CURATION_WRITE_ROWS.update({
    "cmd_learn": {
        "adjudication": "decomposed",
        "host": _CURATIONCMD,
        "floor_chars": 1817,
        "absorbs": _TOPIC_GRAMMAR + _WRITE_CHAIN + [
            _CURATIONCMD + "::_curation_title",      # --title override, else derived
            _CURATIONCMD + "::_learn_body",          # --content, incl. the '-' stdin read
            _CURATIONCMD + "::_learn_provenance"],   # --source/--author coercion
        "rationale": "TASK 91 (carried forward verbatim, because a moved row "
                     "that loses why it exists is a deleted row with extra "
                     "steps): task 91 hardened `decide`'s `--topic`, which took "
                     "the string VERBATIM as both the decisions/ filename stem "
                     "and the entry id while `learn` had normalized since "
                     "260710-learning-repair-p1. The fix is a SHARE, not a "
                     "second implementation: a grammar the sibling verb skips is "
                     "indistinguishable from no grammar for whoever calls the "
                     "sibling, and two copies would drift on the next edit. So "
                     "`cmd_learn`'s three input blocks left it for the first "
                     "three named receivers, and `cmd_decide` calls the same "
                     "three. The parity row "
                     "`kb:one-topic-grammar-for-both-curation-verbs` carries the "
                     "mutation (make `cmd_decide` take `args.topic` verbatim "
                     "again) that reddens the decide half while the learn half "
                     "stays green -- the asymmetry itself, made executable. "
                     "TASK 99 WAVE 13: 21 branches -> 9, through six more "
                     "receivers. Three are the write chain `cmd_decide` had a "
                     "SECOND COPY of, now shared in `features/curationwrite`; "
                     "three are `learn`-only input coercions. `_learn_provenance` "
                     "is deliberately NOT shared with `decide`: `learn` REFUSES "
                     "a malformed `--author` and `decide` falls back to the "
                     "resolved handle, and that asymmetry is load-bearing for "
                     "the loop, which promotes `> DECISION:` markers unattended "
                     "and must not lose a recorded decision to a bad handle. The "
                     "emit stays OUTSIDE the lock here and INSIDE it in "
                     "`cmd_decide` -- pre-existing on both sides of the merge, "
                     "preserved because moving it changes how long a global lock "
                     "is held. Pinned by "
                     "`tests/unit/features/test_curationcmd.py` (the four shared "
                     "input contracts) and `test_curationcmd_handlers.py` (the "
                     "two lock spans, as ordered event logs), with "
                     "`test_learn_hardening.py` passing UNCHANGED across the "
                     "move.",
    },
    "cmd_decide": {
        "adjudication": "decomposed",
        "host": _CURATIONCMD,
        "floor_chars": 2307,
        "absorbs": _TOPIC_GRAMMAR + _WRITE_CHAIN + [
            _CURATIONCMD + "::_curation_title",      # shared with learn since wave 13
            _CURATIONCMD + "::_decide_author"],      # the FALLING-BACK author rule
        "rationale": "Task 99 wave 13: 17 branches -> 9. This row is new because "
                     "until this wave `cmd_decide` still carried the write chain "
                     "INLINE -- its own copy of the three-way collision refusal, "
                     "the `_do_add`-plus-unlink rollback and the emit -- so the "
                     "donor's longer body was paid for by a body that duplicated "
                     "`cmd_learn`. The duplication is what the two ceiling rows "
                     "diagnosed and what `features/curationwrite` removes: the "
                     "three named write-chain receivers are the SAME objects "
                     "both verbs now call, and the only difference between the "
                     "two calls is a noun ('learning' / 'decision'), a json key "
                     "and a label, all passed as arguments. `_decide_author` is "
                     "named separately rather than folded into `learn`'s "
                     "`_learn_provenance` on purpose -- see that row. What did "
                     "NOT move: the lock span (decide takes the index lock "
                     "BEFORE it validates its topic, and emits inside it) and "
                     "the auto-`decision` tag rule, both pinned by "
                     "`tests/unit/features/test_curationcmd_handlers.py` with "
                     "tripwire fixtures rather than by return codes, and both "
                     "byte-identical across the 64-row two-boundary capture the "
                     "wave diffed.",
    },
    "cmd_ingest": {
        "adjudication": "decomposed",
        "host": _CURATIONINGEST,
        "floor_chars": 1784,
        "absorbs": [_CURATIONINGEST + "::" + n for n in (
            "_ingest_dispatch_adapter",   # the CLOSED source set + one refusal route
            "_ingest_select",             # --only: absent admits all, blank admits none
            "_ingest_commit",             # load -> 3-way dedup -> register -> save
            "_ingest_print_text")],       # the text report, the json arm's counterpart
        "rationale": "Task 99 wave 13: 20 branches / 106 lines -> 4 / 63. The "
                     "four receivers are the four stages the body already was, "
                     "and the seam that matters is `_ingest_commit`: the ported "
                     "body's whole point is that the index lock spans "
                     "load -> dedup -> register -> save and NOTHING else, while "
                     "this tree's pre-port copy had widened it to cover adapter "
                     "parsing of an arbitrary path, the staging write, the "
                     "metric append and every line of output. Naming the "
                     "critical section as its own receiver makes that boundary a "
                     "function signature instead of a comment. "
                     "`_ingest_dispatch_adapter` keeps the source set CLOSED -- "
                     "an unknown `--source` is refused, never defaulted -- and "
                     "that arm is unreachable from the CLI because argparse's "
                     "`choices=` rejects it first, which is why the owning unit "
                     "file drives it directly. `_ingest_select` keeps ABSENT and "
                     "EMPTY distinct: no `--only` admits everything, `--only "
                     "' , '` admits nothing. The three dedup routes (id, content "
                     "fingerprint, and an id already taken earlier in the same "
                     "batch) each have a fixture only that route can explain in "
                     "`tests/unit/knowledge/test_curationingest.py` -- the "
                     "wave's non-vacuity sweep proved the obvious fixtures do "
                     "NOT separate them, and two were rewritten because of it.",
    },
})
