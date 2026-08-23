"""ADJUDICATIONS — the rows task 99 wave 17's VERB-EMIT paydown created.

The eighth sibling of `tests/_body_adjudication.py`, split off at the same seam
as the seven before it and for the same mechanical reason the roster documents:
`_body_adjudication` carries a shrink-only `file_over_cap` row, so a wave that
grew it would be WORSENING one ratchet in the very commit whose purpose is to pay
four others down. The roster in `_body_adjudication_siblings` is the only wiring
that changes.

THE SHARED REASON, which is what makes this a seam rather than a bucket: all
three bodies are one shape stated three times — a `cmd_*` handler that COMPUTES a
result and RENDERS it, with the render half written once per output mode, so the
body length is the product of the decision count and the mode count rather than
their sum. Paying the ceiling row down means moving whole PHASES out of the
parent (assembly, refusal, render), so each parent falls below the length the
body differ adjudicated it at, and a split that does not NAME its receivers is
read by the differ as a lost body — which is precisely the silent downgrade this
gate exists to refuse.

`cmd_audit` MOVED here from the host rather than being re-adjudicated in place,
the same way `cmd_update` moved to the flag-verbs sibling at wave 15. Its task-56
row already named two receivers (`_audit_stale_section`, `_print_stale_section`);
wave 17 adds three more and lowers the host floor 2176 -> 1077, and doing that in
the host would have grown a file whose declared value may only fall. The original
rationale is preserved below in substance — nothing is forgiven in a move, and
the claim it makes about the flat check literal is now carried by the receiver
that OWNS that literal.

WHY `cmd_team` HAS NO ROW. It is the fourth body this wave split and the widest
of the four (24 branches), but it carries no divergence row at all — the differ
compares names present in BOTH trees, and the monolith's `cmd_team` and this
one's are not a divergent pair. A row is declared where the gate MEASURES a
shortfall, never pre-emptively: a row describing a divergence the tree does not
have is the stale-forgiveness shape the whole ratchet refuses.

Nothing here is forgiven. Each row carries TWO obligations the gate enforces on
every run: the host plus its named receivers must clear the winner's length, AND
the host alone must clear its own measured floor — so a parent cannot be
hollowed out behind bulky helpers.
"""

_AUDIT = "scripts/agentware_lib/ops/audit.py"
_SETUPCMD = "scripts/agentware_lib/features/setupcmd.py"
_RELEASEGATE = "scripts/agentware_lib/ops/releasegate.py"

#: Five receivers: the two the staleness split named at task 56, plus the three
#: wave 17 added — the flat check LITERAL (which is the body the parent's length
#: was mostly made of), and one renderer per output mode.
_AUDIT_RECEIVERS = [_AUDIT + "::" + n for n in (
    "_audit_stale_section",     # task 56: the optional --stale half, computed
    "_print_stale_section",     # task 56: ...and rendered
    "_audit_index_checks",      # the flat 13-row literal; a dropped line is a
                                # missing CHECK, which no length proxy can see
    "_audit_json_payload",      # omits the optional sections, never nulls them
    "_audit_print_text")]       # details for FAILING checks only

#: Six receivers, one per phase: five idempotent scaffold steps in a fixed order,
#: then the report. The repoint GUARD is a sixth phase and is a receiver too —
#: it is the longest of them, because its refusal has to say which two dirs are
#: in play and whether the one being abandoned is a live corpus.
_INIT_RECEIVERS = [_SETUPCMD + "::" + n for n in (
    "_init_dirs",               # the categories + the work artifacts
    "_init_seed_index",         # absent-only; NEVER clobbers a real index
    "_init_rosters",            # absent-only; an operator's ordering survives
    "_init_templates",          # .md only, absent-only, missing dir is not fatal
    "_init_refuse_repoint",     # 3 to refuse, None to proceed
    "_init_report")]            # one render per mode

#: Two receivers: the ONE skip emitter that replaced two byte-identical copies,
#: and the completed-run report.
_GATE_RECEIVERS = [_RELEASEGATE + "::" + n for n in (
    "_emit_skip",               # a SKIP is passed:true; both no-ops route here
    "_print_gate_report")]      # passing gates DO print their details

VERB_EMIT_ROWS = {
    "cmd_audit": {
        "adjudication": "decomposed",
        "host": _AUDIT,
        "floor_chars": 1077,
        "absorbs": list(_AUDIT_RECEIVERS),
        "rationale": "The staleness half of the audit -- deciding whether to "
                     "run the stale scan and how to render it -- became a "
                     "computing half (`_audit_stale_section`) and a rendering "
                     "half (`_print_stale_section`) at task 56. Wave 17 "
                     "finished the same split: the flat check LITERAL moved to "
                     "`_audit_index_checks` and the two renderers to "
                     "`_audit_json_payload` / `_audit_print_text`, leaving the "
                     "parent as load-outcome dispatch. The checks list is a "
                     "flat literal, so a dropped line there is an absent check "
                     "and not an error -- that is exactly what happened to the "
                     "package-version check once, and it is why the literal is "
                     "a named receiver with its own owning unit test asserting "
                     "the thirteen names in report order, rather than a loop "
                     "over a table. 13 branches -> 6.",
    },
    "cmd_init": {
        "adjudication": "decomposed",
        "host": _SETUPCMD,
        "floor_chars": 1329,
        "absorbs": list(_INIT_RECEIVERS),
        "rationale": "Five independently-idempotent scaffold steps, a refusal "
                     "guard and a two-mode report were inlined in one body, so "
                     "its length was the sum of six obligations and its branch "
                     "count the sum of their guards. Each step is now its own "
                     "function returning what the report renders -- which is "
                     "what makes each one's idempotence testable ON ITS OWN "
                     "rather than only through 'the dir works afterwards'. The "
                     "ORDER is the parent's remaining job and is load-bearing: "
                     "the guard runs AFTER the scaffold (so a refused repoint "
                     "still leaves the requested dir structurally valid) and "
                     "BEFORE the config write (which is the clobber it exists "
                     "to prevent). 19 branches -> 1.",
    },
    "cmd_gate_release": {
        "adjudication": "decomposed",
        "host": _RELEASEGATE,
        "floor_chars": 1360,
        "absorbs": list(_GATE_RECEIVERS),
        "rationale": "The two graceful no-ops -- the kill-switch and the absent "
                     "workspace -- carried BYTE-IDENTICAL emitters, differing "
                     "only in their reason string. That is not merely "
                     "duplication: a divergence between the two would be a "
                     "silent change in what a SKIP means on one of the two "
                     "paths, and a skip is reported as `passed: true` on "
                     "purpose (the gate makes no claim about a tree it did not "
                     "measure). One `_emit_skip` now owns that contract for "
                     "both, and the completed-run report moved to "
                     "`_print_gate_report`, leaving the parent as the tier "
                     "assembly. 13 branches -> 7.",
    },
}
