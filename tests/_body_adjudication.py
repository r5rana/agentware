#!/usr/bin/env python3
"""ADJUDICATIONS — the hand-audited half of the body differ (task 30).

`_body_differ.py` computes every divergence mechanically and defaults each row
to the length proxy: the longer body wins, and the merged tree must keep at
least that many characters. That default is correct for 234 of the 242 rows.

The other eight are rows where the merged tree is ALREADY shorter than the
adjudicated winner, and each one was read line by line before it was written
down here. Three are the plan's own module splits, one is the parser
decomposition every verb PORT feeds (tasks 37..54), three are decisions the plan
made deliberately, and one is a real donor capability this tree has not adopted
yet. Nothing here forgives a row: every entry still leaves a floor the gate
enforces on every run.

Keep this table beside the mechanics, not inside them. A reviewer auditing "what
was allowed to shrink, and who said so" should be able to read one short file
and see all of it, with the reasoning attached to the row rather than buried in
a commit message.

Adjudication kinds and what each obliges (see `_body_differ` for the mechanics):

  decomposed  -> `host` + `absorbs` + `floor_chars`; the split's receivers are
                 named, must all still exist, and their lengths pay for the
                 shrunken parent -- while the parent itself keeps a floor, so it
                 cannot be emptied out behind them.
  superseded  -> `floor_chars`; a plan decision replaced the winner's body, so
                 the row now ratchets against the replacement.
  deferred    -> `floor_chars` + `follow_up`; the winner's body has not landed,
                 a named task owns it, and it may not get worse meanwhile.
"""

# --------------------------------------------------------------------------
# DECOMPOSED -- the plan's own splits (tasks 21 and 22)
# --------------------------------------------------------------------------

# Task 21 split the dream STEP module into a package with one file per step.
# The health-gate step's nine checks each became a named `_dream_hg_check_*`
# helper, so the parent shrank 4854 -> 699 while gaining nine receivers. Naming
# them individually is what makes this a split rather than a deletion: drop any
# one check and the gate reddens on a missing absorber.
_DREAM_HG = "scripts/agentware_lib/features/dreamsteps/stephealthgate.py"
_DREAM_HG_CHECKS = (
    "_dream_hg_check_config", "_dream_hg_check_cron", "_dream_hg_check_gitignore",
    "_dream_hg_check_identity", "_dream_hg_check_kb_structure",
    "_dream_hg_check_kdir_drift", "_dream_hg_check_rebase",
    "_dream_hg_check_sentinel", "_dream_hg_check_templates",
)

# Task 21 split the index module into read / write / rebuild, and task 22 landed
# duplicate-id dedup and quarantine-and-continue on top. The per-entry row build
# moved out to `_frontmatter_row`, which in turn calls `_entry_title_summary`
# (the skills title/summary backfill) and `_apply_org_facet` (the origin/team
# columns) -- the whole chain the donor's body used to hold inline.
_INDEX_REBUILD = "scripts/agentware_lib/knowledge/index/indexrebuild.py"
_INDEX_ROW_CHAIN = ("_frontmatter_row", "_entry_title_summary", "_apply_org_facet")

# Task 21 split `cmd_config`'s ~590-line guard-clause chain into per-setting
# handlers behind CONFIG_ROUTES: 19172 -> 720 chars, with every guard clause now
# a (predicate, handler) pair in a sibling module. Every handler in the route
# table is named here, so collapsing the table back into one function, or losing
# a setting on the way through a later port, reddens this row by name.
_CFG = "scripts/agentware_lib/features/configcmd/"
_CFG_HANDLERS = (
    [_CFG + "cfgdream.py::" + n for n in (
        "_cli_only", "_dream_max_runtime_only", "_dream_only",
        "_dream_schedule_only", "_set_cli", "_set_dream",
        "_set_dream_max_runtime", "_set_dream_schedule")]
    + [_CFG + "cfgidentity.py::" + n for n in (
        "_record_onboarding", "_set_knowledge_dir",
        "_set_skill_propose_threshold", "_set_user_handle",
        "_skill_propose_threshold_only", "_user_handle_only")]
    + [_CFG + "cfgkbgit.py::" + n for n in (
        "_kb_autocommit_only", "_kb_push_only", "_set_autocommit", "_set_push")]
    + [_CFG + "cfgorg.py::" + n for n in (
        "_handle_org", "_kb_mode_only", "_org_triggered", "_set_kb_mode")]
    + [_CFG + "cfgphases.py::" + n for n in ("_handle_phase", "_phase_triggered")]
    + [_CFG + "cfgretrieval.py::" + n for n in (
        "_embed_model_only", "_embedder_only", "_retrieval_mode_only",
        "_set_embed_model", "_set_embedder", "_set_mode", "_set_retrieval_mode")]
    + [_CFG + "cfgshow.py::_show_config"]
)


# Every PORT (tasks 37..54) moves one verb's argparse block out of the monolith's
# `build_parser` and into that verb's own `register_<verb>` in the lib, where
# `agentware_lib.cli.assemble_parser` walks the frozen registration tuple and
# calls it. The parent therefore shrinks by construction on every port — the
# whole point of the modularisation — while the SURFACE is unchanged, and that is
# exactly what a length proxy cannot tell apart from a deleted flag. So the row
# names the receiving registrar of each ported verb: the block has to be findable
# at its declared path or the row reddens by name.
#
# MAINTENANCE PROTOCOL (each port, in the SAME commit as its promotion):
#   1. append the verb's registrar to _PORTED_REGISTRARS (the assembler itself is
#      a receiver too: the monolith INLINED the add_parser sequence that
#      assemble_parser now walks);
#   2. re-pin `floor_chars` to the host's post-move length.
# The COMBINED floor (winner_chars, 67081) never moves, so step 2 can only ever
# be paid for by step 1 — a parent emptied out without a named receiver still
# fails. At task 56 the monolith is gone and the host becomes the lib's own
# build_parser, whose absorbers are then the complete registrar set.
_ASSEMBLER = "scripts/agentware_lib/cli.py::assemble_parser"
_PORTED_REGISTRARS = (
    _ASSEMBLER,   # the walk over the frozen registration tuple that CALLS them
    "scripts/agentware_lib/features/migratecmd.py::register_migrate",   # task 37
    "scripts/agentware_lib/knowledge/relatecmd.py::register_relate",    # task 38
)

# Task 39 ported `plan lint` and could NOT do it as a verbatim move. The
# monolith's `lint_plan` is one 270-line, 44-branch body; the ceilings are 120
# lines and 12 branches per function and the debt fixture is shrink-only, so
# landing it whole was a hard fail with no row available. It is therefore split
# one receiver per RULE GROUP -- the seam the R1..R16 numbering already implies
# -- and the parent is reduced to the ORDER the rules run in, which is itself
# part of the contract (findings are returned as ordered lists).
#
# A length proxy cannot tell that split apart from sixteen deleted rules, so
# every receiver is named. Three of them do not live in the host file: R12..R16
# are `ops.plangovern` (the rules that need the plan's PLACE in the KB and ride
# the WARN->ERROR schema ramps), and the finding-record constructor that absorbed
# the monolith's nested `add` closure is `ops.planmarkers`. Naming them across
# files is the point -- moving a rule to a new module is fine, losing one is not.
_PLANLINT = "scripts/agentware_lib/ops/planlint.py"
_PLANGOV = "scripts/agentware_lib/ops/plangovern.py"
_PLANMARK = "scripts/agentware_lib/ops/planmarkers.py"
_LINT_RECEIVERS = (
    [_PLANLINT + "::" + n for n in (
        "_plan_lint_read",      # R1
        "_lint_sections",       # R2
        "_lint_markers",        # R3, R4
        "_plan_tasks",          # the per-task body slice R5..R9 all read
        "_lint_task_bodies",    # R5, R6, R7
        "_lint_promise",        # R8
        "_lint_autonomy",       # R9
        "_lint_authorship",     # R10
        "_lint_targets")]       # R11
    + [_PLANGOV + "::" + n for n in (
        "_lint_workorder",      # R12
        "_lint_status",         # R13
        "_lint_premises",       # R14
        "_plan_own_slug",       # R15's self-edge test
        "_lint_graph")]         # R15, R16
    + [_PLANMARK + "::_lint_add"]   # the nested `add` closure, now shared
)

# Task 39 step 6 DELETED the monolith's own copies of the plan family, and those
# copies were what had been paying the length floor for three rows. The lib's
# versions are shorter because the SAME per-function caps that forced the lint
# split (120 lines / 12 branches, over a shrink-only debt fixture) also forced
# `_plan_skeleton`, `cmd_plan_new` and `cmd_plan_lint` apart. Every receiver is
# named below, so dropping any one of them reddens the gate on a missing absorber
# rather than passing as a silent deletion.
_PLANNEW = "scripts/agentware_lib/ops/planauthor/plannew.py"
_SKELETON_RECEIVERS = [_PLANNEW + "::" + n for n in (
    "_skeleton_provenance_block",   # the four `> Origin: auto` workorder headers
    "_skeleton_identity_block",     # `> Team:` + `> Author:` (the org union)
    "_skeleton_target_block",       # `## Target packages` body
    "_skeleton_warning_block",      # the R-PKG-03 self-extension warning
    "_skeleton_context_body",       # `## Context`, incl. the quoted UNTRUSTED seed
    "_skeleton_tasks_block",        # the [e2e]+[kb] pair + optional seeded task
    "_skeleton_premises_block")]    # the R14 / R-GND-01 `## Premises` stub
_PLANNEW_RECEIVERS = [_PLANNEW + "::" + n for n in (
    "_plannew_auto_fields",         # --auto slug + provenance resolution
    "_plannew_auto_guards",         # g1 target/text refusals + the g3 lifecycle cap
    "_plannew_context_seed")]       # the g1 `--context` refusal + g2 neutralization
_LINT_CMD_RECEIVERS = [_PLANLINT + "::" + n for n in (
    "_plan_lint_read",              # path read + R1 parse
    "_emit_plan_lint")]             # the text/json rendering half

_UPDATECMD = "scripts/agentware_lib/features/updatecmd.py"
_UPDATE_RECEIVERS = [_UPDATECMD + "::_update_self_heal"]

_STAGING = "scripts/agentware_lib/ops/staging.py"
_KBCOMMIT_RECEIVERS = [_STAGING + "::" + n for n in (
    "_kb_commit_subject",   # the whole conventional-commit subject derivation
    "_git_add_batched")]    # the staging call, now argv-batched

ADJUDICATIONS = {
    "cmd_kb_git_commit": {
        "adjudication": "decomposed",
        "host": _STAGING,
        "floor_chars": 2643,
        "absorbs": list(_KBCOMMIT_RECEIVERS),
        "rationale": "Task 57 widened the de-commit sweep from the nine derived "
                     "artifacts to the whole declared ignore contract, which "
                     "pushed this handler further past caps it was already over "
                     "(the ceiling fixture named it at 20 branches). Its densest "
                     "branch cluster was subject derivation -- a pure function of "
                     "(args, changed paths) with no git state in it -- so that "
                     "became `_kb_commit_subject`, and the three-statement "
                     "`git add` tail became `_git_add_batched` when the path list "
                     "stopped being bounded. Every statement that left the "
                     "handler is accounted for in one of the two receivers; the "
                     "extraction was verified statement-by-statement against the "
                     "pre-move body, not by eye. The move CREATED a contract no "
                     "gate can see -- the refusal is now signalled by returning "
                     "None -- and that CLI-unreachable defensive branch is locked "
                     "behaviourally by `KbCommitSubjectContractTest` in "
                     "`tests/unit/ops/test_kb_git.py`, whose three cases were each proven "
                     "to redden (drop the handler's None arm; make the helper "
                     "fall back instead of refusing; give the handler its own "
                     "diverging copy of one branch). The four derivation branches "
                     "keep their pre-existing end-to-end coverage, untouched.",
    },
    "cmd_update": {
        "adjudication": "decomposed",
        "host": _UPDATECMD,
        "floor_chars": 6229,
        "absorbs": list(_UPDATE_RECEIVERS),
        "rationale": "Task 41 ported the signed-tag update channel. The handler "
                     "arrived at 166 lines against a DECLARED debt of 163 (the "
                     "ceiling fixture had already named this module by path), so "
                     "it was decomposed rather than having its allowance widened: "
                     "`--self-heal` became `_update_self_heal`, which is the one "
                     "mode that MUTATES and the one whose exit code carries a "
                     "non-obvious rule (a migrate-error must exit non-zero even "
                     "though it sets no needs_manual_recovery flag). The other "
                     "four flags are read-only reports and stay inline.",
    },
    "_plan_skeleton": {
        "adjudication": "decomposed",
        "host": _PLANNEW,
        "floor_chars": 2707,
        "absorbs": list(_SKELETON_RECEIVERS),
        "rationale": "Task 39 landed the UNION skeleton (this tree's workorder "
                     "provenance, `> Status: draft`, the target/context/task "
                     "seeds and the `## Premises` stub, PLUS the donor's org "
                     "`> Team:` stamp). At 142 lines / 14 branches it arrived over "
                     "the 120/12 per-function cap in a module carrying no debt "
                     "rows, so each `## Section` body became a named "
                     "`_skeleton_*` builder and the parent kept the template "
                     "assembly. Output proved byte-identical to the monolith on "
                     "both the flat and the fully-featured paths, with org mode "
                     "differing by exactly the one `> Team:` line.",
    },
    "cmd_plan_new": {
        "adjudication": "decomposed",
        "host": _PLANNEW,
        "floor_chars": 1917,
        "absorbs": list(_PLANNEW_RECEIVERS),
        "rationale": "Same cap, same module: 122 lines / 20 branches on arrival. "
                     "The `--auto` machine-plan path (slug check, provenance "
                     "sanitize, the g1 target and text refusals, the g2 target "
                     "neutralization and the g3 draft/ready lifecycle cap) and the "
                     "`--context` g1 refusal moved into named `_plannew_*` "
                     "helpers, leaving the handler as orchestration. Each refusal "
                     "was re-proved firing after the split.",
    },
    "cmd_plan_lint": {
        "adjudication": "decomposed",
        "host": _PLANLINT,
        "floor_chars": 792,
        "absorbs": list(_LINT_CMD_RECEIVERS),
        "rationale": "The `plan lint` CLI adapter split alongside its rule set at "
                     "checkpoint 3: the path read + R1 parse became "
                     "`_plan_lint_read` and the text/json rendering became "
                     "`_emit_plan_lint`, leaving the handler as argument wiring. "
                     "The floor only bit at step 6, when the monolith's copy — "
                     "which had been paying it — was deleted.",
    },
    "lint_plan": {
        "adjudication": "decomposed",
        "host": _PLANLINT,
        "floor_chars": 2201,
        "absorbs": list(_LINT_RECEIVERS),
        "rationale": "Task 39 ported plan lint R1-R16. The monolith's body is "
                     "270 lines / 44 branches against a 120/12 per-function cap "
                     "and a shrink-only debt fixture, so it is split one "
                     "receiver per rule group (R12-R16 into ops.plangovern, the "
                     "finding record into ops.planmarkers) with lint_plan "
                     "reduced to the rule ORDER. Behaviour unchanged - proved "
                     "by a differential probe running both implementations over "
                     "1017 (plan x rel_path x strict x schema) combinations "
                     "with zero differences, including the ordering.",
    },
    "build_parser": {
        "adjudication": "decomposed",
        "host": "scripts/agentware_legacy.py",
        "floor_chars": 65728,
        "absorbs": list(_PORTED_REGISTRARS),
        "rationale": "Every port moves a verb's argparse block from the "
                     "monolith's build_parser into its own register_<verb> in "
                     "the lib, which assemble_parser walks. Task 37 moved "
                     "`migrate` (489 chars); task 38 moved `relate` (864). "
                     "Surface unchanged — the promotion gate proves that against "
                     "the union fixture node by node.",
    },
    "_dream_health_gate_checks": {
        "adjudication": "decomposed",
        "host": _DREAM_HG,
        "floor_chars": 699,
        "absorbs": [_DREAM_HG + "::" + n for n in _DREAM_HG_CHECKS],
        "rationale": "Task 21 split the dream step module into one file per "
                     "step; the nine health checks became named helpers in the "
                     "same file. Logic relocated, not removed.",
    },
    "build_index_from_frontmatter": {
        "adjudication": "decomposed",
        "host": _INDEX_REBUILD,
        "floor_chars": 1302,
        "absorbs": [_INDEX_REBUILD + "::" + n for n in _INDEX_ROW_CHAIN],
        "rationale": "Task 21 split the index module into read/write/rebuild and "
                     "task 22 added duplicate-id dedup; the per-row build moved "
                     "to _frontmatter_row and its two helpers.",
    },
    "cmd_config": {
        "adjudication": "decomposed",
        "host": _CFG + "cfgmain.py",
        "floor_chars": 720,
        "absorbs": _CFG_HANDLERS,
        "rationale": "Task 21 split the ~590-line guard-clause chain into "
                     "per-setting handlers behind CONFIG_ROUTES; cmd_config is "
                     "now the walk over that table.",
    },

    # ----------------------------------------------------------------------
    # SUPERSEDED -- the plan decided against the winner's body
    # ----------------------------------------------------------------------

    # Task 15 deleted the KB-repointing module outright. What that removed from
    # cmd_dream is exactly the `_dream_maybe_repoint_kb()` call and the
    # `kb_repoint` field it stamped onto the cycle record -- a dream step that
    # mutated where the knowledge base points, which is the behaviour task 15
    # exists to end. The donor body is longer BECAUSE it still repoints.
    "cmd_dream": {
        "adjudication": "superseded",
        "floor_chars": 3603,
        "rationale": "Task 15 deleted the KB-repointing module; the donor's "
                     "extra 240 chars are the _dream_maybe_repoint_kb call and "
                     "its kb_repoint cycle field, deliberately removed.",
    },
    # Task 18 rewrote whereami as a marker-driven package resolver. The donor
    # body is longer because it inlines a vendor-specific walk (a `Config` file
    # whose parent directory must be named `src`); the merged body delegates to
    # `core.projectcontext.whereami_payload`, which resolves in any repository
    # layout. Longer, here, means less portable.
    "cmd_whereami": {
        "adjudication": "superseded",
        "floor_chars": 865,
        "rationale": "Task 18 replaced the donor's vendor-specific Config/src "
                     "walk with the marker-driven resolver in "
                     "core.projectcontext; the handler now only picks a start "
                     "directory and renders.",
    },
    # Zero code difference: the ONLY divergence is one docstring sentence, where
    # the donor annotates its `rel_prefix` argument with a citation to a private
    # internal plan artifact -- the class of reference task 20 strips from this
    # repository. Kept as a row rather than waved through, because it is the
    # clearest evidence that the docstring-inclusive normalisation is doing its
    # job. The offending token is deliberately NOT reproduced here: quoting a
    # leaked name to document its removal re-leaks it, and the artifact-reference
    # scanner (tests/gates/test_plan_artifact_refs.py) correctly flagged this comment
    # for doing exactly that.
    "git_entry_ids_at_ref": {
        "adjudication": "superseded",
        "floor_chars": 1648,
        "rationale": "Task 20 removed the internal plan-artifact reference from "
                     "the docstring. Prose only -- the code is byte-identical.",
    },

    # ----------------------------------------------------------------------
    # DEFERRED -- a real donor capability this tree has not adopted yet
    # ----------------------------------------------------------------------
    #
    # EMPTY at task 69, and that is a discharge rather than a deletion. The one
    # row here was `get_cli`: the donor's dashboard loaded the CLI with
    # spec_from_loader + module_from_spec + exec_module and put `scripts/` on
    # sys.path first, while this tree still called the DEPRECATED
    # SourceFileLoader.load_module(). It was deferred to task 69 by NAME because
    # that task owns the dashboard, and task 69 ADOPTED the donor loader when it
    # decomposed the file into `scripts/agentware_dashboard/` -- so the row goes
    # back to the default `longer-body`, floored at the donor's own 648 chars
    # instead of the 473-char holding floor. A deferral is discharged by landing
    # the capability, never by dropping the row: the floor RISES to the winner's
    # length, which is exactly what a deferred row was protecting.
    #
    # `DEFERRED_NAMES` is derived from this table, so the suite's pinned
    # deferred set empties with it and `test_head_is_a_SUPERSET...` keeps every
    # other floor unchanged.
}


# --------------------------------------------------------------------------
# TASK 54 -- the SHARED-VERB promotion wave
# --------------------------------------------------------------------------
# Each of these verbs existed on BOTH sides. Promoting one deletes the
# monolith's handler, so the merged tree stops seeing the longer body and the
# floor recorded against it reddens. Three of the four are DECOMPOSED: the lib's
# handler is shorter because content moved into named helpers in the same
# module, and naming every receiver is what separates that from a deleted
# branch. The fourth is SUPERSEDED by a decision the plan already recorded.
#
# `audit` is the cautionary one. Its row is here because the gate caught a
# genuinely DROPPED check: the lib's checks list was byte-identical to the
# monolith's except `_audit_pkg_version_check(kdir)` was absent, so `agentware
# audit` silently stopped reporting the package-version signal when the verb was
# ported. The check was restored (it already lived in ops.checkuphealth, merely
# uncalled) BEFORE this floor was pinned -- a floor pinned over a real loss
# certifies the loss.
_AUDIT = "scripts/agentware_lib/ops/audit.py"
_METRICSCMD = "scripts/agentware_lib/ops/metricscmd.py"
_RECALLCMD = "scripts/agentware_lib/retrieval/recallcmd.py"

ADJUDICATIONS.update({
    "cmd_audit": {
        "adjudication": "decomposed",
        "host": _AUDIT,
        "floor_chars": 2176,
        "absorbs": [_AUDIT + "::_audit_stale_section",
                    _AUDIT + "::_print_stale_section"],
        "rationale": "The staleness half of the audit -- deciding whether to run "
                     "the stale scan and how to render it -- became a computing "
                     "half (`_audit_stale_section`) and a rendering half "
                     "(`_print_stale_section`), leaving the parent as the checks "
                     "list plus dispatch. The checks list is a flat literal, so a "
                     "dropped line there is an absent check and not an error; "
                     "that is exactly what happened to the package-version check "
                     "and why it is restored in the same commit as this row.",
    },
    "cmd_metrics": {
        "adjudication": "decomposed",
        "host": _METRICSCMD,
        "floor_chars": 2262,
        "absorbs": [_METRICSCMD + "::_report_retrieval"],
        "rationale": "`metrics --retrieval` is a distinct report over the "
                     "retrieval metrics rather than the session aggregate, and it "
                     "short-circuits before any session parsing. It landed in the "
                     "surface pass as a flag the lib ACCEPTED and then ignored, "
                     "which is worse than a missing flag -- the caller gets exit "
                     "0 and the wrong report. Extracting it is what let the "
                     "parent stay under its branch cap while the flag does "
                     "something.",
    },
    "cmd_recall": {
        "adjudication": "decomposed",
        "host": _RECALLCMD,
        "floor_chars": 4478,
        "absorbs": [_RECALLCMD + "::_print_recall_text",
                    _RECALLCMD + "::_print_recall_overflow"],
        "rationale": "The monolith's text renderer -- live-anchored absolute "
                     "path, age line, trust line, and the one-per-invocation "
                     "R-FAIL-08 staleness footer -- was missing from the lib "
                     "entirely and had to be restored. Restoring it inline put "
                     "the parent at 32 branches / 145 lines against a 12/120 cap, "
                     "so the renderer and its overflow half became named "
                     "receivers and the parent went to 19/114.",
    },
    "build_bm25_cache": {
        "adjudication": "superseded",
        "floor_chars": 1255,
        "rationale": "Task 24 adjudicated the cache freshness key: the "
                     "whole-corpus content FINGERPRINT is the authority and the "
                     "monotonic `generation` counter is retired, because a "
                     "counter only orders ONE writer's writes and an index "
                     "arriving from a pull, a restored backup or a teammate's "
                     "shard carries an unrelated sequence. The replacement body "
                     "stamps `fingerprint` + `stat_signature` instead of "
                     "`generation` and is 4 chars shorter for it. The row now "
                     "ratchets against the replacement.",
    },
})


# --------------------------------------------------------------------------
# DONOR-LONGER rows carried by the tasks 54/55 wave
# --------------------------------------------------------------------------
# These three compare against the DONOR body rather than the monolith's, and all
# three predate the verb that surfaced them -- they are pinned here because the
# body gate is the thing that finally named them, and an unadjudicated floor is
# indistinguishable from a regression nobody looked at.
_MCP = "scripts/agentware_lib/features/mcp.py"
_WORKCORPUS = "scripts/agentware_lib/retrieval/workcorpus.py"

ADJUDICATIONS.update({
    "_mcp_tool_recall": {
        "adjudication": "superseded",
        "floor_chars": 973,
        "rationale": "One character, and it is a decision: the donor defaults the "
                     "MCP recall `token_budget` to 10000, this tree to 1500, "
                     "matching the CLI's own recall default (the surface-union "
                     "adjudication picked local). An MCP tool that injects nearly "
                     "seven times the CLI's budget is the same context bloat "
                     "recall exists to prevent, and the two front doors "
                     "disagreeing on it is worse than either value.",
    },
    "_mcp_tool_definitions": {
        "adjudication": "superseded",
        "floor_chars": 1627,
        "rationale": "The same 10000 -> 1500 decision, seen from the SCHEMA side: "
                     "this function declares the `default` the MCP client shows "
                     "its user. It is pinned as its own row because the schema "
                     "and the handler are two places one default has to agree "
                     "with itself, and a length proxy would let either drift.",
    },
    "build_work_corpus": {
        "adjudication": "decomposed",
        "host": _WORKCORPUS,
        "floor_chars": 1195,
        "absorbs": [_WORKCORPUS + "::" + n for n in (
            "_build_work_corpus_uncached",   # the whole uncached build
            "_work_corpus_stat_digest",      # the O(stat) freshness digest
            "_load_work_corpus_cache",       # read + version/digest validation
            "_write_work_corpus_cache",      # atomic write
            "_read_work_text",               # one work document
            "_work_status_multiplier")],     # the plan-status ranking weights
        "rationale": "The donor's version is one inlined body that calls no "
                     "helper at all; this tree's is the same capability split "
                     "into a named family in its own module, so the parent is "
                     "the cache-or-build decision and nothing else. The split is "
                     "what makes the stat short-circuit testable in isolation -- "
                     "a warm work-scope recall must read ZERO work bodies, which "
                     "is an assertion you cannot write against an inlined body.",
    },
})


# --------------------------------------------------------------------------
# TASK 54/55 -- the WORKLOG SCANNER union
# --------------------------------------------------------------------------
# Both trees scanned worklog markers, and one of them had already been refactored
# so that a single primitive owns the rule and the two callers delegate to it.
# This tree carried the OTHER shape: `scan_worklog` inlined the loop and
# `_failure_marker_tally` inlined it a SECOND time. Two copies of one rule is not
# a style question -- they had already drifted, and only in the direction that is
# invisible: the tally recognized neither `> WAIVED:` nor a cross-category
# promotion reference, so a waived marker and a `> LEARNED:` promoted as a skill
# counted as unpromoted in the tally while the completion gate counted them
# closed. Both callers now delegate, so both shrank, and both name the receiver.
_WORKLOGMOD = "scripts/agentware_lib/knowledge/worklog.py"
_MARKERS = _WORKLOGMOD + "::scan_worklog_markers"

ADJUDICATIONS.update({
    "scan_worklog": {
        "adjudication": "decomposed",
        "host": _WORKLOGMOD,
        "floor_chars": 476,
        "absorbs": [_MARKERS],
        "rationale": "Reduced to the back-compat wrapper it is in the winning "
                     "tree: `scan_worklog_markers` owns the marker vocabulary "
                     "(bare/blockquote/bullet forms, the four promotion-reference "
                     "spellings, cross-category resolution and WAIVED) and "
                     "returns the richer record, while this keeps the "
                     "(unpromoted, total) signature and the exit semantics every "
                     "existing caller depends on.",
    },
    "_failure_marker_tally": {
        "adjudication": "decomposed",
        "host": "scripts/agentware_lib/ops/failureladder.py",
        "floor_chars": 963,
        "absorbs": [_MARKERS],
        "rationale": "It re-implemented the scan loop so the per-kind tally could "
                     "count what the gate counts, which is exactly the coupling "
                     "that guarantees drift -- and it HAD drifted, missing both "
                     "WAIVED and cross-category refs. Delegating to the same "
                     "primitive is what makes 'the tally agrees with the gate' a "
                     "property of the code rather than a comment.",
    },
})


# --------------------------------------------------------------------------
# TASK 54 -- the SKILL family
# --------------------------------------------------------------------------
# Both handlers shrank because their index read-modify-write moved into named
# `skillentry` helpers that keep the SAME lock discipline (the lost-update window
# opens at the LOAD, so the whole load->mutate->save runs under index._index_lock
# in the receiver). `cmd_skill_add` additionally hands its security boundary to a
# named vetter. Naming every receiver is what separates that from a dropped lock
# or a dropped check -- and this port restored a genuinely dropped check, so the
# distinction is not hypothetical here.
_SKILLS = "scripts/agentware_lib/features/skills.py"
_SKILLENTRY = "scripts/agentware_lib/knowledge/skillentry.py"

ADJUDICATIONS.update({
    "cmd_skill_add": {
        "adjudication": "decomposed",
        "host": _SKILLS,
        "floor_chars": 3533,
        "absorbs": [_SKILLS + "::_vet_or_refuse",
                    _SKILLENTRY + "::skill_register_entry"],
        "rationale": "The vetter call was NOT a decomposition when this port "
                     "started -- it was a downgrade: the modular copy called "
                     "`vet_skill_dir` (the danger-marker scan) directly instead of "
                     "the BLOCK-dominant `_skill_verdict`, so a skill tree "
                     "containing a SYMLINK installed cleanly and the install-name "
                     "validation and REVIEW tier were bypassed entirely. The tiered "
                     "verdict is restored and now lives in `_vet_or_refuse`, one "
                     "named boundary every cache-write path can share. The index "
                     "registration is a true decomposition into "
                     "`skill_register_entry`, which keeps the lock.",
    },
    "cmd_skill_remove": {
        "adjudication": "decomposed",
        "host": _SKILLS,
        "floor_chars": 1012,
        "absorbs": [_SKILLENTRY + "::skill_unregister_entry"],
        "rationale": "The entry/tag-map removal moved into `skill_unregister_entry` "
                     "under the same `index._index_lock` discipline as the register "
                     "direction. Verified rather than assumed: a shrink here would "
                     "otherwise be indistinguishable from dropping the lock, which "
                     "is exactly the silent lost-update this row exists to police.",
    },
})

ADJUDICATIONS.update({
    "cmd_skill_propose": {
        "adjudication": "decomposed",
        "host": "scripts/agentware_lib/features/proposals/proposeauthor.py",
        "floor_chars": 2464,
        "absorbs": [
            "scripts/agentware_lib/features/proposals/proposeauthor.py::_propose_author"],
        "rationale": "The two pre-merge trees both shipped `skill propose` and "
                     "meant DIFFERENT things by it: proposal AUTHORING "
                     "(create-or-increment a skills-proposed/ ledger) and "
                     "argument-less skill-CANDIDATE detection. The union fixture "
                     "folds both lifecycles' FLAGS onto one node, but it is DERIVED "
                     "and its adjudication table fires only where BOTH trees "
                     "declare an action -- `--slug` is donor-only, so its `required` "
                     "cannot be relaxed and the merged `propose` IS the authoring "
                     "lifecycle. Detection keeps its own spelling, `skill "
                     "candidates`, against the same handler: capability preserved, "
                     "invocation moved. This parent is the validation + the ledger "
                     "write it delegates to `_propose_author`.",
    },
})


# --------------------------------------------------------------------------
# TASK 56 -- the rows the MONOLITH was paying for
# --------------------------------------------------------------------------
# Every row below was green until the shim was deleted, because the merged tree
# still contained the monolith's copy of the function and that copy satisfied the
# floor on its own. The lib had ALREADY decomposed each one; nothing was lost and
# nothing needed adjudicating while both bodies existed. Delete the monolith and
# the gate measures the lib alone, so each split finally has to name its
# receivers -- which is the gate working exactly as intended, one task later than
# the split itself.
#
# Each floor is the LIB's measured body, taken from `--check` against a tree with
# the monolith removed, so these pin what the modular core actually has rather
# than a number I chose.
_IDXVALIDATE = "scripts/agentware_lib/knowledge/index/indexvalidate.py"
_IDXREBUILD = "scripts/agentware_lib/knowledge/index/indexrebuild.py"
_STEPDETECT = "scripts/agentware_lib/features/dreamsteps/stepdetectreport.py"
_STEPAUDIT = "scripts/agentware_lib/features/dreamsteps/stepaudit.py"
_STEPKBPULL = "scripts/agentware_lib/features/dreamsteps/stepkbpull.py"
_FRESHNESS = "scripts/agentware_lib/ops/freshness.py"
_ALERTS = "scripts/agentware_lib/ops/alerts.py"
_DERIVE = "scripts/agentware_lib/ops/derive.py"
_LOOPAN = "scripts/agentware_lib/ops/loopanalytics.py"
_GOLDFIX = "scripts/agentware_lib/retrieval/goldfixture.py"
_ACR = "scripts/agentware_lib/retrieval/acr.py"
_ABGATE = "scripts/agentware_lib/retrieval/regressiongate/abgate.py"
_BENCHSWE = "scripts/agentware_lib/retrieval/bench/benchswe.py"
_SKILLS = "scripts/agentware_lib/features/skills.py"
_KBLAYOUT = "scripts/agentware_lib/core/kblayout.py"

#: The COMPLETE registration surface -- every `register_*` / `_register_*` in the
#: lib, not just the 44 verb entry points. The monolith inlined ONE `add_parser`
#: sequence; the lib walks the frozen registration tuple, and each verb registrar
#: in turn delegates to per-group helpers (`register_config` alone fans out to
#: nine flag groups). Counting only the entry points left a 27% shortfall against
#: the monolith's single body, which is the fan-out, not a missing surface.
_ALL_REGISTRARS = (
        "scripts/agentware_lib/cli.py::register_whereami",
        "scripts/agentware_lib/features/configcmd/cfgmain.py::_register_pointer_flags",
        "scripts/agentware_lib/features/configcmd/cfgmain.py::_register_org_flags",
        "scripts/agentware_lib/features/configcmd/cfgmain.py::_register_kbgit_flags",
        "scripts/agentware_lib/features/configcmd/cfgmain.py::_register_retrieval_flags",
        "scripts/agentware_lib/features/configcmd/cfgmain.py::_register_kb_mode_flags",
        "scripts/agentware_lib/features/configcmd/cfgmain.py::_register_cli_flags",
        "scripts/agentware_lib/features/configcmd/cfgmain.py::_register_provenance_flags",
        "scripts/agentware_lib/features/configcmd/cfgmain.py::_register_dream_flags",
        "scripts/agentware_lib/features/configcmd/cfgmain.py::register_config",
        "scripts/agentware_lib/features/configcmd/cfgports.py::register_ported_setting_flags",
        "scripts/agentware_lib/features/curationcmd.py::register_learn",
        "scripts/agentware_lib/features/curationcmd.py::register_decide",
        "scripts/agentware_lib/features/dream.py::register_dream",
        "scripts/agentware_lib/features/fanoutmq/mqteardown.py::register_fanout",
        "scripts/agentware_lib/features/graphdag/graphcmd.py::register_graph",
        "scripts/agentware_lib/features/graphdag/graphcmd.py::_register_graph_execution",
        "scripts/agentware_lib/features/indexcmd.py::register_index",
        "scripts/agentware_lib/features/mcp.py::register_mcp",
        "scripts/agentware_lib/features/migratecmd.py::register_migrate",
        "scripts/agentware_lib/features/proposals/proposalpromote.py::register_skill",
        "scripts/agentware_lib/features/proposals/proposalpromote.py::_register_skill_cache",
        "scripts/agentware_lib/features/proposals/proposalpromote.py::_register_skill_lifecycle",
        "scripts/agentware_lib/features/setupcmd.py::register_init",
        "scripts/agentware_lib/features/setupcmd.py::register_profile",
        "scripts/agentware_lib/features/trustcmd.py::register_trust",
        "scripts/agentware_lib/features/updatecmd.py::register_update",
        "scripts/agentware_lib/knowledge/attach.py::register_attach",
        "scripts/agentware_lib/knowledge/curation/curationfeatures.py::register_features",
        "scripts/agentware_lib/knowledge/curation/curationingest.py::register_ingest",
        "scripts/agentware_lib/knowledge/graph.py::register_query",
        "scripts/agentware_lib/knowledge/relatecmd.py::register_relate",
        "scripts/agentware_lib/knowledge/worklog.py::register_worklog",
        "scripts/agentware_lib/ops/audit.py::register_audit",
        "scripts/agentware_lib/ops/checkupcmd.py::register_checkup",
        "scripts/agentware_lib/ops/fleet/fleetreport.py::register_fleet",
        "scripts/agentware_lib/ops/fleet/fleetshard.py::register_fleet_emit",
        "scripts/agentware_lib/ops/fleet/fleettemplates.py::register_templates",
        "scripts/agentware_lib/ops/kbsync.py::register_kb_git",
        "scripts/agentware_lib/ops/metricscmd.py::register_metrics",
        "scripts/agentware_lib/ops/metricscmd.py::register_dashboard",
        "scripts/agentware_lib/ops/orglint.py::register_org",
        "scripts/agentware_lib/ops/planauthor/planedit.py::_register_plan_lint",
        "scripts/agentware_lib/ops/planauthor/planedit.py::_register_plan_premises",
        "scripts/agentware_lib/ops/planauthor/planedit.py::_register_plan_new",
        "scripts/agentware_lib/ops/planauthor/planedit.py::_register_plan_edit",
        "scripts/agentware_lib/ops/planauthor/planedit.py::_register_plan_state",
        "scripts/agentware_lib/ops/planauthor/planedit.py::_register_plan_tdirs",
        "scripts/agentware_lib/ops/planauthor/planedit.py::register_plan",
        "scripts/agentware_lib/ops/planauthor/workstamp.py::register_work",
        "scripts/agentware_lib/ops/prefercmd.py::register_prefer",
        "scripts/agentware_lib/ops/prefercmd.py::_register_prefer_capture",
        "scripts/agentware_lib/ops/prefercmd.py::_register_prefer_manage",
        "scripts/agentware_lib/ops/prefercmd.py::_register_prefer_producers",
        "scripts/agentware_lib/ops/releasegate.py::register_gate",
        "scripts/agentware_lib/ops/releasepreflight.py::register_release",
        "scripts/agentware_lib/ops/reviewaudit.py::register_review",
        "scripts/agentware_lib/ops/reviewremediate.py::register_remediate",
        "scripts/agentware_lib/ops/reviewreport.py::register_review_report",
        "scripts/agentware_lib/ops/steering.py::register_steering",
        "scripts/agentware_lib/ops/team.py::register_team",
        "scripts/agentware_lib/ops/tickcmd.py::register_builtin_tick_jobs",
        "scripts/agentware_lib/ops/tickcmd.py::register_tick",
        "scripts/agentware_lib/retrieval/bench/benchcmd.py::register_bench",
        "scripts/agentware_lib/retrieval/evalcmd.py::register_eval",
        "scripts/agentware_lib/retrieval/recallcmd.py::register_recall",
)

ADJUDICATIONS.update({
    "build_parser": {
        "adjudication": "decomposed",
        "host": "scripts/agentware_lib/cli.py",
        "floor_chars": 399,
        "absorbs": list(_ALL_REGISTRARS),
        "rationale": "The monolith built the whole CLI in ONE `build_parser`; the "
                     "lib's `assemble_parser` walks the frozen registration tuple "
                     "and each verb owns its own registrar. The maintenance "
                     "protocol above said to append each verb's registrar as it "
                     "ported, and while the monolith's 65k-char body was still "
                     "present it paid the floor by itself, so a missing row cost "
                     "nothing and the list stopped at task 38. This is that list "
                     "completed -- all 44, generated from _PARSER_REGISTRATION so "
                     "it cannot drift from what actually registers.",
    },
    "validate_index": {
        "adjudication": "decomposed",
        "host": _IDXVALIDATE,
        "floor_chars": 876,
        "absorbs": [_IDXVALIDATE + "::" + n for n in (
            "_entry_required_errors", "_entry_identity_errors",
            "_entry_optional_errors", "_entry_shape_errors",
            "_validate_entry_tag_errors", "_validate_tag_map_errors")],
        "rationale": "One validator became six per-concern error collectors "
                     "(required fields, identity, optional fields, shape, entry "
                     "tags, tag map) with the parent as the order they run in. "
                     "Naming all six is what separates that from six dropped "
                     "checks -- a validator that silently stops checking one "
                     "field reports PASSED, which is the failure this table "
                     "exists to make impossible.",
    },
    "tag_consistency_errors": {
        "adjudication": "decomposed",
        "host": _IDXVALIDATE,
        "floor_chars": 260,
        "absorbs": [_IDXVALIDATE + "::_tag_map_forward_errors",
                    _IDXVALIDATE + "::_tag_map_backref_errors"],
        "rationale": "The forward direction (every entry tag appears in the map) "
                     "and the backref direction (every map row points at a live "
                     "entry) are separate walks over separate data; the parent is "
                     "their union. Both are named because a one-directional tag "
                     "check passes on exactly the corruption the other catches.",
    },
    "rebuild_kb": {
        "adjudication": "decomposed",
        "host": _IDXREBUILD,
        "floor_chars": 1300,
        "absorbs": [_IDXREBUILD + "::_rebuild_kb_locked"],
        "rationale": "The parent acquires the index lock and the receiver does the "
                     "whole rebuild under it. Splitting on the lock boundary is "
                     "what makes the critical section reviewable in one screen.",
    },
    "_dream_step_detect_report": {
        "adjudication": "decomposed",
        "host": _STEPDETECT,
        "floor_chars": 1907,
        "absorbs": [_STEPDETECT + "::" + n for n in (
            "_iter_feats", "_scan_feature", "_detect_feature_markers",
            "_detect_plan_only", "_detect_plan_drift")],
        "rationale": "Three independent per-feature scans (markers, plan-only, "
                     "drift) behind one `_scan_feature` record, leaving the parent "
                     "as the walk plus the tally. Decomposed rather than declared "
                     "as debt because `dreamsteps` is a task-21 package that "
                     "carries NO ceiling debt by construction.",
    },
    "_dream_render_detect_report": {
        "adjudication": "decomposed",
        "host": _STEPDETECT,
        "floor_chars": 1425,
        "absorbs": [_STEPDETECT + "::" + n for n in (
            "_dream_render_stale_section", "_dream_render_conflicts_section",
            "_dream_render_markers_section", "_dream_render_plan_only_section",
            "_dream_render_drift_section")],
        "rationale": "One builder per report SECTION, with the parent holding the "
                     "section ORDER as a single five-line list. The order IS the "
                     "report's contract, and keeping it visible is what makes an "
                     "added or dropped section a one-line reviewable diff.",
    },
    "_dream_step_audit": {
        "adjudication": "decomposed",
        "host": _STEPAUDIT,
        "floor_chars": 1102,
        "absorbs": [_STEPAUDIT + "::_dream_audit_read_capture",
                    _STEPAUDIT + "::_dream_audit_unittest_counts"],
        "rationale": "Parsing the captured audit JSON and counting the unittest "
                     "results are both pure readers over the capture; the step "
                     "keeps the dispatch and the change-gate.",
    },
    "_dream_step_kb_pull": {
        "adjudication": "decomposed",
        "host": _STEPKBPULL,
        "floor_chars": 1366,
        "absorbs": [_STEPKBPULL + "::" + n for n in (
            "_dream_pull_resolve_derived", "_dream_pull_skip_reason",
            "_dream_pull_ok", "_dream_pull_offline_reason",
            "_dream_pull_head_sha")],
        "rationale": "Each precondition that can SKIP the pull became its own "
                     "named reason function, so the step reads as the sequence of "
                     "reasons it can decline. The derived-file reconcile is the "
                     "one that mutates and is named separately for that reason.",
    },
    "_print_stale_report": {
        "adjudication": "decomposed",
        "host": _FRESHNESS,
        "floor_chars": 797,
        "absorbs": [_FRESHNESS + "::_superseded_report_rows"],
        "rationale": "The supersede section builds its rows from the relation "
                     "graph rather than from the stale scan, so it is a separate "
                     "derivation the printer consumes. It was itself restored "
                     "during the `audit` port after going missing.",
    },
    "derive_alerts": {
        "adjudication": "decomposed",
        "host": _ALERTS,
        "floor_chars": 1670,
        "absorbs": [_ALERTS + "::" + n for n in (
            "_alerts_regression", "_alerts_unpromoted", "_alerts_kb",
            "_alerts_scaling", "_alerts_loop", "_alerts_cost")],
        "rationale": "One detector per alert FAMILY, with the parent as the list "
                     "of families it runs. Each is named so a dropped family is a "
                     "visible deletion rather than an alert that stops firing.",
    },
    "derive_iteration_costs": {
        "adjudication": "decomposed",
        "host": _DERIVE,
        "floor_chars": 1758,
        "absorbs": [_DERIVE + "::_plan_marker_status_counts"],
        "rationale": "The plan-marker tally is shared with the plan-size "
                     "derivation, so it is a named helper rather than a private "
                     "loop -- one definition of what an open vs done marker is.",
    },
    "list_loop_features": {
        "adjudication": "decomposed",
        "host": _LOOPAN,
        "floor_chars": 515,
        "absorbs": [_LOOPAN + "::_loop_analytics_for_feature",
                    _LOOPAN + "::derive_loop_analytics"],
        "rationale": "The per-feature analytics moved out so the lister is a "
                     "directory walk and nothing else; the heavy per-feature "
                     "derivation is reused by the analytics command directly.",
    },
    "load_gold_fixture": {
        "adjudication": "decomposed",
        "host": _GOLDFIX,
        "floor_chars": 2317,
        "absorbs": [_GOLDFIX + "::gold_fixture_path",
                    _GOLDFIX + "::evaluate_gold_fixture"],
        "rationale": "Path resolution and evaluation split out from loading, so "
                     "the gate can evaluate a fixture it did not load from the "
                     "default path.",
    },
    "retrieve_bm25_acr": {
        "adjudication": "decomposed",
        "host": _ACR,
        "floor_chars": 1000,
        "absorbs": [_ACR + "::" + n for n in (
            "acr", "freshness", "source_weight", "answer_age_days",
            "date_age_days")],
        "rationale": "The ACR score and each of its inputs (freshness, source "
                     "weight, the two age readers) are separately named because "
                     "each is independently testable and the composite is not.",
    },
    "run_ab_gate": {
        "adjudication": "decomposed",
        "host": _ABGATE,
        "floor_chars": 1532,
        "absorbs": [_ABGATE + "::" + n for n in (
            "_run_ab_gate_ns", "_extract_head_scripts", "_extract_head_cli",
            "_ab_head_recall", "_cleanup_head_cli")],
        "rationale": "The HEAD-side extraction is the part that touches the "
                     "filesystem and must clean up after itself, so it is named "
                     "separately from the comparison it feeds.",
    },
    "swe_score_task": {
        "adjudication": "decomposed",
        "host": _BENCHSWE,
        "floor_chars": 1514,
        "absorbs": [_BENCHSWE + "::" + n for n in (
            "swe_task_passed", "swe_build_ledger_row", "swe_aggregate_seeds",
            "_dirs_identical")],
        "rationale": "Pass/fail, the ledger row, seed aggregation and the "
                     "tree-comparison are each named so a scoring change is "
                     "visible as an edit to the thing that decides it.",
    },
    "_copy_skill_tree": {
        "adjudication": "decomposed",
        "host": _SKILLS,
        "floor_chars": 685,
        "absorbs": [_SKILLS + "::_validate_skill_dir",
                    _SKILLS + "::_vet_or_refuse"],
        "rationale": "The copy no longer validates inline: the shape check and the "
                     "tiered skill-vetter verdict are both named boundaries the "
                     "caller runs first. `_vet_or_refuse` is the one restored "
                     "during the `skill` port, where calling the danger-scanner "
                     "directly had bypassed symlink refusal entirely.",
    },
    "_ensure_kb_gitignore": {
        "adjudication": "decomposed",
        "host": _KBLAYOUT,
        "floor_chars": 1425,
        "absorbs": [_KBLAYOUT + "::_gitignore_rule_line"],
        "rationale": "Rendering one normalized rule line is shared between the "
                     "scaffold and the append-if-missing path, so the two cannot "
                     "disagree about whether a rule already exists.",
    },
})

# Pinned by the suite. Growing this set requires editing BOTH this table and the
# test that asserts it, which is the point: a deferral must be argued for, never
# accumulated.
DEFERRED_NAMES = frozenset(
    name for name, row in ADJUDICATIONS.items()
    if row["adjudication"] == "deferred")
