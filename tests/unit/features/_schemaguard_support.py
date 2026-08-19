"""Shared header for the `test_schemaguard` suite, split out by task 58.

Holds every import, helper, constant and base class the split
parts share, so no part duplicates a definition and none can
drift from another.
"""

import argparse
import contextlib
import importlib.machinery
import importlib.util
import io
import json
import os
import shutil
import tempfile
import unittest
try:
    from tests import _fixtures
    from tests._fixtures import REPO_ROOT, build_synthetic_kb, run_cli, attach_lib_resolver
except ImportError:
    import _fixtures
    from _fixtures import REPO_ROOT, build_synthetic_kb, run_cli, attach_lib_resolver
def _load():
    ld = importlib.machinery.SourceFileLoader(
        "aw_schemaguard", os.path.join(REPO_ROOT, "scripts", "agentware"))
    m = importlib.util.module_from_spec(
        importlib.util.spec_from_loader("aw_schemaguard", ld))
    ld.exec_module(m)
    return attach_lib_resolver(m)
def _migrate_mod():
    """The module that OWNS the migration registry + runner (task 37).

    Unlike the facade, which every loader exec's into a namespace of its own,
    this resolves through `sys.modules` — so the object this returns is the same
    one BOTH facade instances and the modular dispatch arm read. That is what
    makes patching it a real interlock probe rather than a per-instance illusion.
    """
    _load()   # puts scripts/ on sys.path
    return importlib.import_module("agentware_lib.features.migratecmd")
def _lib_cli():
    """The lib's parser assembler — the arm that serves every PORTED verb."""
    _load()   # puts scripts/ on sys.path
    return importlib.import_module("agentware_lib.cli")
_GROUND_TRUTH_WRITERS = {
    "cmd_index_add", "cmd_index_remove", "cmd_index_sync", "cmd_index_rebuild",
    "cmd_index_migrate",
    "cmd_learn", "cmd_decide", "cmd_ingest",
    # declare verb (260717 follow-up B1): mutates the subject `.md` + index.json
    # via _declare_relation -> save_index, registered by func name so the
    # predispatch schema-guard refuses it on a newer KB.
    "cmd_relate",
    # steering-capture verb (260712-steering-capture, Task 2): sugar over
    # cmd_learn(--source user) + cmd_index_remove, but the predispatch gate keys
    # on the dispatched args.func, so it MUST be registered by func name.
    "cmd_prefer",
    # steering-capture approve (260712, Task 7): a distinct dispatched func that
    # mutates prefer-queue state (proposed -> approved) — registered by func name.
    "cmd_prefer_approve",
    # steering-capture reject (260719): the governed counterpart to approve — a
    # dispatched func that flips proposed -> rejected + cancels the workorder.
    # NOT an index writer (no save_index/rebuild_kb), so like cmd_prefer_approve it
    # is absent from _EXPECTED_INDEX_WRITER_DEFS and present here + _kb_writer_funcs.
    "cmd_prefer_reject",
    # P2c transcript miner (260713): the SECOND prefer-queue producer — a
    # dispatched WRITER (appends candidates + persists its own state), registered
    # by func name so the predispatch schema-guard covers it. Not an index writer
    # (no save_index/rebuild_kb), so it is absent from _EXPECTED_INDEX_WRITER_DEFS.
    "cmd_prefer_mine",
    "cmd_plan_new", "cmd_plan_add_task", "cmd_plan_set_state",
    "cmd_plan_set_status", "cmd_plan_claim", "cmd_plan_state_backfill",
    "cmd_features", "cmd_skill_add", "cmd_skill_remove",
    # cmd_dream: step-a rebuilds the structured index in-process (registered
    # after the 260711 adversarial review found it unguarded + cron-installable).
    "cmd_dream",
}
_EXPECTED_INDEX_WRITER_DEFS = {
    # def name -> owning dispatch command (None = primitive/helper of below)
    "cmd_init": "cmd_init",                       # bootstrap (pre-initialization)
    "cmd_index_add": "cmd_index_add",
    "cmd_index_remove": "cmd_index_remove",
    "cmd_index_sync": "cmd_index_sync",
    # `rebuild_kb` itself is NOT a static row any more: its monolith copy held
    # the `save_index` call inline, while the lib's copy acquires the index lock
    # and hands the body to `_rebuild_kb_locked` below (SAME primitive
    # classification), so the write sits one frame down. It is added back by
    # `_expected_index_writer_defs()` for exactly as long as the monolith
    # exists, and its retirement is enforced by `_RETIRED_INTO` rather than
    # taken on the word "decomposed".
    #
    # `rebuild_kb`'s body with the index lock already HELD, split out so the
    # acquisition is one unmissable line at the single entry point. Same
    # classification as its caller — a primitive, not a dispatch command — and
    # inherently schema-safe for the same reason (it regenerates the DERIVED
    # index from untouched frontmatter). Surfaced at task 41, when
    # `_fixtures.cli_source` was widened to scan a ported verb's WHOLE
    # implementation graph instead of just its registrar; it had been invisible
    # to this scan, not absent from the tree.
    "_rebuild_kb_locked": None,
    # P1.7 (write-path scale foundation): two thin helpers that wrap rebuild_kb to
    # regenerate ONLY the DERIVED index (now de-committed + gitignored) from the
    # untouched frontmatter source — inherently schema-SAFE (like rebuild_kb, and
    # the git-sync rebuilds), so classified as primitives (None), not version-gated
    # mutation commands. _try_rebuild_missing_index recovers a missing/corrupt index
    # on load; _rebuild_kb_after_pull reconciles the index after a fast-forward pull.
    "_try_rebuild_missing_index": None,
    "_rebuild_kb_after_pull": None,
    "cmd_index_rebuild": "cmd_index_rebuild",
    "_attach_migrate_structure": "cmd_attach",   # arbitrary candidate path
    "cmd_learn": "cmd_learn",
    "cmd_decide": "cmd_decide",
    "cmd_ingest": "cmd_ingest",
    # relate verb's index writer. PORTED at task 38 into
    # agentware_lib/knowledge/relatecmd.py, where the monolith's single ~150-line
    # `_declare_relation` was split at its four existing stages to satisfy the
    # per-function ceiling; the save_index call went with the reconcile stage. The
    # OWNING COMMAND is unchanged, which is the only thing this map governs.
    "_relate_commit_index": "cmd_relate",

    "kb_git_push_once": "cmd_kb_git_commit",     # git-sync rebuild-before-commit
    "kb_git_merge_continue": "cmd_kb_git_merge_continue",
    "_mq_integrate_kb": "cmd_fanout_merge_queue",
    # `_dream_step_kb_pull` was here until the monolith was deleted. It is NOT a
    # capability that went away: the lib's copy delegates its whole derived
    # reconcile to `_dream_pull_resolve_derived` (classified below, SAME owning
    # command), so the write moved one frame down and the scan follows it there.
    # The monolith's copy performed the rebuild INLINE, so while that file stood
    # its name was discovered too and the row was load-bearing. Retiring it is
    # enforced, not assumed -- see _RETIRED_INTO below.
    "_dream_step_index_rebuild": "cmd_dream",
    # --- task 54 (the `skill` port) ------------------------------------------
    # cmd_skill_add / cmd_skill_remove / cmd_skill_propose no longer appear here
    # because they no longer WRITE the index themselves: each delegates its
    # load->mutate->save to a named receiver that holds `index._index_lock`
    # (skill_register_entry / skill_unregister_entry / _propose_author, all
    # classified below). Their entries are removed rather than left in place, so
    # this set keeps meaning "the functions that actually write" — a stale entry
    # here is a function the gate believes it is policing and is not.

    # --- surfaced at task 41 -------------------------------------------------
    # Six index writers that live in `agentware_lib` and had been INVISIBLE to
    # this scan, not absent from the tree: `_fixtures.cli_source` used to
    # concatenate only the entrypoint, the monolith and each ported verb's
    # REGISTRAR module. Task 41 widened it to the whole lib (a verb's
    # implementation now spans many modules — `update` alone spans six), and
    # these appeared. Each is classified by the command that actually dispatches
    # it, which is what this map governs; none is a new capability.
    "skill_register_entry": "cmd_skill_add",       # lock-wrapped index RMW
    "skill_unregister_entry": "cmd_skill_remove",  # lock-wrapped index RMW
    "cmd_skill_promote": "cmd_skill_promote",
    # `skill propose` became the two-lifecycle SELECTOR at task 54; the ledger
    # write it used to perform inline moved to `_propose_author`, which is what
    # the scan now sees. Owning command unchanged — the only thing this map governs.
    "_propose_author": "cmd_skill_propose",
    "_scaffold_team_dir": "cmd_team",              # team create|register
    "_dream_pull_resolve_derived": "cmd_dream",    # kb-pull step's derived reconcile
}
_RETIRED_INTO = {
    # the lock acquisition stayed at the entry point, the body moved down
    "rebuild_kb": "_rebuild_kb_locked",
    # the kb-pull step delegates its whole derived reconcile
    "_dream_step_kb_pull": "_dream_pull_resolve_derived",
}
def _expected_index_writer_defs():
    """The expectation for the tree AS IT CURRENTLY STANDS.

    TERMINAL-AWARE, the shape tasks 92 and 93 settled on for this wave. While
    `scripts/agentware_legacy.py` exists the scan reads BOTH implementations,
    and the monolith's copies of the `_RETIRED_INTO` keys perform the index
    write INLINE -- so those names really are discovered and really must be
    classified. Once the file is gone only the lib's delegating copies remain
    and the same names must NOT be expected.

    Deriving this from the file's existence rather than pinning one of the two
    answers is what lets the gate pass in both states across the deletion
    commit, without a window in which it is switched off.
    """
    expected = dict(_EXPECTED_INDEX_WRITER_DEFS)
    if os.path.isfile(_fixtures.LEGACY_PATH):
        expected["rebuild_kb"] = None
        expected["_dream_step_kb_pull"] = "cmd_dream"
    return expected
_INDEX_WRITER_EXEMPT_COMMANDS = {
    "cmd_init", "cmd_attach", "cmd_kb_git_commit", "cmd_kb_git_merge_continue",
    "cmd_fanout_merge_queue",
    # --- NOT-YET-GUARDED, surfaced at task 41 (follow-up: task 54) -----------
    # These four write the structured index from `agentware_lib` and are guarded
    # by NEITHER tree's writer registry. That is a PRE-EXISTING hole, not a task-41
    # regression: the monolith does not define `cmd_skill_promote` at all and never
    # listed `cmd_skill_propose` in `_kb_writer_funcs`, so they arrived unguarded
    # with the donor. They were invisible until task 41 widened
    # `_fixtures.cli_source` from "each ported verb's registrar" to the whole lib.
    #
    # They are exempted rather than registered because registering them means
    # adding `_KB_WRITER_TARGETS` rows, and the interlock's correspondence check
    # only requires those for verbs already in `strangler.PORTED_VERBS`. `skill`,
    # `team` and `dream` are NOT ported yet — their rows land with them in the
    # shared-verb wave (task 54), which is where this exemption must be removed.
    # Until then an older CLI can write a newer/migrated KB through these paths.
    "cmd_skill_promote", "cmd_skill_propose", "cmd_team", "cmd_dream",
}
def _collect_dispatch_funcs(parser, acc):
    """Walk the argparse tree collecting every set_defaults(func=…)."""
    d = parser._defaults.get("func")
    if d is not None:
        acc.add(d)
    for action in parser._actions:
        if isinstance(action, argparse._SubParsersAction):
            for sub in action.choices.values():
                _collect_dispatch_funcs(sub, acc)
    return acc
if __name__ == "__main__":
    unittest.main()
