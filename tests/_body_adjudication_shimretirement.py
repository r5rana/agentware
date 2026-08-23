"""ADJUDICATIONS — the rows the deleted MONOLITH used to pay for (task 56).

The sibling half of `tests/_body_adjudication.py`, split out at task 91 when the
table reached the ceiling its debt row declares. The seam is the one the section
header below already named, not a line count: every row HERE was adjudicated for
the SAME reason — the lib had already decomposed the function, and the shim's
surviving copy satisfied the floor on its own until task 96 deleted it — while
the rows that stay in the host file were each adjudicated for a reason of their
own (a plan module split, a port that could not be verbatim, a superseded body, a
deferred capability, a later task sharing one contract between two verbs).

Nothing here is forgiven and nothing changed in the move: same names, same
floors, same absorbers, same rationales. `_body_adjudication` merges this dict
into `ADJUDICATIONS`, which stays the ONE name every consumer imports.
"""

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
#: sequence; the lib walks the DISCOVERED verb rows, and each verb registrar
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

SHIM_RETIREMENT_ROWS = {
    "build_parser": {
        "adjudication": "decomposed",
        "host": "scripts/agentware_lib/cli.py",
        "floor_chars": 399,
        "absorbs": list(_ALL_REGISTRARS),
        "rationale": "The monolith built the whole CLI in ONE `build_parser`; the "
                     "lib's `assemble_parser` walks the verb rows agentware_lib."
                     "verbs DISCOVERS and each verb owns its own registrar. The "
                     "maintenance protocol above said to append each registrar as "
                     "it ported, and while the monolith's 65k-char body was still "
                     "present it paid the floor by itself, so a missing row cost "
                     "nothing and the list stopped at task 38. This is that list "
                     "completed -- all 44, read from the per-module `__verbs__` "
                     "declarations (task 70), so it cannot drift from what runs.",
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
        "floor_chars": 1990,
        "absorbs": [_GOLDFIX + "::gold_fixture_path",
                    _GOLDFIX + "::evaluate_gold_fixture",
                    _GOLDFIX + "::_load_fixture_corpus",
                    _GOLDFIX + "::_load_fixture_gold"],
        "rationale": "Path resolution and evaluation split out from loading, so "
                     "the gate can evaluate a fixture it did not load from the "
                     "default path. Task 99 wave 10 split the LOADER itself at "
                     "its own seam and lowered the host floor to the measured "
                     "remainder: the body was TWO sequential validation phases "
                     "sharing exactly one value -- the set of ids the corpus "
                     "declared, which the gold phase resolves `expected_ids` "
                     "against -- so the split is where that value is produced. "
                     "Both receivers are named individually because each raises "
                     "a DISTINCT half of the refusal taxonomy (eight corpus "
                     "messages, nine gold ones) and losing either would leave a "
                     "malformed fixture reaching the scorer as a valid corpus. "
                     "The split moves branches rather than removing any: every "
                     "message is byte-identical, and the phase ORDER is a "
                     "contract -- a document wrong in BOTH halves reports the "
                     "CORPUS problem, because the gold half cannot be checked "
                     "until the corpus has declared its ids. Proven a pure "
                     "refactor at two boundaries by "
                     "`evidence/task99/evalbench_boundary.py`, whose 24 "
                     "gold-fixture rows drive the whole taxonomy and diff "
                     "BYTE-IDENTICAL before and after.",
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
}
