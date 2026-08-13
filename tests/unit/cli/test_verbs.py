"""Unit tests for `agentware_lib.verbs` — the per-module verb-discovery contract.

Feature 260813-modular-core-adoption, task 70.

The tree-wide assertions (the retired registry is gone and unreferenced, the
discovered order equals the pinned golden, the manifest and the runtime agree)
live in `tests/gates/test_verb_discovery.py`. This file is about the MECHANISM,
so almost everything here runs against SYNTHETIC declarations rather than the
real package: a validator tested only against the 44 declarations that already
pass it proves nothing about the declaration that will be written next.
"""

from tests._paths import TESTS_DIR  # noqa: F401  (puts the repo root on sys.path)

import os
import shutil
import sys
import tempfile
import unittest

try:
    from tests import _fixtures
except ImportError:  # allow `python3 -m unittest tests.unit.cli.test_verbs`
    sys.path.insert(0, TESTS_DIR)
    import _fixtures

_fixtures.load_cli()
from agentware_lib import verbs  # noqa: E402


def _decl(module, *rows):
    return (module, tuple(rows))


class CollectOrdersRowsTest(unittest.TestCase):
    """`collect` turns declarations into the ordered registration."""

    def test_rows_are_sorted_by_the_order_key_not_the_module_name(self):
        # `zzz` declares the FIRST verb and `aaa` the last, so a walk-order or
        # name-order sort would produce the opposite answer. This is the whole
        # promise of the sparse key: where a module sits in the package tree is
        # an implementation detail and may never move a verb on --help.
        rows = verbs.collect([
            _decl("pkg.zzz", ("alpha", 100, "register_alpha")),
            _decl("pkg.aaa", ("omega", 900, "register_omega")),
        ])
        self.assertEqual([r[0] for r in rows], ["alpha", "omega"])

    def test_a_module_may_declare_several_verbs(self):
        rows = verbs.collect([
            _decl("pkg.two", ("first", 100, "register_first"),
                  ("second", 200, "register_second")),
        ])
        self.assertEqual([r[0] for r in rows], ["first", "second"])
        self.assertEqual({r[2] for r in rows}, {"pkg.two"})

    def test_the_registration_shape_drops_the_order_key(self):
        rows = verbs.collect([_decl("pkg.a", ("solo", 100, "register_solo"))])
        self.assertEqual(rows, (("solo", 100, "pkg.a", "register_solo"),))

    def test_no_declarations_is_an_empty_registration_not_an_error(self):
        self.assertEqual(verbs.collect([]), ())

    def test_collect_is_pure_and_repeatable(self):
        # No cache, no module-level accumulation: the same input gives the same
        # answer however many times it is asked. This is the direct probe for
        # `learn-moving-mutable-module-state-changes-its-lifetime` -- a registry
        # that APPENDS on each populate run would grow here.
        declared = [_decl("pkg.a", ("solo", 100, "register_solo"))]
        self.assertEqual(verbs.collect(declared), verbs.collect(declared))


class DuplicateClaimsAreRefusedTest(unittest.TestCase):
    """The two failures a decentralised registry can have that a list cannot."""

    def test_two_modules_declaring_the_same_verb_fail_loudly(self):
        with self.assertRaises(verbs.VerbDeclarationError) as caught:
            verbs.collect([
                _decl("pkg.first", ("clash", 100, "register_clash")),
                _decl("pkg.second", ("clash", 200, "register_clash")),
            ])
        message = str(caught.exception)
        # BOTH claimants named: "duplicate verb" without the two modules leaves
        # the reader grepping 41 files for the second declaration.
        self.assertIn("pkg.first", message)
        self.assertIn("pkg.second", message)
        self.assertIn("clash", message)

    def test_one_module_declaring_the_same_verb_twice_also_fails(self):
        with self.assertRaises(verbs.VerbDeclarationError):
            verbs.collect([_decl("pkg.a", ("dup", 100, "register_dup"),
                                 ("dup", 200, "register_dup"))])

    def test_two_modules_claiming_the_same_order_key_fail_loudly(self):
        # Not a cosmetic clash: the --help position of both verbs would then be
        # decided by the tie-break, i.e. by the package layout, which is the one
        # thing the order key exists to take out of the answer.
        with self.assertRaises(verbs.VerbDeclarationError) as caught:
            verbs.collect([
                _decl("pkg.first", ("alpha", 500, "register_alpha")),
                _decl("pkg.second", ("beta", 500, "register_beta")),
            ])
        message = str(caught.exception)
        self.assertIn("pkg.first", message)
        self.assertIn("pkg.second", message)
        self.assertIn("500", message)

    def test_a_duplicate_is_refused_even_when_the_rows_are_identical(self):
        with self.assertRaises(verbs.VerbDeclarationError):
            verbs.collect([_decl("pkg.a", ("x", 100, "register_x")),
                           _decl("pkg.b", ("x", 100, "register_x"))])


class MalformedDeclarationsAreRefusedTest(unittest.TestCase):
    """Every field is checked, and the message names the module."""

    def _refuse(self, decl):
        with self.assertRaises(verbs.VerbDeclarationError) as caught:
            verbs.collect([("pkg.bad", decl)])
        self.assertIn("pkg.bad", str(caught.exception))

    def test_a_non_sequence_declaration_is_refused(self):
        self._refuse("init")

    def test_a_row_that_is_not_a_triple_is_refused(self):
        self._refuse((("init", 100),))

    def test_a_non_string_verb_is_refused(self):
        self._refuse(((100, 100, "register_init"),))

    def test_an_uppercase_or_spaced_verb_is_refused(self):
        self._refuse((("Init", 100, "register_init"),))
        self._refuse((("two words", 100, "register_init"),))

    def test_a_dashed_verb_is_accepted(self):
        # `fleet-emit`, `kb-git` and `review-report` are real verbs.
        rows = verbs.collect([("pkg.ok", (("fleet-emit", 100,
                                          "register_fleet_emit"),))])
        self.assertEqual(rows[0][0], "fleet-emit")

    def test_a_non_integer_or_negative_order_is_refused(self):
        self._refuse((("init", "100", "register_init"),))
        self._refuse((("init", 0, "register_init"),))
        self._refuse((("init", -1, "register_init"),))

    def test_a_boolean_order_is_refused(self):
        # `True` is an int in Python and would sort as 1, quietly claiming the
        # first --help position. Excluded explicitly rather than by luck.
        self._refuse((("init", True, "register_init"),))

    def test_a_registrar_not_named_register_something_is_refused(self):
        self._refuse((("init", 100, "setup_init"),))
        self._refuse((("init", 100, ""),))


class ResolveTest(unittest.TestCase):
    """Resolving a declared row to its callable registrar."""

    def test_a_real_row_resolves_to_the_registrar(self):
        self.assertTrue(callable(
            verbs.resolve("whereami", "agentware_lib.cli", "register_whereami")))

    def test_a_missing_registrar_fails_naming_the_verb_and_the_target(self):
        with self.assertRaises(verbs.VerbDeclarationError) as caught:
            verbs.resolve("ghost", "agentware_lib.cli", "register_ghost")
        message = str(caught.exception)
        self.assertIn("ghost", message)
        self.assertIn("agentware_lib.cli", message)

    def test_a_non_callable_attribute_is_refused(self):
        with self.assertRaises(verbs.VerbDeclarationError):
            verbs.resolve("bogus", "agentware_lib.verbs", "register_bogus")


class SyntheticPackageDiscoveryTest(unittest.TestCase):
    """End-to-end discovery over a package built for the test.

    `collect` is pure and easy to drive; the WALK is the half that touches
    `pkgutil` and `importlib`, and testing it only against `agentware_lib` would
    mean the duplicate-verb refusal is never proven on the path the CLI actually
    takes. So this builds a throwaway package on `sys.path` and calls the real
    `discover()` against it.
    """

    package = "awtestverbpkg"

    def setUp(self):
        self.root = tempfile.mkdtemp(prefix="aw-verbs-")
        self.pkg_dir = os.path.join(self.root, self.package)
        os.makedirs(os.path.join(self.pkg_dir, "sub"))
        self._write("__init__.py", "")
        self._write(os.path.join("sub", "__init__.py"), "")
        sys.path.insert(0, self.root)

    def tearDown(self):
        if self.root in sys.path:
            sys.path.remove(self.root)
        for name in [n for n in sys.modules if n.split(".")[0] == self.package]:
            del sys.modules[name]
        shutil.rmtree(self.root, ignore_errors=True)

    def _write(self, rel, body):
        with open(os.path.join(self.pkg_dir, rel), "w", encoding="utf-8") as fh:
            fh.write(body)

    def _module(self, rel, *rows):
        decl = "".join('    ("%s", %d, "%s"),\n' % r for r in rows)
        self._write(rel, "__verbs__ = (\n%s)\n" % decl)

    def test_a_declaration_in_a_subpackage_is_discovered(self):
        self._module(os.path.join("sub", "deep.py"), ("deep", 200, "register_deep"))
        self._module("shallow.py", ("shallow", 100, "register_shallow"))
        rows = verbs.discover(self.package)
        self.assertEqual([r[0] for r in rows], ["shallow", "deep"])
        self.assertEqual(rows[1][2], "%s.sub.deep" % self.package)

    def test_modules_with_no_declaration_contribute_nothing(self):
        self._write("quiet.py", "X = 1\n")
        self._module("loud.py", ("loud", 100, "register_loud"))
        self.assertEqual([r[0] for r in verbs.discover(self.package)], ["loud"])

    def test_two_modules_declaring_the_same_verb_fail_the_real_walk(self):
        self._module("one.py", ("clash", 100, "register_clash"))
        self._module("two.py", ("clash", 900, "register_clash"))
        with self.assertRaises(verbs.VerbDeclarationError) as caught:
            verbs.discover(self.package)
        message = str(caught.exception)
        self.assertIn("%s.one" % self.package, message)
        self.assertIn("%s.two" % self.package, message)

    def test_the_walk_is_sorted_regardless_of_creation_order(self):
        for name in ("zeta.py", "alpha.py", "mid.py"):
            self._write(name, "X = 1\n")
        names = verbs.module_names(self.package)
        self.assertEqual(list(names), sorted(names))

    def test_a_module_that_cannot_import_stops_discovery_loudly(self):
        # The alternative is a verb that silently disappears from --help because
        # its module raised on import and the walk swallowed it.
        self._write("broken.py", "raise RuntimeError('boom')\n")
        with self.assertRaises((verbs.VerbDeclarationError, RuntimeError)):
            verbs.discover(self.package)


class LiveDeclarationsTest(unittest.TestCase):
    """The real package, asserted only where the assertion is an INVARIANT."""

    def test_every_declaring_module_is_inside_the_package(self):
        for _verb, _order, module, _attr in verbs.discover():
            self.assertTrue(module.startswith("agentware_lib."), module)

    def test_every_discovered_row_resolves(self):
        for verb, _order, module, attr in verbs.discover():
            self.assertTrue(callable(verbs.resolve(verb, module, attr)))

    def test_discovery_is_stable_across_calls(self):
        self.assertEqual(verbs.discover(), verbs.discover())


if __name__ == "__main__":
    unittest.main()
