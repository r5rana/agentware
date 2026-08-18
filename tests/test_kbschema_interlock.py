"""The KB SCHEMA INTERLOCK — one authority, two dispatch arms (task 37).

Feature 260813-modular-core-adoption. `migrate` is the FIRST verb the strangler
promotes, and it is first because everything else that writes the knowledge base
is gated on it: the KB stamps its own generation in `<kdir>/.schema`, the package
carries `KB_SCHEMA_VERSION`, and the pair decides whether a write is refused
(older package on a newer KB) or preceded by an auto-applied additive migration.

During the strangler window there are TWO dispatch paths — the monolith's
`_kb_predispatch_gate` and the lib's — because each keys on the IDENTITY of the
handler functions its own tree defines. What there must never be is two RUNNERS:
two schema versions or two migration tables over one file on disk is a split
brain, and the failure is silent (a write refused on one path and admitted on the
other, or a `.schema` stamped from a registry the other arm never saw).

So this module asserts the two halves that no surface gate can see:

  1. AUTHORITY — the monolith reads the version, the registry and the runner from
     `agentware_lib.features.migratecmd` at CALL time. Proven by MUTATION: patch
     the lib module and observe the monolith's arm change behaviour.
  2. CORRESPONDENCE — the lib's writer registry tracks the allowlist. Every
     ported verb that is a KB writer has a resolving handler row, and every row
     names a verb that is actually ported. This is the check that makes "the port
     forgot to register its writer" impossible to land, one port at a time.

Plus the gate's own behaviour on the lib arm, and the wiring that puts it on the
dispatch path at all.

Test names are prefixed `test_interlock_`.

Runner: python3 -m unittest tests.test_kbschema_interlock -v
"""

import argparse
import contextlib
import importlib
import io
import os
import shutil
import tempfile
import unittest
from unittest import mock

try:
    from tests._fixtures import (LEGACY_PATH, build_synthetic_kb, load_cli,
                                 load_lib)
except ImportError:  # allow `python3 -m unittest tests.test_kbschema_interlock`
    from _fixtures import LEGACY_PATH, build_synthetic_kb, load_cli, load_lib

MIGRATE_MOD = "agentware_lib.features.migratecmd"

#: A real, resolvable lib writer handler used as the stand-in target while the
#: allowlist holds only `migrate` (which is deliberately NOT a guarded writer).
#: Chosen because `learn` is a KB writer on both sides and its handler already
#: exists in the lib; it is not ported yet, so nothing routes to it — the gate is
#: exercised through an explicitly substituted registry, never by side effect.
STANDIN_TARGET = ("agentware_lib.features.curationcmd", "cmd_learn")


def _lib(dotted):
    return load_lib(dotted)


def writer_registry_errors(ported, writer_verbs, targets, resolve):
    """Correspondence between the ALLOWLIST and the lib writer registry.

    Pure so the live assertion and its mutation arms run the same code. Returns a
    list of human-readable problems:

      * a ported verb that IS a KB writer with no handler row — the port that
        forgot its guard, which is invisible everywhere else: the verb parses,
        runs and writes, and only an out-of-date clone against a migrated KB ever
        finds out;
      * a row whose verb is not ported — a guard for a handler the router never
        reaches, i.e. a registry drifting ahead of the allowlist;
      * a row that does not resolve — a rename that silently emptied the set.
    """
    problems = []
    ported, writer_verbs = set(ported), set(writer_verbs)
    rows = list(targets)
    by_verb = {}
    for module, attr in rows:
        try:
            func = resolve(module, attr)
        except (ImportError, AttributeError) as exc:
            problems.append("unresolvable writer row %s.%s: %s" % (module, attr, exc))
            continue
        by_verb[_verb_of_handler(attr)] = func
    for verb in sorted(ported & writer_verbs):
        if verb not in by_verb:
            problems.append(
                "ported writer verb %r has no row in _KB_WRITER_TARGETS: its "
                "handler dispatches past the schema guard" % verb)
    for verb in sorted(set(by_verb) - ported):
        problems.append("writer row for %r, which is not in PORTED_VERBS" % verb)
    return problems


def _verb_of_handler(attr):
    """`cmd_index_add` -> `index`; `cmd_learn` -> `learn`. The router's key."""
    stem = attr[len("cmd_"):] if attr.startswith("cmd_") else attr
    return stem.split("_")[0].replace("_", "-")


class InterlockAuthorityTest(unittest.TestCase):
    """Claim 1 — ONE runner, reached by both arms."""

    def setUp(self):
        self.kdir = tempfile.mkdtemp(prefix="agentware-interlock-")
        self.addCleanup(shutil.rmtree, self.kdir, True)
        build_synthetic_kb(self.kdir)
        self.mig = _lib(MIGRATE_MOD)
        self.cli = load_cli()

    def _initialized(self):
        with open(os.path.join(self.kdir, ".initialized"), "w",
                  encoding="utf-8") as fh:
            fh.write("test\n")

    def _env(self):
        prev = os.environ.get("AGENTWARE_KNOWLEDGE_DIR")
        os.environ["AGENTWARE_KNOWLEDGE_DIR"] = self.kdir
        self.addCleanup(
            lambda: os.environ.__setitem__("AGENTWARE_KNOWLEDGE_DIR", prev)
            if prev is not None else os.environ.pop("AGENTWARE_KNOWLEDGE_DIR", None))

    def test_interlock_the_monolith_reaches_the_lib_module_object(self):
        """Not a copy: the SAME object, so a patch on either side is one patch.

        TERMINAL STATE (task 56). `_kbmigrate` is the MONOLITH'S alias for the
        lib module (`import agentware_lib.features.migratecmd as _kbmigrate`),
        so once the monolith is deleted there is no alias and no second arm to
        interlock with -- the lib module is the only authority left, which is
        the end state this whole module exists to protect. Assert THAT rather
        than deleting the coverage.

        Read through `getattr` rather than as an attribute so the preserved-
        surface scanner (`tests/_preserved_scan.scan_cli_symbols`) does not
        record `_kbmigrate` as a frozen CLI symbol. It would be an unsatisfiable
        one: the name binds a SUBMODULE, and `_fixtures._scan_imported_for`
        refuses to resolve module values by name on purpose (binding
        `agentware_lib.retrieval.acr` as `CLI.acr` made 16 tests raise "module
        object is not callable"). A frozen symbol no resolver may ever bind is a
        gate that can only go red.
        """
        alias = getattr(self.cli, "_kbmigrate", None)
        if os.path.isfile(LEGACY_PATH):
            self.assertIs(alias, importlib.import_module(MIGRATE_MOD))
        else:
            self.assertIsNone(alias)

    def test_interlock_both_arms_report_one_schema_version(self):
        self.assertEqual(self.cli.KB_SCHEMA_VERSION, self.mig.KB_SCHEMA_VERSION)
        self.assertIs(self.cli._KB_MIGRATIONS, self.mig._KB_MIGRATIONS)

    def test_interlock_patching_the_lib_registry_moves_the_MONOLITH_arm(self):
        # THE mutation proof. If the monolith held a snapshot instead of reading
        # the module, this passes vacuously — so it is asserted in the direction
        # that can only succeed through the live module attribute.
        self._initialized()
        self._env()
        with open(os.path.join(self.kdir, ".schema"), "w", encoding="utf-8") as fh:
            fh.write("0\n")
        ran = []

        def _mig(kdir):
            ran.append(kdir)

        args = argparse.Namespace(func=self.cli.cmd_learn)
        with mock.patch.object(self.mig, "_KB_MIGRATIONS",
                               ((1, "additive", "synthetic", _mig),)), \
                mock.patch.object(self.mig, "KB_SCHEMA_VERSION", 1):
            rc = self.cli._kb_predispatch_gate(args)
        self.assertIsNone(rc)
        self.assertEqual(ran, [self.kdir],
                         "the monolith's gate did not run the LIB's registry")

    def test_interlock_the_older_cli_refusal_is_driven_by_the_lib_version(self):
        self._initialized()
        self._env()
        with open(os.path.join(self.kdir, ".schema"), "w", encoding="utf-8") as fh:
            fh.write("99\n")
        args = argparse.Namespace(func=self.cli.cmd_learn)
        with contextlib.redirect_stderr(io.StringIO()) as errbuf:
            rc = self.cli._kb_predispatch_gate(args)
        self.assertEqual(rc, 1)
        self.assertIn("older CLI on a newer KB", errbuf.getvalue())
        # ...and raising the LIB's version alone lifts the refusal.
        with mock.patch.object(self.mig, "KB_SCHEMA_VERSION", 99):
            self.assertIsNone(self.cli._kb_predispatch_gate(args))


class LibGateTest(unittest.TestCase):
    """Claim 2 — the modular arm's gate, exercised through a substituted registry."""

    def setUp(self):
        self.kdir = tempfile.mkdtemp(prefix="agentware-interlock-lib-")
        self.addCleanup(shutil.rmtree, self.kdir, True)
        build_synthetic_kb(self.kdir)
        self.mig = _lib(MIGRATE_MOD)
        self.writer = getattr(_lib(STANDIN_TARGET[0]), STANDIN_TARGET[1])
        prev = os.environ.get("AGENTWARE_KNOWLEDGE_DIR")
        os.environ["AGENTWARE_KNOWLEDGE_DIR"] = self.kdir
        self.addCleanup(
            lambda: os.environ.__setitem__("AGENTWARE_KNOWLEDGE_DIR", prev)
            if prev is not None else os.environ.pop("AGENTWARE_KNOWLEDGE_DIR", None))
        with open(os.path.join(self.kdir, ".initialized"), "w",
                  encoding="utf-8") as fh:
            fh.write("test\n")

    @contextlib.contextmanager
    def _registered(self):
        with mock.patch.object(self.mig, "_KB_WRITER_TARGETS",
                               (STANDIN_TARGET,)):
            yield

    def _schema(self, n):
        with open(os.path.join(self.kdir, ".schema"), "w", encoding="utf-8") as fh:
            fh.write("%d\n" % n)

    def test_interlock_lib_gate_refuses_an_older_cli(self):
        self._schema(self.mig.KB_SCHEMA_VERSION + 1)
        args = argparse.Namespace(func=self.writer)
        with self._registered(), contextlib.redirect_stderr(io.StringIO()) as err:
            rc = self.mig._kb_predispatch_gate(args)
        self.assertEqual(rc, 1)
        self.assertIn("older CLI on a newer KB", err.getvalue())

    def test_interlock_lib_gate_autoapplies_for_a_registered_writer(self):
        self._schema(0)
        ran = []
        args = argparse.Namespace(func=self.writer)
        with self._registered(), \
                mock.patch.object(self.mig, "_KB_MIGRATIONS",
                                  ((1, "additive", "synthetic",
                                    lambda kdir: ran.append(kdir)),)), \
                mock.patch.object(self.mig, "KB_SCHEMA_VERSION", 1):
            rc = self.mig._kb_predispatch_gate(args)
        self.assertIsNone(rc)
        self.assertEqual(ran, [self.kdir])
        with open(os.path.join(self.kdir, ".schema"), encoding="utf-8") as fh:
            self.assertEqual(int(fh.read().strip()), 1)

    def test_interlock_lib_gate_is_a_noop_for_an_unregistered_func(self):
        # The reader case, and the still-unported-writer case: both must pass
        # through untouched, and neither may stamp `.schema` on a READ.
        self._schema(0)
        args = argparse.Namespace(func=self.writer)
        # The registry is substituted EMPTY rather than relying on the live one
        # being empty today: this must keep meaning the same thing after the
        # ports that fill it (the assertion-that-must-flip trap).
        with mock.patch.object(self.mig, "_KB_WRITER_TARGETS", ()), \
                mock.patch.object(self.mig, "_KB_MIGRATIONS",
                               ((1, "additive", "never",
                                 lambda _k: self.fail("ran for an unguarded func")),)), \
                mock.patch.object(self.mig, "KB_SCHEMA_VERSION", 1):
            self.assertIsNone(self.mig._kb_predispatch_gate(args))
        with open(os.path.join(self.kdir, ".schema"), encoding="utf-8") as fh:
            self.assertEqual(int(fh.read().strip()), 0)

    def test_interlock_lib_gate_is_a_noop_on_an_uninitialized_kb(self):
        os.remove(os.path.join(self.kdir, ".initialized"))
        self._schema(0)
        args = argparse.Namespace(func=self.writer)
        with self._registered(), \
                mock.patch.object(self.mig, "_KB_MIGRATIONS",
                                  ((1, "additive", "never",
                                    lambda _k: self.fail("migrated during init")),)), \
                mock.patch.object(self.mig, "KB_SCHEMA_VERSION", 1):
            self.assertIsNone(self.mig._kb_predispatch_gate(args))

    def test_interlock_an_unresolvable_writer_row_RAISES(self):
        # "Could not answer" must never read as "not a writer": a renamed handler
        # has to stop the CLI, not drop out of the guarded set.
        with mock.patch.object(self.mig, "_KB_WRITER_TARGETS",
                               ((STANDIN_TARGET[0], "cmd_no_such_handler"),)):
            with self.assertRaises(AttributeError):
                self.mig._kb_writer_funcs()

    def test_interlock_the_gate_is_ON_the_lib_dispatch_path(self):
        # Wiring, not behaviour: a gate nobody calls is the failure this whole
        # file exists to prevent. Proven by short-circuit through the real main().
        libcli = _lib("agentware_lib.cli")
        with mock.patch.object(self.mig, "_kb_predispatch_gate",
                               return_value=42) as gate:
            self.assertEqual(libcli.main(["migrate", "--list"]), 42)
        self.assertEqual(gate.call_count, 1)


class WriterRegistryCorrespondenceTest(unittest.TestCase):
    """Claim 3 — the registry tracks the allowlist, port by port."""

    def setUp(self):
        self.mig = _lib(MIGRATE_MOD)
        self.strangler = _lib("agentware_lib.strangler")

    @staticmethod
    def _resolve(module, attr):
        return getattr(importlib.import_module(module), attr)

    def test_interlock_the_live_registry_matches_the_live_allowlist(self):
        self.assertEqual(
            [], writer_registry_errors(self.strangler.PORTED_VERBS,
                                       self.mig._KB_WRITER_VERBS,
                                       self.mig._KB_WRITER_TARGETS,
                                       self._resolve))

    def test_interlock_a_ported_writer_with_no_row_REDDENS(self):
        problems = writer_registry_errors({"migrate", "learn"},
                                          self.mig._KB_WRITER_VERBS,
                                          (), self._resolve)
        self.assertEqual(1, len(problems), problems)
        self.assertIn("learn", problems[0])

    def test_interlock_a_row_for_an_unported_verb_REDDENS(self):
        problems = writer_registry_errors({"migrate"},
                                          self.mig._KB_WRITER_VERBS,
                                          (STANDIN_TARGET,), self._resolve)
        self.assertEqual(1, len(problems), problems)
        self.assertIn("learn", problems[0])

    def test_interlock_an_unresolvable_row_REDDENS(self):
        problems = writer_registry_errors(
            {"migrate", "learn"}, self.mig._KB_WRITER_VERBS,
            ((STANDIN_TARGET[0], "cmd_gone"),), self._resolve)
        self.assertTrue(any("unresolvable" in p for p in problems), problems)

    def test_interlock_migrate_is_never_a_guarded_writer(self):
        # One func serves `--list` (READ) and `--apply` (WRITE); guarding it would
        # make a read stamp `.schema`. It self-guards instead.
        self.assertNotIn("migrate", self.mig._KB_WRITER_VERBS)
        self.assertNotIn(self.mig.cmd_migrate, self.mig._kb_writer_funcs())

    def test_interlock_the_writer_verb_contract_covers_the_monoliths_set(self):
        # The contract half: the verbs the monolith guards today must all be
        # named, so a later port cannot quietly narrow the obligation by
        # promoting a writer this table never claimed.
        cli = load_cli()
        monolith = {_verb_of_handler(f.__name__) for f in cli._kb_writer_funcs()}
        self.assertTrue(
            monolith <= set(self.mig._KB_WRITER_VERBS),
            "unclaimed writer verbs: %s"
            % sorted(monolith - set(self.mig._KB_WRITER_VERBS)))


if __name__ == "__main__":
    unittest.main()
