"""The verb registry is RETIRED, and discovery is complete (task 70).

Adding a verb must touch exactly one file. That is only true if three things
hold, and each is asserted here rather than described:

  * the single-file registry (`scripts/agentware_lib/_registration.py`) is GONE
    and nothing in the tree still reaches for it — asserted from the AST, so a
    historical mention inside a rationale string stays legal and an IMPORT does
    not;
  * the DISCOVERED verb surface is byte-identical to the pinned golden order,
    which since task 70 is the only place the whole ordered surface is written
    down in one piece;
  * the generated manifest and the running package agree about which module
    declares which verb.

That last pairing is the completeness gate, and it is worth being explicit about
why it has teeth. `catalog/modules.json` records each module's `__verbs__` by
READING the file (`tests/_manifest_gen.declared_verbs`, pure AST, no import).
`agentware_lib.verbs` finds the same declarations by IMPORTING the package. The
two answers can only differ in ways that matter: a declaration built at runtime
instead of written down (the walk sees it, the manifest cannot), a module that
fails to import or that the walk never reaches (the manifest sees it, the walk
cannot), or a stale committed manifest. A single-source check would catch none
of those, because it would be asking one witness to confirm itself.

Stdlib only, hermetic, deterministic.
"""

from tests._paths import TESTS_DIR

import ast
import os
import sys
import unittest

try:
    from tests import _corpus, _fixtures, _manifest_gen
except ImportError:  # allow `python3 -m unittest tests.gates.test_verb_discovery`
    sys.path.insert(0, TESTS_DIR)
    import _corpus
    import _fixtures
    import _manifest_gen

from tests.gates.test_parser_golden import EXPECTED_VERBS

_fixtures.load_cli()
from agentware_lib import verbs  # noqa: E402

REPO_ROOT = _corpus.REPO_ROOT

#: The file task 70 deleted. Named by path so the assertion keeps meaning
#: something after the symbol stops existing anywhere else.
RETIRED_REGISTRY = "scripts/agentware_lib/_registration.py"
RETIRED_MODULE = "_registration"
RETIRED_SYMBOL = "_PARSER_REGISTRATION"


def _python_corpus():
    """Every committed Python file the corpus claims, as absolute paths."""
    return [os.path.join(REPO_ROOT, rel)
            for rel in _corpus.corpus_files(REPO_ROOT) if rel.endswith(".py")]


def _tree(path):
    with open(path, encoding="utf-8") as fh:
        return ast.parse(fh.read())


class TheRegistryIsRetiredTest(unittest.TestCase):
    """The file is gone, and no code path still reaches for it."""

    def test_the_registry_file_is_absent(self):
        self.assertFalse(
            os.path.exists(os.path.join(REPO_ROOT, RETIRED_REGISTRY)),
            "%s is back. A verb is declared in its own module's `__verbs__`; a "
            "second, central list is the monolith this task removed."
            % RETIRED_REGISTRY)

    def test_no_module_is_claimed_by_the_manifest_at_that_path(self):
        manifest = _manifest_gen.load_committed()
        paths = {entry["path"] for entry in manifest["modules"]}
        paths |= {entry["path"] for entry in manifest["files"]}
        self.assertNotIn(RETIRED_REGISTRY, paths)

    def test_nothing_imports_the_retired_registry(self):
        """An IMPORT of it, read from the AST — not a mention of its name.

        Deliberately not a grep: the constant differ's and the body differ's
        adjudication rationales quote `_PARSER_REGISTRATION` to explain what was
        retired and why, and that history is worth keeping. What must not exist
        is a live reference, so the check reads import statements and name
        loads, where a string literal cannot reach.
        """
        offenders = []
        for path in _python_corpus():
            for node in ast.walk(_tree(path)):
                if isinstance(node, ast.Import):
                    names = [a.name for a in node.names]
                elif isinstance(node, ast.ImportFrom):
                    names = [node.module or ""] + [a.name for a in node.names]
                else:
                    continue
                if any(RETIRED_MODULE in (n or "").split(".") for n in names):
                    offenders.append(os.path.relpath(path, REPO_ROOT))
        self.assertEqual(sorted(set(offenders)), [])

    def test_the_retired_symbol_is_not_read_as_a_name_anywhere(self):
        offenders = []
        for path in _python_corpus():
            for node in ast.walk(_tree(path)):
                named = (isinstance(node, ast.Name) and node.id == RETIRED_SYMBOL)
                attred = (isinstance(node, ast.Attribute)
                          and node.attr == RETIRED_SYMBOL)
                if named or attred:
                    offenders.append(os.path.relpath(path, REPO_ROOT))
        self.assertEqual(sorted(set(offenders)), [])

    def test_the_merge_union_policy_no_longer_names_the_registry(self):
        """The merge driver existed BECAUSE every lane edited that one file.

        With the registry gone, two lanes adding two verbs edit two different
        modules, so leaving the path in the union list would install a merge
        policy for a file that cannot exist — and would quietly document a
        centralised registry that is no longer there.
        """
        from agentware_lib.features import fanout
        self.assertNotIn(RETIRED_REGISTRY, fanout.FANOUT_MERGE_UNION_PATHS)


class DiscoveryMatchesThePinnedGoldenTest(unittest.TestCase):
    """The discovered order IS the --help order, and it has not moved."""

    def setUp(self):
        self.rows = verbs.discover()

    def test_the_discovered_verb_order_is_the_pinned_golden(self):
        self.assertEqual([row[0] for row in self.rows], EXPECTED_VERBS)

    def test_every_declared_registrar_resolves_to_a_callable(self):
        broken = []
        for verb, _order, module, attr in self.rows:
            try:
                verbs.resolve(verb, module, attr)
            except verbs.VerbDeclarationError as exc:
                broken.append(str(exc))
        self.assertEqual(broken, [])

    def test_order_keys_are_unique_and_ascending(self):
        keys = [row[1] for row in self.rows]
        self.assertEqual(keys, sorted(keys))
        self.assertEqual(len(set(keys)), len(keys))

    def test_the_order_keys_are_sparse(self):
        """Gaps between neighbours are what make an insert a one-file change.

        Asserted as a PROPERTY (every gap leaves room) rather than as the
        current step: issuing 100s is a convention, leaving room is the
        contract. A dense run would force a renumbering of every later verb,
        which is the single-shared-edit problem this task removed, respread
        across forty files.
        """
        keys = [row[1] for row in self.rows]
        tight = [(a, b) for a, b in zip(keys, keys[1:]) if b - a < 2]
        self.assertEqual(tight, [],
                         "no room to insert a verb between these order keys")

    def test_the_registrar_name_matches_its_verb(self):
        for verb, _order, _module, attr in self.rows:
            self.assertEqual(attr, "register_%s" % verb.replace("-", "_"))

    def test_the_declaration_lives_in_the_module_that_defines_the_registrar(self):
        """One file per verb means the DECLARING file also holds the `def`.

        Declaring a verb in a package aggregator that merely re-exports its
        registrar would still work and would still be wrong: the reader who
        opens the declaring module to see what the verb does would find an
        import list.
        """
        misplaced = []
        for verb, _order, module, attr in self.rows:
            path = os.path.join(REPO_ROOT, "scripts", *module.split(".")) + ".py"
            defined = any(isinstance(node, ast.FunctionDef) and node.name == attr
                          for node in _tree(path).body)
            if not defined:
                misplaced.append("%s: %s not defined in %s" % (verb, attr, module))
        self.assertEqual(misplaced, [])


class TheManifestIsTheCompletenessGateTest(unittest.TestCase):
    """The committed manifest and the running package agree, exactly."""

    def setUp(self):
        self.manifest = _manifest_gen.load_committed()
        self.rows = verbs.discover()

    def _manifest_rows(self):
        out = []
        for entry in self.manifest["modules"]:
            for row in entry.get("verbs", []):
                out.append((row["verb"], row["order"], entry["name"], row["attr"]))
        return sorted(out, key=lambda r: r[1])

    def test_both_halves_agree_on_the_declaration_attribute(self):
        # The manifest reads the AST and cannot import the package, so it
        # carries its own copy of the attribute name. Two copies that can
        # disagree are a bug waiting; this is the assertion that they cannot.
        self.assertEqual(_manifest_gen.VERB_DECLARATION, verbs.DECLARATION)

    def test_the_manifest_claims_every_discovered_verb(self):
        self.assertEqual(self._manifest_rows(), list(self.rows))

    def test_the_manifest_claims_the_module_that_declares_each_verb(self):
        claimed = {entry["name"] for entry in self.manifest["modules"]
                   if entry.get("verbs")}
        self.assertEqual(claimed, {row[2] for row in self.rows})

    def test_no_module_on_disk_declares_a_verb_the_manifest_missed(self):
        """The AST half, run FRESH — the committed manifest could be stale.

        `load_committed` reads the file; this walks the tree. A verb added to a
        module without regenerating the manifest is caught here rather than
        surviving until someone happens to regenerate.
        """
        fresh = []
        for path in _manifest_gen.domain_modules():
            for row in _manifest_gen.declared_verbs(path):
                fresh.append((row["verb"], row["order"],
                              _manifest_gen._dotted(path), row["attr"]))
        self.assertEqual(sorted(fresh, key=lambda r: r[1]), list(self.rows))

    def test_the_ast_half_is_not_vacuously_empty(self):
        # Every assertion above compares two sets. If the extractor silently
        # returned nothing, they would agree only if discovery were also empty
        # -- but a broken extractor plus a broken walk is exactly the pair a
        # self-comparing gate cannot see. Pin the count independently.
        total = sum(len(_manifest_gen.declared_verbs(p))
                    for p in _manifest_gen.domain_modules())
        self.assertEqual(total, len(EXPECTED_VERBS))


if __name__ == "__main__":
    unittest.main()
