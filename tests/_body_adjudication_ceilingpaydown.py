"""ADJUDICATIONS — the rows a task-99 CEILING PAYDOWN created.

The third sibling of `tests/_body_adjudication.py`, split out at task 99 wave 7
for the same reason and at the same kind of seam as the two before it: the host
hit the ceiling its own debt row declares, and that row is SHRINK-ONLY, so a
wave that added rows there would be worsening a ratchet in the very commit whose
purpose is to pay one down.

The seam is an OBLIGATION, not a line count. Every row here was adjudicated for
one reason: task 99 is paying down the per-function modularity ceiling, and the
way you pay a function-over-cap row down is to move whole steps out of the body
— which shrinks the parent below the length the body differ adjudicated it at,
so the split has to NAME its receivers. The rows that stay in the host were each
adjudicated for a reason of their own (a plan module split, a port that could not
be verbatim, a superseded body, a deferred capability, two verbs sharing one
contract).

LATER WAVES ADD ROWS HERE, not in the host. That is what keeps the host's
shrink-only row falling instead of rising, and the roster in
`_body_adjudication_siblings` means the host's wiring never changes again.

Nothing here is forgiven. Each row still carries TWO obligations the gate
enforces on every run: the host plus its named receivers must clear the winner's
length, AND the host alone must clear its own measured floor — so a parent
cannot be hollowed out behind bulky helpers.

`_body_adjudication_siblings` merges this into `SIBLING_ROWS`, which the host
merges into `ADJUDICATIONS` — the ONE name every consumer imports.
"""

# --------------------------------------------------------------------------
# DECOMPOSED -- task 99 wave 7: the fanout lane lifecycle
# --------------------------------------------------------------------------
# Three ceiling rows paid down in one wave, and the three functions are the life
# of ONE lane: provision it, integrate it, tear it down. Each was a body whose
# steps were an ORDERED PIPELINE written as a run of statements, so the parent
# shrank by moving whole steps out -- not by dropping any of them. Every receiver
# is named below, so losing a step on the way through a later port reddens this
# row by name rather than silently returning the lane lifecycle to a shorter
# implementation than the donor's.
#
# The floors are the MEASURED host bodies after the split. They are what stops
# this from being a licence to keep hollowing the parents out: a decomposed row
# carries TWO obligations, and the second one pins the host alone.

_FANOUT = "scripts/agentware_lib/features/fanout.py"
_MQINTEGRATE = "scripts/agentware_lib/features/fanoutmq/mqintegrate.py"
_MQTEARDOWN = "scripts/agentware_lib/features/fanoutmq/mqteardown.py"

CEILING_PAYDOWN_ROWS = {
    "cmd_fanout_merge_queue": {
        "adjudication": "decomposed",
        "host": _MQINTEGRATE,
        "floor_chars": 2266,
        "absorbs": [_MQINTEGRATE + "::" + n for n in (
            "_mq_queue_opts",           # the whole option surface + gate precedence
            "_mq_dry_run_report",       # the --dry-run plan, both formats
            "_mq_prepare_integration",  # the both-sides integration-branch preflight
            "_mq_run_gates",            # the stop-at-first-failure gate loop
            "_mq_process_lane")],       # the ORDERED single-lane pipeline
        "rationale": "Task 99 wave 7 paid down the widest body left in the tree "
                     "after wave 6: 31 branches / 149 lines, over BOTH ceiling "
                     "caps, now 10/60. The handler is a QUEUE wrapped around a "
                     "PIPELINE, and the pipeline's step order (skip -> package "
                     "-> KB -> gates -> gate-artifact commit -> mark merged) was "
                     "encoded in nothing but statement adjacency. The five "
                     "receivers are named individually because each is a step "
                     "an operator can lose: drop `_mq_run_gates` and lanes "
                     "integrate ungated; drop `_mq_prepare_integration` and the "
                     "KB side of the integration branch is never created, "
                     "silently degrading every lane to package-only. Note what "
                     "did NOT get its own receiver: the per-lane pipeline stayed "
                     "ONE function (`_mq_process_lane`) rather than five named "
                     "arms, because splitting it is exactly what would move the "
                     "step order out of the code and into a caller where nothing "
                     "states it -- five arms each read correct in isolation "
                     "while a transposition between two of them is invisible. "
                     "Nothing was dropped, and that is PROVEN rather than "
                     "asserted: `evidence/task99/fanoutmq_boundary.py` captures "
                     "112 rows across the CLI and function boundaries -- "
                     "including the lane registry bytes, both repos' porcelain, "
                     "the integration monolith and the integration KB's id set "
                     "after every row -- and the before/after diff is EMPTY at "
                     "158,779 bytes. The order itself is pinned by "
                     "`tests/unit/features/test_mqintegrate_lane.py`, whose rows "
                     "are built on lanes rigged to fail TWO steps at once so the "
                     "assertion names which one won, and the non-vacuity sweep "
                     "transposes each pair for real (86 mutations, 86 RED).",
    },
    "cmd_fanout_teardown": {
        "adjudication": "decomposed",
        "host": _MQTEARDOWN,
        "floor_chars": 1662,
        "absorbs": [_MQTEARDOWN + "::" + n for n in (
            "_teardown_one_lane",   # the whole per-lane SAFETY order + its stderr
            "_teardown_report")],   # the outcome renderer, text/json asymmetry
        "rationale": "Task 99 wave 7: 16 branches -> 5. This is the only place "
                     "in the package that runs `branch -D`, so its shrink is the "
                     "one that would matter most if a step had been dropped "
                     "rather than moved. `_teardown_one_lane` holds the ENTIRE "
                     "R-GIT-02 safety order -- the blocker is consulted before "
                     "anything is removed, --dry-run answers before that, "
                     "worktrees go before their branches (a branch checked out "
                     "by a live worktree cannot be deleted), and the registry "
                     "entry is dropped LAST so a failure anywhere above leaves "
                     "the lane re-runnable. It writes its own stderr because for "
                     "a refusal the MESSAGE is the contract: teardown never "
                     "prompts, so the reason plus the --force escape is the "
                     "operator's only handoff. `_teardown_report` keeps the "
                     "text/json asymmetry (text drops the rows stderr already "
                     "carried; json keeps every one). Both are pinned by "
                     "`tests/unit/features/test_mqteardown.py`, including rows "
                     "that transpose the safety order -- where a transposition "
                     "is not cosmetic, it deletes an operator's uncommitted "
                     "work -- and the boundary capture is byte-identical.",
    },
    "cmd_fanout_spin_up": {
        "adjudication": "decomposed",
        "host": _FANOUT,
        "floor_chars": 2014,
        "absorbs": [_FANOUT + "::" + n for n in (
            "_spin_up_preflight",     # repo/base resolution + the work-tree guard
            "_print_spin_up_lanes")],  # the four-line-per-lane text renderer
        "rationale": "Task 99 wave 7: 13 branches -> 6. The two receivers are "
                     "the two halves that are not per-lane: a WHOLE-COMMAND "
                     "preflight (both repos resolved, both base refs defaulted "
                     "through each other, both work trees proven) and the text "
                     "renderer. The preflight is factored out rather than "
                     "inlined per lane because every failure it can find is "
                     "fatal for EVERY feature named on the command line -- "
                     "reporting it per-lane would repeat one message N times and "
                     "still provision nothing. Losing `_spin_up_preflight` would "
                     "drop the work-tree guard and let spin-up attempt "
                     "`worktree add` against a plain directory; losing "
                     "`_print_spin_up_lanes` would drop the error-row skip that "
                     "keeps a failed lane from being printed twice. Both are "
                     "pinned by `tests/unit/features/test_fanout.py` and the "
                     "boundary capture's 15 spin-up rows.",
    },
}

# --------------------------------------------------------------------------
# DECOMPOSED -- task 99 wave 8: the KB-git sync path
# --------------------------------------------------------------------------
# Four ceiling rows paid down in one wave, across two modules that are two ends
# of ONE workflow: pull the teammate's entries in, push yours back out, and
# finish the prose merge when the two collide. Five bodies shrank, and all five
# shrank for the same two reasons -- whole DISPOSITIONS moved into named
# functions, and a two-line "first line of the failure" idiom written at six
# sites collapsed onto one shared reduction.
#
# `_first_line` is named as a receiver on three rows even though it is small,
# and that is deliberate: it is the ONLY receiver that lives in a different
# module from its hosts, so it is the one a later port is most likely to drop
# on the floor. Losing it reddens these rows by name instead of quietly
# returning three operator-facing notes to a shape nobody chose.
#
# The floors are the MEASURED host bodies after the split. A decomposed row
# carries TWO obligations and the second one pins the host alone, so this is not
# a licence to keep hollowing the parents out.

_KBSYNC = "scripts/agentware_lib/ops/kbsync.py"
_KBPULL = "scripts/agentware_lib/ops/kbpull.py"
_KBGIT = "scripts/agentware_lib/ops/kbgit.py"
_FIRST_LINE = _KBGIT + "::_first_line"

CEILING_PAYDOWN_ROWS.update({
    "kb_git_push_once": {
        "adjudication": "decomposed",
        "host": _KBSYNC,
        "floor_chars": 2793,
        "absorbs": [_KBSYNC + "::" + n for n in (
            "_push_attempt",                # attempt + the two terminal arms
            "_push_after_clean_rebase",     # the conflict-free replay's tail
            "_prose_conflict_disposition",  # abort vs pause, C-1
            "_resolve_derived_conflict")]   # rebuild, stage, gate, continue, push
        + [_FIRST_LINE],
        "rationale": "Task 99 wave 8 paid down the widest body left in the lib "
                     "after wave 7: 22 branches, now 6. What remains in the "
                     "parent is the ORDER and nothing else -- attempt, classify, "
                     "rebase, classify the conflicts, dispatch -- and each "
                     "receiver is named individually because each is a "
                     "disposition an operator can lose. Drop `_push_attempt` "
                     "and a push that failed for being OFFLINE stops being a "
                     "graceful C-4 skip and starts rebasing the operator's tree; "
                     "drop `_resolve_derived_conflict` and the nothing-lost gate "
                     "(C-2) goes with it, so a lossy merge reaches the remote; "
                     "drop `_prose_conflict_disposition` and the agent-free "
                     "contract (C-1) loses its abort arm. Nothing was dropped, "
                     "and that is PROVEN rather than asserted: "
                     "`evidence/task99/kbsync_boundary.py` captures 112 rows "
                     "across the CLI and function boundaries -- including each "
                     "repo's porcelain, branch list, rebase-in-progress state, "
                     "commit subjects and entry-id set after every row -- and "
                     "the before/after diff is EMPTY at 32,704 bytes. The order "
                     "is pinned by `tests/unit/ops/test_kbsync.py`, and the "
                     "non-vacuity sweep transposes it for real (86 mutations, "
                     "86 RED).",
    },
    "cmd_kb_git_push": {
        "adjudication": "decomposed",
        "host": _KBSYNC,
        "floor_chars": 1357,
        "absorbs": [_KBSYNC + "::_resolve_sync_target"],
        "rationale": "Task 99 wave 8: 13 branches -> 7. The receiver is the "
                     "four-step precondition chain this verb shares with "
                     "`kb-git merge-continue`, which was written inline at BOTH "
                     "call sites and is now enforced once. It is named as a "
                     "receiver rather than folded silently because it holds two "
                     "guards whose loss is not visible in ordinary use: C-3, "
                     "which REFUSES to push the agentware package repo, and C-4, "
                     "which degrades a non-repo target to a local-only skip "
                     "instead of blocking work. Their ORDER is load-bearing too "
                     "-- the refusal precedes the upstream check, so a package "
                     "repo with no upstream refuses loudly rather than skipping "
                     "quietly -- and it is pinned on a fixture where both would "
                     "fire, in `tests/unit/ops/test_kbsync_handlers.py`.",
    },
    "cmd_kb_git_merge_continue": {
        "adjudication": "decomposed",
        "host": _KBSYNC,
        "floor_chars": 824,
        "absorbs": [_KBSYNC + "::_resolve_sync_target"],
        "rationale": "Task 99 wave 8: the second caller of the shared "
                     "precondition chain, and the reason the chain is "
                     "parameterized rather than copied. This verb takes the C-3 "
                     "refusal and the C-4 degrade but NOT the upstream check, "
                     "because a paused rebase must stay finishable on a repo "
                     "whose remote has since gone away. That difference is one "
                     "keyword argument here and one assertion in "
                     "`tests/unit/ops/test_kbsync_handlers.py`; before the "
                     "split it was two divergeable copies of the same four "
                     "guards. This row carried no ceiling debt of its own -- it "
                     "shrank as a side effect of paying down its sibling's -- "
                     "and it is adjudicated anyway, because the differ cannot "
                     "tell a shrunken body from a dropped capability.",
    },
    "kb_git_merge_continue": {
        "adjudication": "decomposed",
        "host": _KBSYNC,
        "floor_chars": 2585,
        "absorbs": [_FIRST_LINE],
        "rationale": "Task 99 wave 8: 13 branches -> 12, the smallest shrink of "
                     "the wave (18 characters) and the one that says most about "
                     "why this table exists. The body kept every one of its "
                     "eleven fail-loud dispositions -- they ARE the disposition "
                     "set, which is why the ceiling row's own seam said so -- "
                     "and lost only the two lines of the `rebase --continue` "
                     "failure idiom, which moved to the shared reduction. To the "
                     "differ an 18-character shrink is indistinguishable from a "
                     "dropped branch, so the receiver is named and the floor "
                     "pins the eleven that stayed.",
    },
    "cmd_kb_git_pull": {
        "adjudication": "decomposed",
        "host": _KBPULL,
        "floor_chars": 1276,
        "absorbs": [_KBPULL + "::_pull_preconditions", _FIRST_LINE],
        "rationale": "Task 99 wave 8: 13 branches -> 5. `_pull_preconditions` "
                     "holds the three guards a fast-forward requires and, more "
                     "importantly, their ORDER: a repo with no upstream is "
                     "reported as such even when it is ALSO dirty, because "
                     "'you have no remote to pull from' is the actionable fact "
                     "and 'commit your work first' would send the operator to "
                     "fix the wrong thing. Losing that receiver would drop the "
                     "clean-tree precondition, and a fast-forward onto "
                     "uncommitted writes is the one way this verb can destroy "
                     "work. The second receiver is the shared first-line "
                     "reduction, which this handler uses TWICE -- once for the "
                     "failed-fetch notice and once for the success summary. "
                     "Pinned by `tests/unit/ops/test_kbpull.py`, including the "
                     "dirty-AND-no-upstream fixture that makes the order "
                     "falsifiable.",
    },
})


# --------------------------------------------------------------------------
# DECOMPOSED -- task 99 wave 9: the RECALL READ-PATH
# --------------------------------------------------------------------------
# Five ceiling rows paid down in one wave, spanning the four modules that turn a
# query into a ranked, budgeted payload: the pin that decides what leads, the
# corpus the work scope ranks, the vector cache the semantic mode reads, and the
# verb that assembles all of it. Each parent shrank by moving whole PHASES out,
# so every receiver is named -- a phase that goes missing on a later port must
# redden this row rather than quietly returning a shorter answer.

_PINPOLICY = "scripts/agentware_lib/retrieval/pinpolicy.py"
_RECALLVERB = "scripts/agentware_lib/retrieval/recallcmd.py"
_VECTORCACHE = "scripts/agentware_lib/retrieval/vectorcache.py"

CEILING_PAYDOWN_ROWS.update({
    "_apply_skill_pin": {
        "adjudication": "decomposed",
        "host": _PINPOLICY,
        "floor_chars": 1783,
        "absorbs": [_PINPOLICY + "::" + n for n in (
            "_first_qualifying_skill",   # threshold + the non-redundant `> 0`
            "_protected_learning_ids",   # the PRE-pin window, by identity
            "_window_victim",            # who may be evicted, and who may not
            "_reprotect_learnings")],    # the repair AND its own preconditions
        "rationale": "Task 99 wave 9: 22 branches in sixty lines -- the densest "
                     "body left in the lib -- now 6. The four receivers are the "
                     "four distinct questions that were interleaved, and each is "
                     "a rule an operator can lose. Drop "
                     "`_protected_learning_ids` and the pin silently starts "
                     "evicting the prior problem-and-error context R-RET-03 "
                     "guarantees is always included, to make room for a "
                     "procedure -- the wrong trade in exactly the situation "
                     "where both are needed. Drop `_window_victim` and the "
                     "repair picks a learning or the pinned skill as its own "
                     "victim, undoing in one line what the other three "
                     "receivers exist to protect. `_first_qualifying_skill` "
                     "keeps the `score > 0` test that is NOT redundant with the "
                     "threshold: a `pin_floor` of 0.0 makes the threshold 0.0, "
                     "and without it an entry that matched nothing would be "
                     "pinned to position 0. `_reprotect_learnings` owns its own "
                     "preconditions rather than being guarded at the call site, "
                     "because it is the one place that knows a repair is "
                     "meaningless without a window. Nothing was dropped, and "
                     "that is PROVEN: `evidence/task99/recall_boundary.py` "
                     "drives the pin over eleven window shapes and the "
                     "before/after capture is byte-identical at 73,728 bytes. "
                     "The learnings-are-never-evicted POSTCONDITION is asserted "
                     "directly by `tests/unit/retrieval/test_pinpolicy.py` "
                     "rather than inferred from the resulting order, including "
                     "the one case where it cannot hold and the repair must "
                     "give up.",
    },
    "build_recall_payload": {
        "adjudication": "decomposed",
        "host": _RECALLVERB,
        "floor_chars": 2036,
        "absorbs": [_RECALLVERB + "::" + n for n in (
            "_budget_walk",        # the greedy SKIP-NOT-BREAK walk + record shape
            "_team_scope_block")],  # the five-way org-mode disjunction, once
        "rationale": "Task 99 wave 9: 14 branches -> 5. `_budget_walk` carries "
                     "INV-5, the single most losable property in the module: an "
                     "entry that does not fit is SKIPPED and the walk continues, "
                     "so a smaller lower-ranked entry still lands. A `break` "
                     "reads identically and passes every test whose oversized "
                     "entry happens to rank last, which is why the fixtures put "
                     "one in the middle. It also owns the pointers-only overflow "
                     "contract and the additive-only provenance keys -- both "
                     "shapes the loop parses. `_team_scope_block` is the ONE "
                     "definition of 'org mode is in play here'; written inline "
                     "it was a five-operand disjunction, and a caller testing "
                     "four of the five would publish the scope block for some "
                     "invocations and not others. Byte-identical across the "
                     "wave's twelve payload-boundary rows.",
    },
    "cmd_recall": {
        "adjudication": "decomposed",
        "host": _RECALLVERB,
        "floor_chars": 2358,
        "absorbs": [_RECALLVERB + "::" + n for n in (
            "_print_recall_text",         # the restored monolith renderer
            "_print_recall_overflow",     # its pointers-only overflow half
            "_work_scope_ranked",         # BM25 + the plan-status weighting
            "_work_scope_budget_walk",    # the work arm's own budget walk
            "_work_scope_payload",        # the ephemeral-corpus payload
            "_run_work_scope_recall")],   # the whole second command
        "rationale": "Task 99 wave 9 re-adjudicates the task-54 row: 19 branches "
                     "-> 10. The four new receivers are the `--scope work` arm, "
                     "which was a SECOND COMMAND inlined into this one -- ten of "
                     "the nineteen branches, sharing only the argument "
                     "validation with the curated path and none of its ranking, "
                     "its payload shape or its renderer. `_work_scope_ranked` "
                     "holds the plan-status multiplier and the "
                     "last-key-first stable-sort order that implements score "
                     "desc / created desc / id asc; lose it and a superseded "
                     "plan competes with the plan that replaced it on equal "
                     "terms forever. `_work_scope_budget_walk` is the work arm's "
                     "own INV-5 walk over a different record shape (heading as "
                     "summary, literal category 'work'). "
                     "`_work_scope_payload` keeps the empty-corpus answer a "
                     "well-formed payload rather than an error. The two original "
                     "print receivers are unchanged and still named. Proven "
                     "byte-identical across 34 CLI rows including every "
                     "validation refusal, both formats and four work-scope "
                     "budgets.",
    },
    "build_vector_cache": {
        "adjudication": "decomposed",
        "host": _VECTORCACHE,
        "floor_chars": 1911,
        "absorbs": [_VECTORCACHE + "::" + n for n in (
            "_plan_vector_reuse",   # the per-entry reuse decision + its distrust
            "_assemble_vectors")],  # the vectors map and its single dimension
        "rationale": "Task 99 wave 9: 14 branches -> 5. Three sequential phases "
                     "with one artifact each -- PLAN what to reuse, EMBED what "
                     "is left, ASSEMBLE the file -- and only the middle one "
                     "stayed inline, because it is the only phase that can fail "
                     "on somebody ELSE's contract and the count check belongs "
                     "next to the call that asked. `_plan_vector_reuse` holds "
                     "the reuse rule (id AND content fingerprint AND stored "
                     "shape) and its distrust of the cache file, which is JSON "
                     "on disk that anything may have rewritten: a malformed row "
                     "must re-embed rather than raise. `_assemble_vectors` holds "
                     "the one-dimension-per-cache invariant and the error that "
                     "NAMES the drifting entry -- a cache mixing two widths "
                     "scores half the corpus meaninglessly, and silently. Pinned "
                     "by `tests/unit/retrieval/test_vectorcache.py`, whose "
                     "stand-in backend breaks its contract in both directions "
                     "because no real one can be relied on to.",
    },
})


# --------------------------------------------------------------------------
# DECOMPOSED -- task 99 wave 10: the benchmark harness family
# --------------------------------------------------------------------------
# Four ceiling rows paid down in one wave (a fifth, `benchledger::cmd_bench_lint`,
# needs no row here -- its body was IDENTICAL in both trees, so the differ never
# carried one; a sixth, `goldfixture::load_gold_fixture`, already had a
# `decomposed` row and was extended in place, in `_body_adjudication_shimretirement`,
# because the two sibling tables must stay disjoint).
#
# Three of the four hosts are the `eval` verb's handler family, and their
# receivers sit in a NEW sibling module, `retrieval/evalsuites.py`. That is not a
# stylistic choice: `evalcmd.py` was at 592 of its 600-line cap, so its three
# rows could not be paid down by adding helpers to it at all. The direction is
# one-way -- `evalcmd` imports `evalsuites`, never the reverse -- and the
# ablation instrument (`evaluate_ablation`) deliberately STAYS in `evalcmd` so no
# renderer over there ever needs to reach back for it and no cycle is possible.
#
# The floors are the MEASURED host bodies after the split, and they are what stops
# this from being a licence to hollow the parents out behind bulky helpers.
#
# Pure refactor PROVEN, not asserted: `evidence/task99/evalbench_boundary.py`
# captures 104 rows across the CLI and function boundaries -- every `eval` suite,
# format, gate and refusal, the gold-fixture loader's whole error taxonomy, both
# suite runners under all four exception kinds, `bench lint` on both sides of its
# cluster bound and `bench swe`'s live arm with the agent spawn injected -- and
# the capture is BYTE-IDENTICAL before and after, with its own determinism pinned
# first by a before->before re-run.

_EVALCMD = "scripts/agentware_lib/retrieval/evalcmd.py"
_EVALSUITES = "scripts/agentware_lib/retrieval/evalsuites.py"
_BENCHSWE = "scripts/agentware_lib/retrieval/bench/benchswe.py"

CEILING_PAYDOWN_ROWS.update({
    "cmd_eval": {
        "adjudication": "decomposed",
        "host": _EVALCMD,
        "floor_chars": 1121,
        "absorbs": [
            _EVALCMD + "::_run_ablate_suite",       # the two-strategy lift arm
            _EVALCMD + "::_run_default_suite",      # the own-gold arm + the A/B
            _EVALSUITES + "::print_ablate_report",  # the per-metric lift table
            _EVALSUITES + "::print_default_report"],  # the own-gold report
        "rationale": "Task 99 wave 10: 21 branches / 120 lines -> 10 / 35. The "
                     "handler was a DISPATCHER with two of its four arms "
                     "inlined -- its own docstring already named the other two "
                     "as sub-runners -- so the two that were written out are now "
                     "named beside them. Each receiver is a step an operator can "
                     "lose: drop `_run_default_suite` and the plain `eval` arm "
                     "takes the ablate path; drop `print_ablate_report` and the "
                     "lift table stops printing while every metric still "
                     "records. Two properties in the receivers exist nowhere "
                     "else: the ablate arm REFUSES two identical strategies "
                     "before scoring anything (a delta of exactly zero for every "
                     "metric reads as a measurement rather than a mistake), and "
                     "the default arm's `_gate_exit(gate, ab)` takes TWO "
                     "verdicts, so a run that passes the regression gate and "
                     "fails the clean-HEAD A/B still exits non-zero -- dropping "
                     "`ab` there changes no report at all. Both are pinned by "
                     "`tests/unit/retrieval/test_evalcmd_owngold.py`.",
    },
    "_run_longmemeval_suite": {
        "adjudication": "decomposed",
        "host": _EVALCMD,
        "floor_chars": 1347,
        "absorbs": [
            _EVALSUITES + "::validate_lme_args",           # the 4 ordered guards
            _EVALSUITES + "::record_and_gate_suite",       # the shared ledger block
            _EVALSUITES + "::print_longmemeval_report"],   # the per-category report
        "rationale": "Task 99 wave 10: 18 branches -> 4. The body was three "
                     "obligations stacked in one function -- validate, record "
                     "and gate, report -- and the middle one was a HAND-COPY of "
                     "the gold-fixture suite's, differing in exactly one "
                     "character sequence (the suite name stamped on the gate). "
                     "It is a parameter now, so the two suites cannot drift "
                     "apart in what they record. `validate_lme_args` is named "
                     "individually because its four refusals have an ORDER that "
                     "is a contract -- dataset, then --top-k, then --as-of, then "
                     "--limit -- and an invocation can be wrong in several ways "
                     "at once, so which message it gets is what an operator "
                     "debugs against. What did NOT move is the except-chain: "
                     "this suite catches (ValueError, OSError, JSONDecodeError) "
                     "because it reads an OPERATOR-SUPPLIED dataset file, while "
                     "the gold-fixture arm catches only ValueError. Lifting "
                     "'the guards' into one shared helper would collapse that, "
                     "and the only input separating them is an OSError from the "
                     "frozen scorer. Pinned by "
                     "`tests/unit/retrieval/test_evalcmd.py`.",
    },
    "_run_gold_fixture_suite": {
        "adjudication": "decomposed",
        "host": _EVALCMD,
        "floor_chars": 1380,
        "absorbs": [
            _EVALSUITES + "::validate_scoring_args",       # --top-k + --as-of
            _EVALSUITES + "::print_gold_fixture_report",   # the frozen-suite report
            _EVALSUITES + "::print_recorded_line"],        # the shared ledger note
        "rationale": "Task 99 wave 10: 13 branches -> 5, through the same three "
                     "receivers as its longmemeval sibling. "
                     "`validate_scoring_args` returns the RESOLVED `as_of` "
                     "rather than a bare verdict, which is the point of the "
                     "split: the default and the validation are ONE decision, "
                     "and a caller that got back only 'valid' would re-derive "
                     "the date -- which is how two arms of one command end up "
                     "scoring at two different dates. `print_recorded_line` is "
                     "named because it is now the SINGLE copy of a line four "
                     "reports print; four copies drift. The report keeps the "
                     "corpus FINGERPRINT that no other report prints, because "
                     "the corpus ships frozen and a fingerprint that moved means "
                     "the fixture moved, not that the operator's KB grew. "
                     "Pinned by `tests/unit/retrieval/test_evalsuites.py`.",
    },
    "cmd_bench_swe": {
        "adjudication": "decomposed",
        "host": _BENCHSWE,
        "floor_chars": 1410,
        "absorbs": [
            _BENCHSWE + "::_print_swe_dry_run",   # the plan, human form
            _BENCHSWE + "::_run_swe_arm",         # seeds x tasks for ONE arm
            _BENCHSWE + "::_run_swe_task",        # one spawn, one grade
            _BENCHSWE + "::_print_swe_report"],   # the completed-run report
        "rationale": "Task 99 wave 10: 20 branches / 119 lines -> 8 / 51. The "
                     "live arm was a TRIPLE-NESTED loop, and its nesting order "
                     "is the measurement design rather than an accident: the "
                     "pass RATE is per SEED (one number per full sweep of the "
                     "suite) while turns and tokens are averaged over every "
                     "individual RUN. Collapsing the two levels produces a "
                     "number that looks like a pass rate and is not one, which "
                     "is what `swe_aggregate_seeds` then reports a spread over "
                     "-- so `_run_swe_arm` and `_run_swe_task` are named "
                     "separately, at exactly that boundary. `_run_swe_task` also "
                     "holds the two degradations the benchmark depends on: an "
                     "agent that TIMES OUT and one whose binary is missing both "
                     "score zero turns and are then GRADED ANYWAY, because an "
                     "abstain task passes by leaving the repo alone and "
                     "swallowing the run would drop it from the denominator. "
                     "`_print_swe_report` prints its delta on `is not None`, so "
                     "a delta of exactly 0.0 -- the most interesting NEGATIVE "
                     "result this benchmark produces -- is not dropped as falsy. "
                     "Pinned by `tests/unit/retrieval/test_benchswe.py`, which "
                     "injects the spawn at the module's own `subprocess` name "
                     "rather than patching the real module (that would also "
                     "redirect `ledger.git_info()`, three statements later and "
                     "OUTSIDE the try).",
    },
})

