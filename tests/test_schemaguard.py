"""Central KB-writer registry / schema-guard coverage tests
(feature 260711-emitter-gate-hardening, T3).

Before this feature the older-CLI schema guard covered only index/learn/decide/
ingest — the plan-state writers (cmd_plan_new, cmd_plan_set_status,
cmd_plan_claim — which runs EVERY loop iteration — cmd_plan_set_state,
cmd_plan_state_backfill), cmd_features and the skill writers were UNGUARDED, so
an older CLI could silently mutate a newer (migrated) KB through those verbs.

A single registry (`_kb_writer_funcs`, the version-refusal SUPERSET) now gates
every KB-mutating verb; a separate `_kb_autoapply_funcs` subset (superset minus
the migration-runner verb `cmd_plan_state_backfill`) drives the on-stale-KB
auto-apply so migration 001's backfill never double-runs. `cmd_migrate` and
`cmd_attach` are deliberately NOT registered.

Tests (behavioral, not grep-shaped):
  - COMPLETENESS: walk the argparse set_defaults(func=…) tree and assert the
    registry equals a ground-truth writer set (deliberate-omission canary);
  - per-writer newer-.schema refusal via the real dispatch gate;
  - recall/query stay EXEMPT (not refused);
  - `migrate --list` on an older CLI prints the mismatch honestly and leaves
    `.schema` UNTOUCHED (read-only).

`test_schemaguard_` is unique vs every existing module filename and method.
Runner: python3 -m unittest tests.test_schemaguard -v
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
    from tests._fixtures import REPO_ROOT, build_synthetic_kb, run_cli
except ImportError:
    from _fixtures import REPO_ROOT, build_synthetic_kb, run_cli


def _load():
    ld = importlib.machinery.SourceFileLoader(
        "aw_schemaguard", os.path.join(REPO_ROOT, "scripts", "agentware"))
    m = importlib.util.module_from_spec(
        importlib.util.spec_from_loader("aw_schemaguard", ld))
    ld.exec_module(m)
    return m


def _migrate_mod():
    """The module that OWNS the migration registry + runner (task 37).

    Unlike the facade, which every loader exec's into a namespace of its own,
    this resolves through `sys.modules` — so the object this returns is the same
    one BOTH facade instances and the modular dispatch arm read. That is what
    makes patching it a real interlock probe rather than a per-instance illusion.
    """
    _load()   # puts scripts/ on sys.path
    return importlib.import_module("agentware_lib.features.migratecmd")


# The GROUND TRUTH: every KB-mutating command verb. If a new writer verb is added
# without registering it, the completeness test below fails LOUDLY. cmd_migrate /
# cmd_attach are intentionally absent (see registry docstring).
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

# Top-level defs that call save_index()/rebuild_kb() (the structured-index
# rewriters). Discovered by scanning the CLI source in
# test_schemaguard_no_unregistered_index_writer, this PINNED set means a NEW
# index-writing function anywhere in the CLI trips the test — the genuinely
# non-vacuous completeness check that WOULD have caught cmd_dream. Each maps to the
# dispatch COMMAND that reaches it; that command must be registered OR a documented
# exemption (git-sync / fanout-merge / bootstrap / attach — see the DECISIONs in
# the feature worklog: they rebuild only {entries,tags} from migration-invariant
# frontmatter as part of a git/merge/init flow).
_EXPECTED_INDEX_WRITER_DEFS = {
    # def name -> owning dispatch command (None = primitive/helper of below)
    "cmd_init": "cmd_init",                       # bootstrap (pre-initialization)
    "cmd_index_add": "cmd_index_add",
    "cmd_index_remove": "cmd_index_remove",
    "cmd_index_sync": "cmd_index_sync",
    "rebuild_kb": None,                           # the primitive itself
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
    "_declare_relation": "cmd_relate",           # relate verb's index writer

    "kb_git_push_once": "cmd_kb_git_commit",     # git-sync rebuild-before-commit
    "kb_git_merge_continue": "cmd_kb_git_merge_continue",
    "_mq_integrate_kb": "cmd_fanout_merge_queue",
    "_dream_step_kb_pull": "cmd_dream",
    "_dream_step_index_rebuild": "cmd_dream",
    "cmd_skill_add": "cmd_skill_add",
    "cmd_skill_remove": "cmd_skill_remove",
}
# Commands that legitimately rewrite the index but are EXEMPT from the schema gate
# for the CURRENT schema (documented DECISION in the worklog): git-sync / merge
# rebuilds regenerate only {entries,tags} from migration-001-invariant frontmatter
# during a commit/merge; init is the pre-initialization bootstrap; attach targets
# an arbitrary candidate path with its own conformance flow.
_INDEX_WRITER_EXEMPT_COMMANDS = {
    "cmd_init", "cmd_attach", "cmd_kb_git_commit", "cmd_kb_git_merge_continue",
    "cmd_fanout_merge_queue",
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


class SchemaGuardTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.m = _load()

    def setUp(self):
        self.kdir = tempfile.mkdtemp(prefix="agentware-schemaguard-")
        self.addCleanup(shutil.rmtree, self.kdir, True)
        build_synthetic_kb(self.kdir)
        # Mark initialized so the predispatch gate engages (it no-ops on an
        # uninitialized KB — the onboarding path).
        with open(os.path.join(self.kdir, ".initialized"), "w",
                  encoding="utf-8") as f:
            f.write("test\n")

    def _write_schema(self, n):
        with open(os.path.join(self.kdir, ".schema"), "w", encoding="utf-8") as f:
            f.write("%d\n" % n)

    def _read_schema_raw(self):
        p = os.path.join(self.kdir, ".schema")
        if not os.path.exists(p):
            return None
        with open(p, encoding="utf-8") as f:
            return f.read()

    def _gate(self, func):
        """Run the real dispatch gate for `func` against this KB."""
        prev = os.environ.get("AGENTWARE_KNOWLEDGE_DIR")
        os.environ["AGENTWARE_KNOWLEDGE_DIR"] = self.kdir
        try:
            with contextlib.redirect_stderr(io.StringIO()):
                return self.m._kb_predispatch_gate(argparse.Namespace(func=func))
        finally:
            if prev is None:
                os.environ.pop("AGENTWARE_KNOWLEDGE_DIR", None)
            else:
                os.environ["AGENTWARE_KNOWLEDGE_DIR"] = prev

    # --- completeness -------------------------------------------------------
    def test_schemaguard_registry_equals_ground_truth(self):
        registry = {f.__name__ for f in self.m._kb_writer_funcs()}
        self.assertEqual(
            registry, _GROUND_TRUTH_WRITERS,
            "registry drifted from the ground-truth writer set: "
            "missing=%s extra=%s" % (
                sorted(_GROUND_TRUTH_WRITERS - registry),
                sorted(registry - _GROUND_TRUTH_WRITERS)))

    def test_schemaguard_completeness_canary(self):
        # Deliberate-omission canary: proves the completeness assertion is not
        # vacuous — dropping the loop's every-iteration writer MUST be detected.
        registry = {f.__name__ for f in self.m._kb_writer_funcs()}
        self.assertIn("cmd_plan_claim", registry,
                      "cmd_plan_claim runs every loop iteration — the original hole")
        omitted = registry - {"cmd_plan_claim"}
        self.assertNotEqual(omitted, _GROUND_TRUTH_WRITERS,
                            "canary: an omitted writer must break the equality")

    def test_schemaguard_every_registry_func_is_dispatchable(self):
        parser_funcs = {f.__name__
                        for f in _collect_dispatch_funcs(self.m.build_parser(), set())}
        registry = {f.__name__ for f in self.m._kb_writer_funcs()}
        self.assertTrue(registry <= parser_funcs,
                        "registry has non-dispatchable funcs: %s"
                        % sorted(registry - parser_funcs))

    def test_schemaguard_migrate_and_attach_excluded(self):
        registry = {f.__name__ for f in self.m._kb_writer_funcs()}
        self.assertNotIn("cmd_migrate", registry)
        self.assertNotIn("cmd_attach", registry)

    def test_schemaguard_autoapply_excludes_dualmode_and_runner(self):
        # Auto-apply excludes the migration-runner verb (double-run) AND the
        # dual-mode verbs whose read-only path must never write .schema: cmd_dream
        # (--dry-run) and cmd_prefer (digest/list/scan-queue run at session-start /
        # Stop-hook time). All three still carry version-refusal via the superset.
        superset = self.m._kb_writer_funcs()
        autoapply = self.m._kb_autoapply_funcs()
        self.assertEqual({f.__name__ for f in (superset - autoapply)},
                         {"cmd_plan_state_backfill", "cmd_dream", "cmd_prefer"})

    def test_schemaguard_dream_registered_but_not_autoapply(self):
        reg = {f.__name__ for f in self.m._kb_writer_funcs()}
        auto = {f.__name__ for f in self.m._kb_autoapply_funcs()}
        self.assertIn("cmd_dream", reg, "cmd_dream must be version-refusal guarded")
        self.assertNotIn("cmd_dream", auto,
                         "cmd_dream --dry-run must not trigger a .schema write")

    def test_schemaguard_dream_refuses_on_newer_kb(self):
        self._write_schema(self.m.KB_SCHEMA_VERSION + 1)
        self.assertEqual(self._gate(self.m.cmd_dream), 1,
                         "dream must REFUSE on a newer KB (rebuild_kb would corrupt it)")

    def test_schemaguard_no_unregistered_index_writer(self):
        # THE non-vacuous completeness check: scan the CLI source for every
        # save_index()/rebuild_kb() call site, map to the enclosing top-level def,
        # and assert (1) the writer-def set matches the pinned expectation (a NEW
        # index-writing function trips this — this is what would have caught
        # cmd_dream), and (2) every writer-def's owning command is registered OR a
        # documented exemption.
        import re
        try:
            from tests._fixtures import cli_source
        except ImportError:
            from _fixtures import cli_source
        # The IMPLEMENTATION source, not the entrypoint path. Task 25 moved the
        # monolith body to scripts/agentware_legacy.py behind a thin facade, and
        # a scan still reading the entrypoint would find ZERO writer defs and
        # report the completeness check as "changed" rather than as "looking at
        # nothing" — the vacuous shape this check exists to prevent.
        lines = cli_source().split("\n")
        defs = [(i + 1, re.match(r"^def (\w+)\(", l).group(1))
                for i, l in enumerate(lines) if re.match(r"^def \w+\(", l)]

        def enclosing(ln):
            name = "<module>"
            for dl, dn in defs:
                if dl <= ln:
                    name = dn
                else:
                    break
            return name

        discovered = set()
        for i, l in enumerate(lines):
            if re.search(r"\b(save_index|rebuild_kb)\(", l) and \
                    not re.match(r"^def (save_index|rebuild_kb)\(", l):
                discovered.add(enclosing(i + 1))
        self.assertEqual(
            discovered, set(_EXPECTED_INDEX_WRITER_DEFS),
            "structured-index writer functions changed — classify the new/removed "
            "one (register its command or add a documented exemption): missing=%s "
            "unexpected=%s" % (
                sorted(set(_EXPECTED_INDEX_WRITER_DEFS) - discovered),
                sorted(discovered - set(_EXPECTED_INDEX_WRITER_DEFS))))

        registered = {f.__name__ for f in self.m._kb_writer_funcs()}
        for wdef, command in _EXPECTED_INDEX_WRITER_DEFS.items():
            if command is None:
                continue
            self.assertTrue(
                command in registered or command in _INDEX_WRITER_EXEMPT_COMMANDS,
                "index-writer '%s' -> command '%s' is neither schema-guarded nor a "
                "documented exemption" % (wdef, command))

    # --- newer-.schema refusal for EVERY writer (older CLI on newer KB) ------
    def test_schemaguard_all_writers_refuse_on_newer_kb(self):
        self._write_schema(self.m.KB_SCHEMA_VERSION + 1)
        for func in self.m._kb_writer_funcs():
            self.assertEqual(
                self._gate(func), 1,
                "writer %s must REFUSE (rc 1) on a newer KB" % func.__name__)

    def test_schemaguard_named_writers_refuse(self):
        # The specific verbs the plan calls out (previously unguarded).
        self._write_schema(self.m.KB_SCHEMA_VERSION + 1)
        for name in ("cmd_plan_new", "cmd_plan_set_status", "cmd_plan_claim",
                     "cmd_features", "cmd_skill_remove"):
            self.assertEqual(self._gate(getattr(self.m, name)), 1,
                             "%s must refuse on a newer KB" % name)

    def test_schemaguard_readers_exempt_on_newer_kb(self):
        self._write_schema(self.m.KB_SCHEMA_VERSION + 1)
        for name in ("cmd_recall", "cmd_query", "cmd_config", "cmd_metrics"):
            self.assertIsNone(self._gate(getattr(self.m, name)),
                              "%s is read-only and must NOT be gated" % name)

    # --- CLI-level realism (through main() + argparse) ----------------------
    def test_schemaguard_cli_writer_refuses_reader_works(self):
        self._write_schema(self.m.KB_SCHEMA_VERSION + 1)
        # a writer verb refuses loud...
        code, _o, err = run_cli(["plan", "new", "990101-x", "--title", "t"],
                                self.kdir)
        self.assertEqual(code, 1)
        self.assertIn("older CLI on a newer KB", err)
        # ...features too...
        code, _o, err = run_cli(["features"], self.kdir)
        self.assertEqual(code, 1)
        # ...while a reader still works.
        code, _o, _e = run_cli(["recall", "bm25 deterministic ranking"], self.kdir)
        self.assertEqual(code, 0)

    # --- migrate --list honesty + read-only ---------------------------------
    def test_schemaguard_migrate_list_older_cli(self):
        newer = self.m.KB_SCHEMA_VERSION + 1
        self._write_schema(newer)
        before = self._read_schema_raw()
        code, out, _e = run_cli(["migrate", "--list"], self.kdir)
        self.assertEqual(code, 0)
        self.assertIn("OLDER CLI", out)
        self.assertIn("NEWER KB", out)
        self.assertNotIn("up to date", out)
        # read-only: .schema byte-identical after --list.
        self.assertEqual(self._read_schema_raw(), before,
                         "migrate --list must not touch .schema")

    def test_schemaguard_migrate_list_read_only_when_stale(self):
        # cur < target: prints pending, still never creates/writes .schema here
        # beyond what already exists (bootstrap case: no .schema file).
        code, out, _e = run_cli(["migrate", "--list"], self.kdir)
        self.assertEqual(code, 0)
        self.assertFalse(os.path.exists(os.path.join(self.kdir, ".schema")),
                         "migrate --list must NOT create .schema")

    # --- T7 (260713-p2a-capture-hardening): prefer WRITE migration contract --
    def test_schemaguard_cli_prefer_capture_refuses_on_newer_kb(self):
        # The P1.6 hole stays CLOSED: an older CLI must REFUSE a `prefer capture`
        # on a NEWER-schema KB. cmd_prefer stays in the version-refusal superset
        # even though it is EXCLUDED from the migration auto-apply set. Real
        # dispatch through main()+argparse.
        self._write_schema(self.m.KB_SCHEMA_VERSION + 1)
        code, _o, err = run_cli(
            ["prefer", "capture", "always pin dependency versions", "--global"],
            self.kdir)
        self.assertEqual(code, 1)
        self.assertIn("older CLI on a newer KB", err)

    def test_schemaguard_prefer_write_refuses_pending_restructuring(self):
        # A prefer WRITE must NOT land against a schema about to be RESTRUCTURED
        # (the case the auto-apply exclusion would otherwise silently admit), while
        # an additive-only pending migration STILL captures (forward-compatible).
        # Tested directly on the gate. The registry is patched on the module that
        # OWNS it since task 37 (agentware_lib.features.migratecmd): the monolith
        # reaches the runner through that module at call time, so patching the
        # owner is what BOTH dispatch arms see — patching the facade re-export
        # would rebind a name nothing consults and pass vacuously.
        def _noop(_k):
            return None
        mig = _migrate_mod()
        prev = mig._KB_MIGRATIONS
        self.addCleanup(setattr, mig, "_KB_MIGRATIONS", prev)
        # Inject a restructuring migration ABOVE the current schema; .schema = the
        # CLI's current version so version-refusal (cur > version) does NOT fire.
        mig._KB_MIGRATIONS = prev + (
            (prev[-1][0] + 1, "restructuring", "big-move", _noop),)
        self._write_schema(self.m.KB_SCHEMA_VERSION)
        with contextlib.redirect_stderr(io.StringIO()) as errbuf:
            rc = self.m._prefer_write_migration_gate(self.kdir)
        self.assertEqual(rc, 1, "a pending restructuring must refuse a prefer write")
        self.assertIn("restructuring migration", errbuf.getvalue())
        # An additive-only pending migration (cur=0 < schema 1) must NOT refuse.
        mig._KB_MIGRATIONS = prev
        os.remove(os.path.join(self.kdir, ".schema"))
        self.assertIsNone(
            self.m._prefer_write_migration_gate(self.kdir),
            "an additive-only pending migration must NOT refuse a prefer write")

    def test_schemaguard_prefer_capture_cli_migration_gate_e2e(self):
        # END-TO-END via main() (audit round-2 coverage gap): the migration gate is
        # wired into the real `prefer capture` CLI path. The registry has ONE home
        # since task 37 and both facade instances reach it, so there is no longer
        # an instance to choose between — patch the owning module.
        m = _migrate_mod()
        prev = m._KB_MIGRATIONS
        self.addCleanup(setattr, m, "_KB_MIGRATIONS", prev)

        def _noop(_k):
            return None
        # restructuring-pending (one above the CLI schema) -> REFUSE, 0 writes.
        m._KB_MIGRATIONS = prev + (
            (prev[-1][0] + 1, "restructuring", "big-move", _noop),)
        self._write_schema(m.KB_SCHEMA_VERSION)
        code, _o, err = run_cli(
            ["prefer", "capture", "always pin deps everywhere", "--global"],
            self.kdir)
        self.assertEqual(code, 1)
        self.assertIn("restructuring migration", err)
        with open(os.path.join(self.kdir, "index.json"), encoding="utf-8") as f:
            self.assertFalse(any("preference" in (e.get("tags") or [])
                                 for e in json.load(f)["entries"]))
        # additive-only pending (cur=0 < schema) -> STILL captures.
        m._KB_MIGRATIONS = prev
        os.remove(os.path.join(self.kdir, ".schema"))
        code, _o, err = run_cli(
            ["prefer", "capture", "always pin deps here now", "--global"],
            self.kdir)
        self.assertEqual(code, 0, err)
        with open(os.path.join(self.kdir, "index.json"), encoding="utf-8") as f:
            self.assertTrue(any("preference" in (e.get("tags") or [])
                                for e in json.load(f)["entries"]))


if __name__ == "__main__":
    unittest.main()
