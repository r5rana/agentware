"""The layered test-corpus contract, and the ledger that keeps the freeze honest.

Feature 260813-modular-core-adoption, task 58.

WHY A LEDGER AT ALL
-------------------
A unittest node id embeds the module path (`tests.test_recall.RecallTest.
test_x`), so MOVING a test file changes the id of every test in it. The task-9
preserved-surface freeze diffs frozen ids against what the destination
collects, and it is right to: an id that stops collecting is normally a test
that was deleted. Relayering the corpus makes 2,624 ids stop collecting at once
for a reason that is not a loss.

Two wrong ways to resolve that, both rejected:

  * RE-DERIVE the frozen sets against the moved tree. That regenerates a mixed
    fixture (`learn-re-derive-and-diff-never-regenerate-a-mixed-fixture`) and
    silently forgives a test genuinely deleted in the same commit -- the freeze
    would be buying green with its own baseline.
  * MATCH BY TAIL ONLY (`Class.test_method`, module ignored). Tails are not
    unique -- the donor convention repeats `ExportsTest.test_all_exports_
    present` across 14 modules -- so a tail-only freeze cannot tell "moved" from
    "a same-named test exists somewhere else".

So the move is RECORDED instead. `RELOCATIONS` maps each old module to the
module(s) that now own its tests, and `relocate()` resolves a frozen id through
it. The gate keeps all of its teeth: a frozen id whose tail is collected by NO
successor is still LOST, and a module not listed here is not relocated at all.

TIERS
-----
  tests/unit/<layer>/   one PACKAGE layer's modules. Six mirror
                        `agentware_lib/<layer>/` (cli, core, features,
                        knowledge, ops, retrieval); `dashboard` mirrors
                        `scripts/agentware_dashboard/`, which task 69
                        decomposed into a package with the same discipline --
                        its security kernel, route table, toolkit seam and
                        entrypoint each own a unit file, while the tests that
                        bind a port stay in `integration/`
  tests/integration/    two or more layers, or a seam this package owns but the
                        lib does not -- the orchestrator, the hooks, the
                        dashboard, the MCP server
  tests/e2e/            a whole user-visible path end to end: a full loop, a
                        fresh clone, a benchmark run
  tests/gates/          the migration and repo-structure gates themselves,
                        whose subject is a `tests/_*.py` harness module or a
                        tree-wide invariant rather than product behaviour

Helper modules (`tests/_*.py`) stay at the corpus root: they are imported by
bare name from every tier, and `tests/_paths.py` is the anchor that makes that
work from any depth.
"""

import os

#: Every test file must stay under this many lines. A file at the cap is a file
#: about to become a second monolith; split it along its class boundaries.
TEST_FILE_LINE_CAP = 600

#: The tier directories, relative to `tests/`.
TIERS = ("unit/cli", "unit/core", "unit/dashboard", "unit/features",
         "unit/knowledge", "unit/ops", "unit/retrieval", "integration", "e2e",
         "gates")

#: {old dotted module: (successor dotted module, ...)} -- the task-58 move.
#: A tuple because task 58 also SPLITS a file over the line cap, and a split
#: sends one old module's tests to several new ones.
#:
#: Task 60 adds the one row that is NOT a task-58 local move: the donor's flat
#: `tests.test_parser_golden` lands here directly at its layered path, so its
#: nine frozen ids need the same module-half rewrite a local move needed. The
#: mechanism is identical (`relocate()` reads only the module half) and the
#: honesty checks are the same -- the old path must not exist, the successor
#: must, and every frozen tail must still collect.
RELOCATIONS = {
    "tests.test_acr": ("tests.unit.retrieval.test_acr",),
    "tests.test_acr_gate": ("tests.unit.retrieval.test_acr_gate",),
    "tests.test_adopted_capabilities": ("tests.integration.test_adopted_capabilities",),
    "tests.test_agent_identity": ("tests.unit.ops.test_agent_identity",),
    "tests.test_alwayson_steering": ("tests.gates.test_alwayson_steering",),
    "tests.test_atomic_save": ("tests.unit.knowledge.test_atomic_save",),
    "tests.test_attach_validate": ("tests.unit.knowledge.test_attach_validate",),
    "tests.test_backfill_migration": ("tests.unit.features.test_backfill_migration",),
    "tests.test_bench_swe": ("tests.e2e.test_bench_swe",),
    "tests.test_bm25_cache": ("tests.unit.retrieval.test_bm25_cache",),
    "tests.test_body_differ": ("tests.gates.test_body_differ",),
    "tests.test_cache_freshness": ("tests.unit.retrieval.test_cache_freshness",),
    "tests.test_checkup_aggregate": ("tests.unit.ops.test_checkup_aggregate",),
    "tests.test_checkup_config": ("tests.unit.features.test_checkup_config",),
    "tests.test_checkup_drift": ("tests.unit.ops.test_checkup_drift",),
    "tests.test_checkup_e2e": ("tests.e2e.test_checkup_e2e",),
    "tests.test_checkup_emit": ("tests.unit.ops.test_checkup_emit",),
    "tests.test_checkup_report": ("tests.unit.ops.test_checkup_report",),
    "tests.test_checkup_tick": ("tests.unit.ops.test_checkup_tick",),
    "tests.test_checkup_triggers": ("tests.unit.ops.test_checkup_triggers",),
    "tests.test_checkup_watcher": ("tests.unit.ops.test_checkup_watcher",),
    "tests.test_codex_log": ("tests.integration.test_codex_log",),
    "tests.test_config_mode": ("tests.unit.features.test_config_mode",),
    "tests.test_constant_baseline": ("tests.gates.test_constant_baseline",),
    "tests.test_constant_differ": (
        "tests.gates.test_constant_differ",
        "tests.gates.test_constant_differ_refutation",
    ),
    # Task 69 decomposed the dashboard: the path-confinement class moved to the
    # security kernel's own unit file while the port-binding tests stayed put,
    # so one old module has two successors (the same shape task 58's file splits
    # produced).
    "tests.test_dashboard": ("tests.integration.test_dashboard",
                             "tests.unit.dashboard.test_confine"),
    "tests.test_dashboard_api": ("tests.integration.test_dashboard_api",),
    "tests.test_decisions": ("tests.unit.knowledge.test_decisions",),
    "tests.test_declare_habitual_e2e": ("tests.e2e.test_declare_habitual_e2e",),
    "tests.test_decommit": (
        "tests.unit.ops.test_decommit",
        "tests.unit.ops.test_decommit_sync",
    ),
    "tests.test_dependency_graph": ("tests.unit.knowledge.test_dependency_graph",),
    "tests.test_donestamp": ("tests.integration.test_donestamp",),
    "tests.test_dream": (
        "tests.unit.features.test_dream",
        "tests.unit.features.test_dream_scheduler",
        "tests.unit.features.test_dream_report",
        "tests.unit.features.test_dream_observability",
    ),
    "tests.test_dream_change_gate": ("tests.unit.features.test_dream_change_gate",),
    "tests.test_dream_min_interval": ("tests.unit.features.test_dream_min_interval",),
    "tests.test_dream_pkg_retire": ("tests.unit.features.test_dream_pkg_retire",),
    "tests.test_dream_skill_propose": ("tests.unit.features.test_dream_skill_propose",),
    "tests.test_dream_stale_propose": ("tests.unit.features.test_dream_stale_propose",),
    "tests.test_dreampkg_retire_gate": ("tests.gates.test_dreampkg_retire_gate",),
    "tests.test_drift_dream": ("tests.unit.features.test_drift_dream",),
    "tests.test_e2e_updater": ("tests.e2e.test_e2e_updater",),
    "tests.test_e2e_writepath": ("tests.e2e.test_e2e_writepath",),
    "tests.test_embedder": ("tests.unit.retrieval.test_embedder",),
    "tests.test_embedder_fastembed": ("tests.unit.retrieval.test_embedder_fastembed",),
    "tests.test_embedding": ("tests.unit.retrieval.test_embedding",),
    "tests.test_env_config_parity": ("tests.gates.test_env_config_parity",),
    "tests.test_eval": ("tests.unit.retrieval.test_eval",),
    "tests.test_eval_pinning": (
        "tests.unit.retrieval.test_eval_pinning",
        "tests.unit.retrieval.test_eval_pinning_seam",
        "tests.unit.retrieval.test_eval_pinning_gold",
    ),
    "tests.test_existing_cli": ("tests.integration.test_existing_cli",),
    "tests.test_fanout": (
        "tests.integration.test_fanout",
        "tests.integration.test_fanout_merge_queue",
    ),
    "tests.test_fresh_clone": ("tests.e2e.test_fresh_clone",),
    "tests.test_freshness": ("tests.unit.ops.test_freshness",),
    "tests.test_freshness_gen": ("tests.unit.features.test_freshness_gen",),
    "tests.test_frontmatter": ("tests.unit.core.test_frontmatter",),
    "tests.test_g1harden": ("tests.gates.test_g1harden",),
    "tests.test_gate": ("tests.unit.retrieval.test_gate",),
    "tests.test_gatebaseline": ("tests.gates.test_gatebaseline",),
    "tests.test_git_argv_batching": ("tests.unit.core.test_git_argv_batching",),
    "tests.test_git_coauthor_guard": ("tests.unit.ops.test_git_coauthor_guard",),
    "tests.test_index_reader_safety": ("tests.unit.knowledge.test_index_reader_safety",),
    "tests.test_index_write_safety": ("tests.unit.knowledge.test_index_write_safety",),
    "tests.test_indexlock": ("tests.unit.knowledge.test_indexlock",),
    "tests.test_ingestion": ("tests.unit.knowledge.test_ingestion",),
    "tests.test_invariants": ("tests.gates.test_invariants",),
    "tests.test_kb_git": (
        "tests.unit.ops.test_kb_git",
        "tests.unit.ops.test_kb_git_push",
        "tests.unit.ops.test_kb_git_contract",
    ),
    "tests.test_kb_push": ("tests.integration.test_kb_push",),
    "tests.test_kbschema_interlock": ("tests.unit.features.test_kbschema_interlock",),
    "tests.test_kbtarget_guard": ("tests.gates.test_kbtarget_guard",),
    "tests.test_learn_hardening": ("tests.unit.features.test_learn_hardening",),
    "tests.test_learn_provenance": ("tests.unit.knowledge.test_learn_provenance",),
    "tests.test_ledger": ("tests.unit.retrieval.test_ledger",),
    "tests.test_legacy_ratchet": (
        "tests.gates.test_legacy_ratchet",
        "tests.gates.test_legacy_ratchet_record",
        "tests.gates.test_legacy_ratchet_terminal",
    ),
    "tests.test_longmemeval": ("tests.e2e.test_longmemeval",),
    "tests.test_loop_emission": ("tests.integration.test_loop_emission",),
    "tests.test_loop_guards": ("tests.integration.test_loop_guards",),
    "tests.test_loop_org_lint": ("tests.integration.test_loop_org_lint",),
    "tests.test_loop_perf": ("tests.integration.test_loop_perf",),
    "tests.test_loop_retrieval_gate": ("tests.integration.test_loop_retrieval_gate",),
    "tests.test_loop_selfheal_gate": ("tests.integration.test_loop_selfheal_gate",),
    "tests.test_loop_transitions": ("tests.e2e.test_loop_transitions",),
    "tests.test_loop_wiring": ("tests.integration.test_loop_wiring",),
    "tests.test_loop_worklog_gate": ("tests.integration.test_loop_worklog_gate",),
    "tests.test_markdown_links": ("tests.gates.test_markdown_links",),
    "tests.test_mcp_server": ("tests.integration.test_mcp_server",),
    "tests.test_metrics": (
        "tests.unit.ops.test_metrics",
        "tests.unit.ops.test_metrics_loop",
        "tests.unit.ops.test_metrics_health",
        "tests.unit.ops.test_metrics_alerts",
    ),
    "tests.test_migrate_runner": ("tests.unit.features.test_migrate_runner",),
    "tests.test_module_ceilings": ("tests.gates.test_module_ceilings",),
    "tests.test_mutation_canary": ("tests.gates.test_mutation_canary",),
    "tests.test_org_curator": ("tests.unit.ops.test_org_curator",),
    "tests.test_org_isolation": ("tests.unit.core.test_org_isolation",),
    "tests.test_org_scoping": ("tests.unit.retrieval.test_org_scoping",),
    "tests.test_orgidentity_gate": ("tests.gates.test_orgidentity_gate",),
    "tests.test_parity_gate": ("tests.gates.test_parity_gate",),
    "tests.test_parity_manifest": ("tests.gates.test_parity_manifest",),
    # donor-side landing (task 60), not a task-58 local move -- see above.
    "tests.test_parser_golden": ("tests.gates.test_parser_golden",),
    "tests.test_pkg_apply_edges": ("tests.unit.features.test_pkg_apply_edges",),
    "tests.test_pkg_channel": ("tests.unit.features.test_pkg_channel",),
    "tests.test_pkg_cleanup": ("tests.unit.features.test_pkg_cleanup",),
    "tests.test_pkg_heartbeat": ("tests.unit.features.test_pkg_heartbeat",),
    "tests.test_pkg_rollback": ("tests.unit.features.test_pkg_rollback",),
    "tests.test_pkg_smoke": ("tests.e2e.test_pkg_smoke",),
    "tests.test_pkg_staleness": ("tests.unit.features.test_pkg_staleness",),
    "tests.test_pkg_state_lock": ("tests.integration.test_pkg_state_lock",),
    "tests.test_pkg_tamper_consistency": ("tests.unit.features.test_pkg_tamper_consistency",),
    "tests.test_pkg_trust_anchor": ("tests.unit.core.test_pkg_trust_anchor",),
    "tests.test_pkg_updater": ("tests.unit.features.test_pkg_updater",),
    "tests.test_plan_artifact_refs": ("tests.gates.test_plan_artifact_refs",),
    "tests.test_plan_authoring": ("tests.unit.ops.test_plan_authoring",),
    "tests.test_plan_graph": (
        "tests.unit.features.test_plan_graph",
        "tests.unit.features.test_plan_graph_scheduler",
        "tests.unit.features.test_plan_graph_driver",
        "tests.unit.features.test_plan_graph_merge",
        "tests.unit.features.test_plan_graph_approval",
        "tests.unit.features.test_plan_graph_e2e",
    ),
    "tests.test_plan_lint": (
        "tests.unit.ops.test_plan_lint",
        "tests.unit.ops.test_plan_lint_rules",
    ),
    "tests.test_poisoned_index": ("tests.integration.test_poisoned_index",),
    "tests.test_portability_gate": ("tests.gates.test_portability_gate",),
    "tests.test_prefer_capture": (
        "tests.unit.ops.test_prefer_capture",
        "tests.unit.ops.test_prefer_capture_evasion",
        "tests.unit.ops.test_prefer_capture_workorder",
    ),
    "tests.test_prefer_classify": ("tests.unit.ops.test_prefer_classify",),
    "tests.test_prefer_context_seam": ("tests.unit.ops.test_prefer_context_seam",),
    "tests.test_prefer_e2e": ("tests.e2e.test_prefer_e2e",),
    "tests.test_prefer_forget": ("tests.unit.ops.test_prefer_forget",),
    "tests.test_prefer_injection": ("tests.integration.test_prefer_injection",),
    "tests.test_prefer_precision": ("tests.unit.ops.test_prefer_precision",),
    "tests.test_prefer_precision_e2e": ("tests.e2e.test_prefer_precision_e2e",),
    "tests.test_prefer_queue": ("tests.unit.ops.test_prefer_queue",),
    "tests.test_prefer_reject": ("tests.unit.ops.test_prefer_reject",),
    "tests.test_prefer_review": ("tests.unit.ops.test_prefer_review",),
    "tests.test_prefer_scope": ("tests.unit.ops.test_prefer_scope",),
    "tests.test_premise_gate": ("tests.unit.ops.test_premise_gate",),
    "tests.test_premise_prehook": ("tests.integration.test_premise_prehook",),
    "tests.test_presence_gate": ("tests.gates.test_presence_gate",),
    "tests.test_preserved_gates": (
        "tests.gates.test_preserved_gates",
        "tests.gates.test_preserved_collision",
        "tests.gates.test_preserved_seam",
    ),
    "tests.test_preserved_surface": ("tests.gates.test_preserved_surface",),
    "tests.test_promotion_gate": ("tests.gates.test_promotion_gate",),
    "tests.test_provenance_logging": ("tests.integration.test_provenance_logging",),
    "tests.test_provenance_observability": ("tests.integration.test_provenance_observability",),
    "tests.test_quarantine": ("tests.unit.knowledge.test_quarantine",),
    "tests.test_readme_union": ("tests.gates.test_readme_union",),
    "tests.test_rebuild": ("tests.unit.features.test_rebuild",),
    "tests.test_recall": ("tests.unit.retrieval.test_recall",),
    "tests.test_recall_mode": ("tests.unit.retrieval.test_recall_mode",),
    "tests.test_relate": ("tests.unit.knowledge.test_relate",),
    "tests.test_release_gate": ("tests.unit.ops.test_release_gate",),
    "tests.test_release_preflight": ("tests.unit.features.test_release_preflight",),
    "tests.test_remediate": ("tests.unit.ops.test_remediate",),
    "tests.test_resolver_parity": ("tests.integration.test_resolver_parity",),
    "tests.test_retrieval_lint": ("tests.unit.ops.test_retrieval_lint",),
    "tests.test_retrieval_metrics": ("tests.unit.ops.test_retrieval_metrics",),
    "tests.test_reverse_guard": ("tests.unit.ops.test_reverse_guard",),
    "tests.test_review_config": ("tests.unit.ops.test_review_config",),
    "tests.test_review_orchestrator": (
        "tests.unit.ops.test_review_orchestrator",
        "tests.unit.ops.test_review_retry",
        "tests.unit.ops.test_review_failclosed",
        "tests.unit.ops.test_review_spawn_seam",
        "tests.unit.ops.test_review_injection",
        "tests.unit.ops.test_review_verify_critic",
        "tests.unit.ops.test_review_containment",
    ),
    "tests.test_review_report": ("tests.unit.ops.test_review_report",),
    "tests.test_schemaguard": ("tests.unit.features.test_schemaguard",),
    "tests.test_scopework_recall": ("tests.unit.retrieval.test_scopework_recall",),
    "tests.test_scorecard": ("tests.unit.retrieval.test_scorecard",),
    "tests.test_setstate_matrix": ("tests.unit.ops.test_setstate_matrix",),
    "tests.test_skill_e2e": ("tests.e2e.test_skill_e2e",),
    "tests.test_skill_hook": ("tests.integration.test_skill_hook",),
    "tests.test_skill_lockfile": ("tests.unit.features.test_skill_lockfile",),
    "tests.test_skill_pin_floor": ("tests.unit.ops.test_skill_pin_floor",),
    "tests.test_skill_promote": (
        "tests.unit.features.test_skill_promote",
        "tests.unit.features.test_skill_promote_cluster",
    ),
    "tests.test_skill_roster": ("tests.unit.features.test_skill_roster",),
    "tests.test_skill_sync": ("tests.unit.features.test_skill_sync",),
    "tests.test_skill_sync_adjudicate": ("tests.unit.features.test_skill_sync_adjudicate",),
    "tests.test_skill_trust": ("tests.unit.features.test_skill_trust",),
    "tests.test_sortonce": ("tests.unit.knowledge.test_sortonce",),
    "tests.test_stage_donor": ("tests.gates.test_stage_donor",),
    "tests.test_staleness": ("tests.unit.ops.test_staleness",),
    "tests.test_staleness_downweight": ("tests.unit.knowledge.test_staleness_downweight",),
    "tests.test_staleness_propose": ("tests.unit.ops.test_staleness_propose",),
    "tests.test_steering_content": ("tests.gates.test_steering_content",),
    "tests.test_strangler_routing": ("tests.integration.test_strangler_routing",),
    "tests.test_suite_delta": ("tests.gates.test_suite_delta",),
    "tests.test_surface_fixture": ("tests.gates.test_surface_fixture",),
    "tests.test_surface_union": ("tests.gates.test_surface_union",),
    "tests.test_target_injection": ("tests.integration.test_target_injection",),
    "tests.test_team_mode_e2e": ("tests.e2e.test_team_mode_e2e",),
    "tests.test_templates_lint": ("tests.unit.knowledge.test_templates_lint",),
    "tests.test_tick_dispatch": ("tests.unit.features.test_tick_dispatch",),
    "tests.test_tick_dream_cadence": ("tests.unit.ops.test_tick_dream_cadence",),
    "tests.test_tick_schedule": ("tests.unit.ops.test_tick_schedule",),
    "tests.test_transcript_miner": (
        "tests.unit.ops.test_transcript_miner",
        "tests.unit.ops.test_transcript_miner_ledger",
        "tests.unit.ops.test_transcript_miner_dedup",
        "tests.unit.ops.test_transcript_miner_reconcile",
    ),
    "tests.test_transcript_miner_e2e": ("tests.e2e.test_transcript_miner_e2e",),
    "tests.test_transcript_miner_governance": ("tests.unit.ops.test_transcript_miner_governance",),
    "tests.test_transcript_miner_wiring": ("tests.integration.test_transcript_miner_wiring",),
    "tests.test_trust_determinism": ("tests.unit.features.test_trust_determinism",),
    "tests.test_trust_e2e": ("tests.e2e.test_trust_e2e",),
    "tests.test_trust_surface": ("tests.unit.features.test_trust_surface",),
    "tests.test_update_state": ("tests.unit.features.test_update_state",),
    "tests.test_updater_activation": ("tests.unit.features.test_updater_activation",),
    "tests.test_vector_cache": ("tests.unit.retrieval.test_vector_cache",),
    "tests.test_whereami": ("tests.unit.cli.test_whereami",),
    "tests.test_whereami_gate": ("tests.gates.test_whereami_gate",),
    "tests.test_workcache": ("tests.unit.retrieval.test_workcache",),
    "tests.test_worklog_promotion": ("tests.unit.knowledge.test_worklog_promotion",),
    "tests.test_workorder_emit": ("tests.unit.ops.test_workorder_emit",),
}


def relocate(node_id):
    """Every id a frozen `node_id` may legitimately have become.

    Returns the id unchanged when its module was never relocated, so callers
    can apply this unconditionally.
    """
    parts = node_id.split(".")
    module, tail = ".".join(parts[:-2]), ".".join(parts[-2:])
    successors = RELOCATIONS.get(module)
    if not successors:
        return (node_id,)
    return tuple("%s.%s" % (succ, tail) for succ in successors)


def resolves(node_id, live):
    """Does `node_id` -- possibly relocated -- collect in `live`?"""
    return any(candidate in live for candidate in relocate(node_id))


#: Directories under `tests/` that hold DATA, not tests. `fixtures/swe_tasks`
#: ships `test_hidden.py` files that are inputs to the SWE benchmark -- they are
#: never collected, and counting them as corpus would make the tier and cap
#: checks fail on files the suite does not own.
NON_CORPUS_DIRS = ("__pycache__", "fixtures", "baselines")


def test_files(tests_dir):
    """Every collected test module path under `tests_dir`, repo-relative."""
    out = []
    for dirpath, dirnames, filenames in os.walk(tests_dir):
        dirnames[:] = [d for d in dirnames if d not in NON_CORPUS_DIRS]
        for name in filenames:
            if name.startswith("test_") and name.endswith(".py"):
                out.append(os.path.join(dirpath, name))
    return sorted(out)


def flat_test_files(tests_dir):
    """Test files still sitting directly under `tests/` -- must be empty."""
    return sorted(os.path.join(tests_dir, n) for n in os.listdir(tests_dir)
                  if n.startswith("test_") and n.endswith(".py"))


def oversized_test_files(tests_dir, cap=TEST_FILE_LINE_CAP):
    """{path: line count} for every test file over the cap."""
    out = {}
    for path in test_files(tests_dir):
        with open(path, encoding="utf-8") as fh:
            count = sum(1 for _ in fh)
        if count > cap:
            out[path] = count
    return out


def untiered_test_files(tests_dir):
    """Test files that are not inside one of the declared tiers."""
    tiers = tuple(os.path.join(tests_dir, t.replace("/", os.sep))
                  for t in TIERS)
    return [p for p in test_files(tests_dir)
            if not any(p.startswith(t + os.sep) for t in tiers)]
