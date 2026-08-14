#!/usr/bin/env python3
"""GOLDEN MASTER over the rendered ``--help`` corpus + the registration contract.

Feature 260813-modular-core-adoption, task 60.

Subject: the ``tests/_help_golden.py`` harness and a tree-wide invariant (every
reachable parser surface), which is what puts this file in ``tests/gates/``.

What it closes
--------------
The migration's other surface gates are all deliberately prose-blind. The
object dump (``_surface_capture.py``) records ``required``/``nargs``/
``choices``/``default`` and never reads ``help=``; the parity manifest compares
capability ROWS; the mutation canary proves ONE patched symbol is observable.
So until this file existed, the entire rendered help surface -- 125 surfaces
and every ``help=`` string on them -- could empty out, contradict its handler,
or keep documenting a deleted flag without a single test noticing.

Frozen names, deliberately
--------------------------
``HelpGoldenMaster`` and ``ParserRegistrationContract``, and nine of the method
names below, are the DONOR's node ids (``tests.test_parser_golden.*`` in
``preserved-surface.json``). This file is where they land, so the names are
carried across verbatim and the move is recorded in
``_test_layout.RELOCATIONS``; renaming any of them would read to the freeze as
nine deleted tests, which is exactly what the ledger exists to prevent. The
additional methods are local extensions and are free to be named for what they
assert.

Version handling, stated rather than assumed
--------------------------------------------
Rendered help is interpreter-dependent (argparse changed both its section
header and its "option that takes a value" rendering across recent versions)
and width-dependent (88 of the 125 surfaces re-wrap at a different
``$COLUMNS``). That is precisely why the donor tree's eighty goldens fail here
and why they are regenerated once, on this operator's interpreter, with
``COLUMNS`` pinned.

The BYTE comparison therefore runs on the interpreter recorded in the corpus's
own ``_meta.json`` and says so explicitly on any other, instead of failing an
operator for running a different Python. Every STRUCTURAL assertion here --
the surface set, the surface count, the display order, the registrar and
dispatch contracts, and the non-vacuity proofs -- is version-independent and
runs everywhere, so a foreign interpreter still catches a lost, added or
reordered verb.

Regenerate with:  python3 tests/_help_golden.py --write
"""

import argparse
import importlib
import json
import os
import shutil
import tempfile
import unittest

from tests._paths import TESTS_DIR
from tests import _help_golden as HG

FROZEN_SURFACE_FIXTURE = os.path.join(TESTS_DIR, "fixtures",
                                      "preserved-surface.json")

#: The 45 top-level verbs in EXACT ``--help`` display order.
#:
#: An INDEPENDENT pin, deliberately NOT derived from the discovered verb rows:
#: the parser is BUILT from those, so a self-consistency check would move on both
#: sides in lockstep and certify its own regression. Since task 70 the rows come
#: from 41 separate ``__verbs__`` declarations rather than one frozen tuple,
#: which makes this pin MORE load-bearing, not less: it is now the only place the
#: whole ordered surface is written down in one piece, so a declaration silently
#: dropped, renamed or re-keyed in any of those 41 files lands here as a diff.
#: Adding a real verb is a genuine ``--help`` change -- update this list AND
#: regenerate the corpus.
EXPECTED_VERBS = [
    "init", "config", "profile", "attach", "index", "skill", "update",
    "query", "learn", "decide", "relate", "ingest", "features",
    "migrate", "worklog", "work", "audit", "recall", "mcp", "eval",
    "bench", "metrics", "fleet", "fleet-emit", "team", "dashboard",
    "dream", "kb-git", "steering", "org", "templates", "gate",
    "whereami", "plan", "fanout", "trust", "release", "tick", "checkup",
    "prefer", "review", "remediate", "review-report", "graph", "scaffold",
]

#: 1 root + 45 verbs + 82 sub-verbs. Pinned mechanically so a lost sub-verb
#: cannot diff clean by also losing its fixture. Task 73 added `scaffold` and its
#: two sub-verbs (`new`, `check`) -- the first verb this tree has gained since the
#: merge, and the reason `scaffold new` REPORTS this pin as a step for the author
#: rather than editing it: a generator that moved its own pin would certify its
#: own surface change.
EXPECTED_SURFACES = 128

#: Every leaf parser must dispatch into the decomposed package. The monolith is
#: gone (task 96) and the facade is a bootstrap plus a dispatch tail, so a
#: handler resolving anywhere else is a routing regression.
OWNING_PACKAGE = "agentware_lib"


def _fresh_corpus(dest):
    """Write a corpus from the LIVE parser into ``dest`` and return surfaces."""
    surfaces = HG.capture()
    HG.write(dest=dest, surfaces=surfaces)
    return surfaces


def _all_parsers(parser, parts=()):
    """Yield ``(verb_chain, parser)`` for the root and EVERY subparser."""
    yield tuple(parts), parser
    for act in parser._actions:
        if isinstance(act, argparse._SubParsersAction):
            for name, sub in act.choices.items():
                for res in _all_parsers(sub, tuple(parts) + (name,)):
                    yield res


def _action_surface_counts(parser):
    """``id(action) -> number of surfaces that action appears on``.

    argparse's ``parents=`` mechanism REUSES the parent's ``Action`` objects
    rather than copying them, so the shared ``--format`` action is literally
    the same object on ~95 parsers. A probe that mutates one without knowing
    that measures the wrong thing.
    """
    counts = {}
    for _parts, sub in _all_parsers(parser):
        for act in sub._actions:
            counts[id(act)] = counts.get(id(act), 0) + 1
    return counts


def _mutate_one_single_surface_help(parser):
    """Append a probe token to a help string owned by exactly ONE surface.

    Returns the surface key, or ``None`` when no such action exists.
    """
    counts = _action_surface_counts(parser)
    for parts, leaf in HG.leaf_parsers(parser):
        for act in leaf._actions:
            if (act.help and act.option_strings != ["-h", "--help"]
                    and counts[id(act)] == 1):
                act.help = act.help + " ZZ-MUTATION-PROBE"
                return "_".join(parts)
    return None


class HelpGoldenMaster(unittest.TestCase):
    """The committed corpus and the live parser agree.

    Class name frozen: it carries four donor node ids.
    """

    @classmethod
    def setUpClass(cls):
        cls.surfaces = HG.capture()
        cls.meta = HG.read_meta()

    def _skip_unless_capture_interpreter(self):
        """Skip ONLY the byte comparison, and only off the recorded version."""
        tag = (self.meta or {}).get("python_tag")
        if tag and tag != HG.interpreter_tag():
            self.skipTest(
                "corpus captured on python %s; running %s. Rendered --help is "
                "interpreter-dependent, so the BYTE compare is scoped to the "
                "capture interpreter. Structural pins still ran. Regenerate "
                "with `python3 tests/_help_golden.py --write`."
                % ((self.meta or {}).get("python"), HG.platform_version()))

    # ---- frozen donor node ids ------------------------------------------

    def test_golden_dir_exists(self):
        self.assertTrue(
            os.path.isdir(HG.GOLDEN_DIR),
            "help golden corpus missing; regenerate with "
            "`python3 tests/_help_golden.py --write`")

    def test_surface_set_matches_fixtures_exactly(self):
        live = set(self.surfaces)
        fixtures = HG.golden_keys()
        self.assertEqual(
            live, fixtures,
            "help surface set drifted from the corpus "
            "(fixture_without_surface=%s, unpinned_new_surface=%s)"
            % (sorted(fixtures - live), sorted(live - fixtures)))

    def test_all_help_surfaces_byte_identical_to_golden(self):
        self.assertEqual(
            len(self.surfaces), EXPECTED_SURFACES,
            "expected %d help surfaces (1 root + %d verbs + sub-verbs); got %d"
            % (EXPECTED_SURFACES, len(EXPECTED_VERBS), len(self.surfaces)))
        self._skip_unless_capture_interpreter()
        self.assertEqual(
            HG.compare(surfaces=self.surfaces), [],
            "rendered --help drifted from the committed corpus; if the change "
            "is intended, regenerate with `python3 tests/_help_golden.py "
            "--write` and review the diff")

    def test_golden_master_is_non_vacuous_on_help_change(self):
        """Mutate ONE ``add_argument`` help string and confirm the rendered
        surface stops matching. The first assertion is interpreter-independent
        (mutated vs unmutated LIVE render), so the probe keeps its teeth on any
        Python; the second is the donor's original claim against the committed
        fixture and is scoped to the capture interpreter for the same reason
        the byte compare above is."""
        parser = HG.build_parser()
        target = _mutate_one_single_surface_help(parser)
        self.assertIsNotNone(target, "found no single-surface help to mutate")
        mutated = HG.capture(parser=parser)
        self.assertNotEqual(
            mutated[target], self.surfaces[target],
            "mutating an add_argument help string did not change the rendered "
            "surface %r -- the golden master would be VACUOUS" % target)
        self._skip_unless_capture_interpreter()
        self.assertNotEqual(mutated[target], HG.read_golden(target),
                            "mutated surface %r still matched its golden"
                            % target)

    # ---- local extensions ------------------------------------------------

    def test_meta_is_absent_from_the_surface_enumeration(self):
        """``_meta.json`` is provenance, not a surface, and must never be read
        as one -- otherwise the set comparison reports a phantom key."""
        self.assertTrue(os.path.isfile(HG.META_PATH))
        self.assertNotIn("_meta", HG.golden_keys())

    def test_meta_records_the_capturing_interpreter_and_width(self):
        self.assertIsNotNone(
            self.meta, "corpus has no _meta.json; the byte compare would then "
                       "silently apply to every interpreter")
        self.assertEqual(self.meta["columns"], HG.COLUMNS)
        self.assertEqual(self.meta["surfaces"], len(HG.golden_keys()))
        self.assertTrue(self.meta["python"].startswith(
            self.meta["python_tag"] + "."),
            "recorded python %r is not on the recorded tag %r"
            % (self.meta["python"], self.meta["python_tag"]))

    def test_every_frozen_donor_surface_is_present(self):
        """The migration's contract is a strict SUPERSET of both trees, and the
        asset ratchet only checks that the DIRECTORY exists -- which a corpus
        holding one file would satisfy. This asserts what that row is really a
        proxy for: each of the donor's frozen 80 surfaces has a live fixture.
        It is the only check that would catch regenerating the corpus from a
        tree that had silently dropped a donor verb."""
        with open(FROZEN_SURFACE_FIXTURE, encoding="utf-8") as fh:
            frozen = json.load(fh)
        family = frozen["fixture_families"]["help_golden"]
        self.assertEqual(family["path"], "tests/fixtures/help_golden")
        members = set(m[:-4] for m in family["members"] if m.endswith(".txt"))
        self.assertEqual(len(members), 80, "frozen donor member list changed")
        self.assertEqual(
            sorted(members - HG.golden_keys()), [],
            "donor help surface(s) frozen in preserved-surface.json have no "
            "fixture in the regenerated corpus -- a capability was lost")
        self.assertNotIn(
            "fixture_family:help_golden",
            frozen["assets"]["pending_in_destination"],
            "the corpus exists, so its pending-asset row must be dropped")

    def test_a_second_regeneration_produces_no_diff(self):
        """The acceptance criterion, proven rather than asserted: capture is a
        pure function of the parser, so writing twice changes nothing, and a
        corpus written now equals the one committed."""
        tmp = tempfile.mkdtemp(prefix="aw-help-golden-")
        try:
            _fresh_corpus(tmp)
            first = {k: HG.read_golden(k, tmp) for k in HG.golden_keys(tmp)}
            HG.write(dest=tmp)
            second = {k: HG.read_golden(k, tmp) for k in HG.golden_keys(tmp)}
            self.assertEqual(first, second,
                             "regenerating twice changed the corpus -- the "
                             "capture is not deterministic")
            self._skip_unless_capture_interpreter()
            self.assertEqual(
                first, {k: HG.read_golden(k) for k in HG.golden_keys()},
                "a freshly regenerated corpus differs from the committed one")
        finally:
            shutil.rmtree(tmp, ignore_errors=True)


class HelpGoldenNonVacuity(unittest.TestCase):
    """Prove the comparison BITES. Every check here writes its own throwaway
    corpus from the live parser, so it holds on any interpreter."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="aw-help-golden-probe-")
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self.surfaces = _fresh_corpus(self.tmp)

    def test_baseline_is_clean(self):
        self.assertEqual(HG.compare(dest=self.tmp, surfaces=self.surfaces), [])

    def test_a_mutated_help_string_drifts_exactly_its_own_surface(self):
        parser = HG.build_parser()
        target = _mutate_one_single_surface_help(parser)
        self.assertIsNotNone(target)
        diffs = HG.compare(dest=self.tmp, surfaces=HG.capture(parser=parser))
        self.assertEqual(
            [d.split(":")[0] for d in diffs], [target],
            "expected exactly surface %r to be reported drifted; got %s"
            % (target, diffs))

    def test_a_shared_parent_action_drifts_every_inheriting_surface(self):
        """``parents=[fmt_parent]`` REUSES the parent's ``Action`` objects, so
        the shared ``--format`` action is ONE object on ~95 parsers. Editing
        its help is therefore a many-surface ``--help`` change, not a local
        one -- pinned here so a future reader does not mistake the resulting
        95-file regeneration diff for corpus rot."""
        parser = HG.build_parser()
        counts = _action_surface_counts(parser)
        biggest = max(counts.values())
        self.assertGreater(biggest, 1, "expected a shared parent action")
        target_id = [sid for sid, n in counts.items() if n == biggest][0]
        hit = None
        for _parts, sub in _all_parsers(parser):
            for act in sub._actions:
                if id(act) == target_id:
                    hit = act
                    break
            if hit is not None:
                break
        hit.help = (hit.help or "") + " ZZ-SHARED-PROBE"
        drifted = [d.split(":")[0] for d in
                   HG.compare(dest=self.tmp, surfaces=HG.capture(parser=parser))]
        self.assertEqual(
            len(drifted), biggest,
            "a shared action on %d surfaces drifted %d of them"
            % (biggest, len(drifted)))

    def test_a_removed_surface_is_reported(self):
        shrunk = dict(self.surfaces)
        victim = sorted(k for k in shrunk if k != HG.ROOT_KEY)[0]
        del shrunk[victim]
        diffs = HG.compare(dest=self.tmp, surfaces=shrunk)
        self.assertEqual([d.split(":")[0] for d in diffs], [victim])
        self.assertIn("no live surface", diffs[0])

    def test_an_unpinned_new_surface_is_reported(self):
        grown = dict(self.surfaces)
        grown["zz-brand-new-verb"] = "usage: agentware zz-brand-new-verb\n"
        diffs = HG.compare(dest=self.tmp, surfaces=grown)
        self.assertEqual([d.split(":")[0] for d in diffs], ["zz-brand-new-verb"])
        self.assertIn("NO GOLDEN FIXTURE", diffs[0])

    def test_a_stale_fixture_is_pruned_by_a_rewrite(self):
        stale = os.path.join(self.tmp, "zz-retired-verb.txt")
        with open(stale, "w", encoding="utf-8") as fh:
            fh.write("usage: gone\n")
        _written, pruned = HG.write(dest=self.tmp, surfaces=self.surfaces)
        self.assertEqual(pruned, ["zz-retired-verb.txt"])
        self.assertFalse(os.path.exists(stale))

    def test_the_columns_pin_is_load_bearing(self):
        """A corpus captured at the operator's own terminal width would fail
        for everyone else, so prove the pin actually changes the bytes."""
        prev = os.environ.get("COLUMNS")
        os.environ["COLUMNS"] = "200"
        try:
            wide = HG.walk_help(HG.build_parser())
            rewrapped = [k for k in wide if wide[k] != self.surfaces[k]]
            self.assertTrue(
                rewrapped,
                "no surface re-wrapped at COLUMNS=200 -- the pin in "
                "_help_golden.capture() would then be untested boilerplate")
            self.assertEqual(HG.capture(), self.surfaces,
                             "capture() did not pin COLUMNS")
            self.assertEqual(os.environ.get("COLUMNS"), "200",
                             "capture() leaked COLUMNS into the caller")
        finally:
            if prev is None:
                os.environ.pop("COLUMNS", None)
            else:
                os.environ["COLUMNS"] = prev

    def test_the_corpus_carries_no_environment_state(self):
        """A golden that embeds a resolved path or a knowledge dir would leak
        the capturing operator's machine into a committed fixture and fail on
        every other clone."""
        polluted = {
            "AGENTWARE_KNOWLEDGE_DIR": os.path.join(self.tmp, "kb"),
            "AGENTWARE_ORG_ROOT": os.path.join(self.tmp, "org"),
            "AGENTWARE_TEAM": "zz-probe-team",
            "AGENTWARE_CONFIG_HOME": os.path.join(self.tmp, "cfg"),
            "HOME": self.tmp,
        }
        saved = {k: os.environ.get(k) for k in polluted}
        os.environ.update(polluted)
        try:
            self.assertEqual(
                HG.capture(), self.surfaces,
                "rendered --help changed with AGENTWARE_*/HOME set -- the "
                "corpus is not environment-independent")
        finally:
            for key, val in saved.items():
                if val is None:
                    os.environ.pop(key, None)
                else:
                    os.environ[key] = val


class HelpGoldenVersionScope(unittest.TestCase):
    """The interpreter scoping is a live BRANCH, so prove both of its arms.

    A skip that fires too eagerly would silently disarm the byte compare, and
    that failure looks exactly like a passing suite. So: the foreign arm must
    skip, the matching arm must NOT, and -- the one that keeps the whole design
    honest -- the committed corpus must be on THIS interpreter, so the skip is
    dead code here and the byte compare genuinely ran.
    """

    @staticmethod
    def _case_with_meta(tag):
        case = HelpGoldenMaster("test_golden_dir_exists")
        case.meta = {"python_tag": tag, "python": tag + ".0"}
        return case

    def test_the_byte_compare_skips_on_a_foreign_interpreter(self):
        with self.assertRaises(unittest.SkipTest) as ctx:
            self._case_with_meta("2.0")._skip_unless_capture_interpreter()
        self.assertIn("BYTE compare is scoped", str(ctx.exception))
        self.assertIn("_help_golden.py --write", str(ctx.exception))

    def test_the_byte_compare_runs_on_the_recorded_interpreter(self):
        self._case_with_meta(
            HG.interpreter_tag())._skip_unless_capture_interpreter()

    def test_the_committed_corpus_is_on_this_interpreter(self):
        self.assertEqual(
            HG.read_meta()["python_tag"], HG.interpreter_tag(),
            "the committed corpus was captured on a different interpreter than "
            "the one running, so every byte assertion in this file SKIPPED. "
            "Regenerate with `python3 tests/_help_golden.py --write`.")


class ParserRegistrationContract(unittest.TestCase):
    """The DISCOVERED verb registration and the display-order pin.

    Class name frozen: it carries five donor node ids. What it reads changed at
    task 70 -- ``agentware_lib.verbs.registration()`` walking the package
    replaced the frozen ``_PARSER_REGISTRATION`` tuple -- but what it ASSERTS did
    not: the ordered ``(verb, module, attr)`` surface still has to equal
    ``EXPECTED_VERBS`` and still has to be what ``build_parser()`` produces.
    """

    @classmethod
    def setUpClass(cls):
        cls.parser = HG.build_parser()
        cls.verbs = HG.top_level_verbs(cls.parser)
        from agentware_lib import verbs as verbmod
        cls.registration = verbmod.registration()

    # ---- frozen donor node ids ------------------------------------------

    def test_registration_matches_pinned_display_order(self):
        self.assertEqual(
            [v for (v, _m, _a) in self.registration], EXPECTED_VERBS,
            "the DISCOVERED verb order drifted from the pinned %d-verb display "
            "order (a dropped, added, re-keyed or reordered `__verbs__` row). If "
            "a verb was legitimately added, update EXPECTED_VERBS AND regenerate "
            "the corpus -- a new verb IS a --help change." % len(EXPECTED_VERBS))

    def test_build_parser_top_level_order_matches_registration(self):
        self.assertEqual(self.verbs,
                         [v for (v, _m, _a) in self.registration],
                         "build_parser() top-level --help order != discovered "
                         "order")

    def test_every_registered_verb_resolves_to_callable_registrar(self):
        """Every ``(verb, module, attr)`` row names a real callable in a real
        module, and the verb it claims is actually exposed. A row pointing at a
        renamed registrar would otherwise only surface as an import error at
        CLI start-up."""
        choices = set(self.verbs)
        broken = []
        for verb, module, attr in self.registration:
            if verb not in choices:
                broken.append("%s: in the tuple but not exposed by "
                              "build_parser()" % verb)
                continue
            self.assertIsNotNone(module,
                                 "verb %r has no owning module; the monolith "
                                 "is gone, so None is unreachable" % verb)
            try:
                mod = importlib.import_module(module)
            except ImportError as exc:
                broken.append("%s: cannot import %s (%s)" % (verb, module, exc))
                continue
            if not callable(getattr(mod, attr, None)):
                broken.append("%s: %s.%s missing or not callable"
                              % (verb, module, attr))
        self.assertEqual(broken, [], "broken registration row(s):\n"
                                     + "\n".join(broken))

    def test_every_leaf_subparser_dispatches_to_owning_handler(self):
        broken = []
        leaves = 0
        for parts, leaf in HG.leaf_parsers(self.parser):
            leaves += 1
            func = leaf.get_default("func")
            surface = " ".join(parts)
            if not callable(func):
                broken.append("%s -> no callable func" % surface)
                continue
            owner = getattr(func, "__module__", "")
            if owner != OWNING_PACKAGE and not owner.startswith(
                    OWNING_PACKAGE + "."):
                broken.append("%s -> handler %s.%s is outside %s"
                              % (surface, owner,
                                 getattr(func, "__name__", func),
                                 OWNING_PACKAGE))
        self.assertEqual(broken, [],
                         "leaf subparser(s) with a broken dispatch:\n"
                         + "\n".join(broken))
        self.assertGreaterEqual(leaves, len(EXPECTED_VERBS),
                                "leaf walk found fewer leaves than verbs")

    def test_contract_ratchet_detects_dropped_or_reordered_verb(self):
        """Non-vacuity: dropping OR reordering any entry breaks the pin."""
        self.assertNotEqual(self.verbs[:-1], EXPECTED_VERBS,
                            "dropping a verb must break the pin")
        swapped = list(self.verbs)
        swapped[0], swapped[1] = swapped[1], swapped[0]
        self.assertNotEqual(swapped, EXPECTED_VERBS,
                            "reordering verbs must break the pin")

    # ---- local extension --------------------------------------------------

    def test_build_parser_matches_the_independent_verb_pin(self):
        """The transitive close of the two frozen checks above, asserted
        directly: it is the rendered ``--help`` order that is the contract, and
        the tuple is only the mechanism that produces it."""
        self.assertEqual(self.verbs, EXPECTED_VERBS)


if __name__ == "__main__":
    unittest.main()
