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
Runner: python3 -m unittest tests.unit.features.test_schemaguard -v
"""

from tests.unit.features._schemaguard_support import (  # noqa: F401
    _EXPECTED_INDEX_WRITER_DEFS, _GROUND_TRUTH_WRITERS,
    _INDEX_WRITER_EXEMPT_COMMANDS, _RETIRED_INTO, _collect_dispatch_funcs,
    _expected_index_writer_defs, _lib_cli, _load, _migrate_mod, argparse,
    build_synthetic_kb, contextlib, io, json, os, run_cli, shutil,
    tempfile, unittest)


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

    def _gate(self, func, mod=None):
        """Run the real dispatch gate for `func` against this KB.

        `mod` selects the ARM (default: the monolith's). Each arm's gate keys on
        the identity of the handlers ITS tree dispatches, so a handler must be
        gated by its own arm — asking the monolith about a ported handler
        correctly returns None, and reading that as "unguarded" would be the
        wrong conclusion.
        """
        mod = mod or self.m
        prev = os.environ.get("AGENTWARE_KNOWLEDGE_DIR")
        os.environ["AGENTWARE_KNOWLEDGE_DIR"] = self.kdir
        try:
            with contextlib.redirect_stderr(io.StringIO()):
                return mod._kb_predispatch_gate(argparse.Namespace(func=func))
        finally:
            if prev is None:
                os.environ.pop("AGENTWARE_KNOWLEDGE_DIR", None)
            else:
                os.environ["AGENTWARE_KNOWLEDGE_DIR"] = prev

    def _arms(self):
        """The dispatch arms serving verbs right now, as (module, writer_funcs).

        The strangler window (feature 260813 tasks 37..56) splits dispatch across
        the monolith and the lib, and each arm's `_kb_writer_funcs()` holds only
        the handlers IT dispatches. The ground truth below is a property of the
        MERGED CLI — "every KB-mutating verb is schema-guarded" — so it is
        asserted against the UNION. Pinning it to one arm would have made the
        first ported writer look like a deleted guard, and would go on hiding a
        genuinely unguarded handler on the other side. At task 56 the monolith
        goes and this collapses to a single arm with no edit here.
        """
        mig = _migrate_mod()
        return ((self.m, self.m._kb_writer_funcs()),
                (mig, mig._kb_writer_funcs()))

    def _registry(self):
        """Union of both arms' writer registries, by function name."""
        return {f.__name__ for _mod, funcs in self._arms() for f in funcs}

    def _autoapply(self):
        """Union of both arms' auto-apply subsets, by function name."""
        return {f.__name__ for mod, _funcs in self._arms()
                for f in mod._kb_autoapply_funcs()}

    # --- completeness -------------------------------------------------------
    def test_schemaguard_registry_equals_ground_truth(self):
        registry = self._registry()
        self.assertEqual(
            registry, _GROUND_TRUTH_WRITERS,
            "registry drifted from the ground-truth writer set: "
            "missing=%s extra=%s" % (
                sorted(_GROUND_TRUTH_WRITERS - registry),
                sorted(registry - _GROUND_TRUTH_WRITERS)))

    def test_schemaguard_completeness_canary(self):
        # Deliberate-omission canary: proves the completeness assertion is not
        # vacuous — dropping the loop's every-iteration writer MUST be detected.
        registry = self._registry()
        self.assertIn("cmd_plan_claim", registry,
                      "cmd_plan_claim runs every loop iteration — the original hole")
        omitted = registry - {"cmd_plan_claim"}
        self.assertNotEqual(omitted, _GROUND_TRUTH_WRITERS,
                            "canary: an omitted writer must break the equality")

    def test_schemaguard_every_registry_func_is_dispatchable(self):
        # Both parsers, for the same reason the registry is a union: a ported
        # handler is registered by the lib's assembler and no longer appears in
        # the monolith's build_parser, so a one-sided comparison would report a
        # correctly-guarded writer as unreachable.
        parser_funcs = set()
        for parser in (self.m.build_parser(), _lib_cli().build_parser()):
            parser_funcs |= {f.__name__
                             for f in _collect_dispatch_funcs(parser, set())}
        registry = self._registry()
        self.assertTrue(registry <= parser_funcs,
                        "registry has non-dispatchable funcs: %s"
                        % sorted(registry - parser_funcs))

    def test_schemaguard_migrate_and_attach_excluded(self):
        registry = self._registry()
        self.assertNotIn("cmd_migrate", registry)
        self.assertNotIn("cmd_attach", registry)

    def test_schemaguard_autoapply_excludes_dualmode_and_runner(self):
        # Auto-apply excludes the migration-runner verb (double-run) AND the
        # dual-mode verbs whose read-only path must never write .schema: cmd_dream
        # (--dry-run) and cmd_prefer (digest/list/scan-queue run at session-start /
        # Stop-hook time). All three still carry version-refusal via the superset.
        self.assertEqual(self._registry() - self._autoapply(),
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
        expected = _expected_index_writer_defs()
        self.assertEqual(
            discovered, set(expected),
            "structured-index writer functions changed — classify the new/removed "
            "one (register its command or add a documented exemption): missing=%s "
            "unexpected=%s" % (
                sorted(set(expected) - discovered),
                sorted(discovered - set(expected))))

        registered = self._registry()
        for wdef, command in expected.items():
            if command is None:
                continue
            self.assertTrue(
                command in registered or command in _INDEX_WRITER_EXEMPT_COMMANDS,
                "index-writer '%s' -> command '%s' is neither schema-guarded nor a "
                "documented exemption" % (wdef, command))

    def test_schemaguard_a_retired_writer_row_MOVED_it_did_not_vanish(self):
        """Every `_RETIRED_INTO` row is a delegation, proven from the source.

        Deleting the monolith removed two names from the discovered set --
        `rebuild_kb` and `_dream_step_kb_pull` -- because the monolith's copies
        performed the index write INLINE while the lib's copies hand it to a
        callee. Updating the expectation to match a smaller discovered set is
        the move that quietly turns this completeness gate into a record of
        whatever the scan last happened to find, so the shrink is justified
        HERE, mechanically, instead of in a comment.

        The obligation is no-capability-loss, the same question task 92's
        `is_terminal()` asks of the ratchet: the write must still happen and
        must still be classified the same way. A row that merely disappeared --
        a writer renamed out of the scan's sight, or one that stopped being
        policed -- cannot satisfy this, because there would be no delegation
        edge to find.
        """
        import ast
        try:
            from tests._fixtures import cli_source
        except ImportError:
            from _fixtures import cli_source
        tree = ast.parse(cli_source())
        bodies = {n.name: n for n in ast.walk(tree)
                  if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))}

        for retired, successor in _RETIRED_INTO.items():
            self.assertNotIn(retired, _EXPECTED_INDEX_WRITER_DEFS,
                             "%s is retired; it must not also be expected"
                             % retired)
            # (1) the retired def still exists -- this is a MOVE, not a removal
            self.assertIn(retired, bodies,
                          "%s is gone entirely: that is a capability question, "
                          "not a decomposition" % retired)
            # (2) ...and still calls the successor it delegated to
            called = {c.func.id for c in ast.walk(bodies[retired])
                      if isinstance(c, ast.Call)
                      and isinstance(c.func, ast.Name)}
            called |= {c.func.attr for c in ast.walk(bodies[retired])
                       if isinstance(c, ast.Call)
                       and isinstance(c.func, ast.Attribute)}
            self.assertIn(successor, called,
                          "%s no longer calls %s -- the write did not move "
                          "there, so retiring its row drops coverage"
                          % (retired, successor))
            # (3) the successor is policed, with the SAME classification
            self.assertIn(successor, _EXPECTED_INDEX_WRITER_DEFS,
                          "%s delegates to %s, which nothing classifies"
                          % (retired, successor))

        # Non-vacuity: the map is not empty and its successors really are the
        # rows the scan discovers, so a future edit cannot satisfy this by
        # emptying `_RETIRED_INTO`.
        self.assertTrue(_RETIRED_INTO)
        self.assertEqual(_EXPECTED_INDEX_WRITER_DEFS["_rebuild_kb_locked"], None,
                         "rebuild_kb was a primitive; its successor must be too")
        self.assertEqual(_EXPECTED_INDEX_WRITER_DEFS["_dream_pull_resolve_derived"],
                         "cmd_dream",
                         "_dream_step_kb_pull was owned by cmd_dream; so is its "
                         "successor")

    # --- newer-.schema refusal for EVERY writer (older CLI on newer KB) ------
    def test_schemaguard_all_writers_refuse_on_newer_kb(self):
        self._write_schema(self.m.KB_SCHEMA_VERSION + 1)
        for mod, funcs in self._arms():
            for func in funcs:
                self.assertEqual(
                    self._gate(func, mod), 1,
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
