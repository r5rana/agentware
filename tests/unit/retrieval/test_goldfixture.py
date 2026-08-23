"""Owning unit file for `agentware_lib.retrieval.goldfixture` — task 99 wave 10.

The module had no owning file before this one. Its loader was a 20-branch
validator whose only witness was `evaluate_gold_fixture`, which observes a
SCORED RESULT and none of the fourteen refusals that produce it — so thirteen of
them were asserted nowhere, while the one an operator can actually trigger from
the CLI (a missing file) was covered by an integration row.

What is asserted here is the module's PROMISE, not its shape:

  * THE ERROR TAXONOMY IS THE CONTRACT. Each refusal names WHICH record is
    wrong and WHY. A validator that collapsed them into one "malformed fixture"
    would still refuse every bad input and would be strictly worse, so each
    message is pinned by the part that identifies the record.
  * REFUSAL ORDER. A document that is wrong in BOTH halves reports the CORPUS
    problem, because the gold half cannot be checked until the corpus has
    declared its ids. That is the only property that separates "validate the
    corpus, then the gold" from "validate both and report whichever".
  * THE CATEGORY CONTRACT. Every returned entry carries a `category` — declared,
    else `_DEFAULT_CATEGORY` — because `category` is the key the R-RET-03 skill
    pin reads. An entry that arrives uncategorised makes the pin unreachable
    over this corpus while every metric still looks fine.
  * IDS ARE COERCED ON BOTH SIDES. A gold row may name an expected id as a
    NUMBER. Membership is tested on `str(x)` AND the stored value is `str(x)`;
    testing one without the other either rejects a valid fixture or stores an id
    the scorer will never match.
  * THE SHIPPED FIXTURE IS PART OF THE CONTRACT. The frozen corpus loads, and it
    carries at least one `skills` document — otherwise this suite's own
    documented purpose (a drift-free check that the pin fires) is inert.

Stdlib-only, hermetic: temp files under `mkdtemp`, no KB, no clock, no network.
"""

from tests._paths import TESTS_DIR

import json
import os
import shutil
import sys
import tempfile
import unittest

HERE = TESTS_DIR
REPO = os.path.dirname(HERE)
if os.path.join(REPO, "scripts") not in sys.path:
    sys.path.insert(0, os.path.join(REPO, "scripts"))

import agentware_lib.retrieval.goldfixture as goldfixture  # noqa: E402


_OK_CORPUS = [
    {"id": "a", "title": "Alpha", "summary": "alpha body", "tags": ["x"],
     "category": "skills"},
    {"id": "b", "title": "Beta", "summary": "beta body", "tags": ["y"]},
]
_OK_GOLD = [{"query": "alpha", "expected_ids": ["a"]}]


class _FixtureFileMixin(object):
    """Writes a fixture document to a temp file and returns its path."""

    def setUp(self):
        self.root = tempfile.mkdtemp(prefix="goldfix-")
        self.addCleanup(shutil.rmtree, self.root, True)

    def _write(self, doc, name="fixture.json"):
        path = os.path.join(self.root, name)
        with open(path, "w", encoding="utf-8") as f:
            json.dump(doc, f)
        return path


class GoldFixtureCorpusPhaseTest(unittest.TestCase):
    """`_load_fixture_corpus` — the first of the two validation phases."""

    def test_goldfix_corpus_returns_entries_and_the_declared_id_set(self):
        """The phase returns BOTH halves of what it produced: the entries and
        the ids they declared. The id set is returned rather than re-derived by
        the caller because a second derivation is a second rule about what an
        id is, and the two can disagree."""
        entries, seen = goldfixture._load_fixture_corpus(_OK_CORPUS)
        self.assertEqual([e["id"] for e in entries], ["a", "b"])
        self.assertEqual(seen, {"a", "b"})

    def test_goldfix_corpus_defaults_the_category_but_keeps_a_declared_one(self):
        """The category contract, and it is asserted on BOTH arms in one test
        because the interesting property is that they differ: `a` declares
        `skills` and must keep it (the pin reads exactly that value), while `b`
        declares none and must arrive defaulted rather than uncategorised."""
        entries, _ = goldfixture._load_fixture_corpus(_OK_CORPUS)
        self.assertEqual(entries[0]["category"], "skills")
        self.assertEqual(entries[1]["category"], goldfixture._DEFAULT_CATEGORY)

    def test_goldfix_corpus_entry_carries_every_scored_field(self):
        """title/summary/tags are what `build_corpus` ranks on. A loader that
        dropped one would still return an entry the scorer accepts, and every
        query mentioning that text would silently stop matching."""
        entries, _ = goldfixture._load_fixture_corpus(_OK_CORPUS)
        self.assertEqual(entries[0]["title"], "Alpha")
        self.assertEqual(entries[0]["summary"], "alpha body")
        self.assertEqual(entries[0]["tags"], ["x"])

    def test_goldfix_corpus_missing_fields_become_empty_not_absent(self):
        """A record with no title/summary/tags still yields all five keys, so
        no downstream reader has to guard for a missing one."""
        entries, _ = goldfixture._load_fixture_corpus([{"id": "z"}])
        self.assertEqual(sorted(entries[0]),
                         ["category", "id", "summary", "tags", "title"])
        self.assertEqual(entries[0]["title"], "")
        self.assertEqual(entries[0]["tags"], [])

    def test_goldfix_corpus_tags_are_copied_not_aliased(self):
        """The returned entry must not share its tag list with the fixture
        record: a caller that mutates the entry would otherwise rewrite the
        document it was loaded from."""
        raw = [{"id": "z", "tags": ["t"]}]
        entries, _ = goldfixture._load_fixture_corpus(raw)
        entries[0]["tags"].append("added")
        self.assertEqual(raw[0]["tags"], ["t"])

    def test_goldfix_corpus_refusals_name_the_offending_record(self):
        """Each refusal identifies WHICH record is wrong and why. The index is
        part of the message because a fixture with fifty entries is otherwise
        un-debuggable."""
        cases = [
            (None, "'corpus' must be a non-empty list"),
            ({"a": 1}, "'corpus' must be a non-empty list"),
            ([], "'corpus' must be a non-empty list"),
            (["notadict"], "corpus entry #0 is not an object"),
            ([{"title": "t"}], "corpus entry #0 missing 'id'"),
            ([{"id": 7}], "corpus entry #0 missing 'id'"),
            ([{"id": "   "}], "corpus entry #0 missing 'id'"),
            ([{"id": "a"}, {"id": "a"}], "duplicate id 'a'"),
        ]
        for corpus, needle in cases:
            with self.assertRaises(ValueError) as ctx:
                goldfixture._load_fixture_corpus(corpus)
            self.assertIn(needle, str(ctx.exception))

    def test_goldfix_corpus_reports_the_index_of_the_bad_record(self):
        """Not #0 — the second entry. A message hard-coded to the first record
        passes every single-entry fixture."""
        with self.assertRaises(ValueError) as ctx:
            goldfixture._load_fixture_corpus([{"id": "a"}, {"id": ""}])
        self.assertIn("corpus entry #1", str(ctx.exception))


class GoldFixtureGoldPhaseTest(unittest.TestCase):
    """`_load_fixture_gold` — the second phase, which depends on the first."""

    def test_goldfix_gold_returns_query_and_expected_ids_only(self):
        """The row is normalised to exactly two keys, so a fixture that carries
        extra annotation cannot leak it into the scorer's gold set."""
        gold = goldfixture._load_fixture_gold(
            [{"query": "alpha", "expected_ids": ["a"], "note": "ignored"}],
            {"a"})
        self.assertEqual(gold, [{"query": "alpha", "expected_ids": ["a"]}])

    def test_goldfix_gold_coerces_a_numeric_expected_id_on_both_sides(self):
        """A numeric id must RESOLVE against the corpus and be STORED as a
        string. Coercing only the membership test stores an int the scorer will
        never match; coercing only the stored value rejects a valid fixture."""
        gold = goldfixture._load_fixture_gold(
            [{"query": "seven", "expected_ids": [7]}], {"7"})
        self.assertEqual(gold[0]["expected_ids"], ["7"])

    def test_goldfix_gold_refusals_name_the_offending_row(self):
        cases = [
            (None, "'gold' must be a non-empty list"),
            ([], "'gold' must be a non-empty list"),
            ({"query": "q"}, "'gold' must be a non-empty list"),
            (["notadict"], "gold entry #0 is not an object"),
            ([{"expected_ids": ["a"]}], "gold entry #0 missing 'query'"),
            ([{"query": "  ", "expected_ids": ["a"]}],
             "gold entry #0 missing 'query'"),
            ([{"query": "q"}], "gold entry #0 missing 'expected_ids'"),
            ([{"query": "q", "expected_ids": []}],
             "gold entry #0 missing 'expected_ids'"),
            ([{"query": "q", "expected_ids": ["ghost"]}],
             "gold entry #0 expects unknown id 'ghost'"),
        ]
        for rows, needle in cases:
            with self.assertRaises(ValueError) as ctx:
                goldfixture._load_fixture_gold(rows, {"a"})
            self.assertIn(needle, str(ctx.exception))

    def test_goldfix_gold_checks_every_expected_id_not_just_the_first(self):
        """A row whose FIRST id resolves and whose second does not must still
        be refused — a loop that returned on the first hit would accept it."""
        with self.assertRaises(ValueError) as ctx:
            goldfixture._load_fixture_gold(
                [{"query": "q", "expected_ids": ["a", "ghost"]}], {"a"})
        self.assertIn("unknown id 'ghost'", str(ctx.exception))

    def test_goldfix_gold_resolves_against_the_set_it_is_given(self):
        """The `seen` set is a PARAMETER, so the same rows accepted against one
        corpus are refused against another. This is what makes the two phases
        sequential rather than independent."""
        rows = [{"query": "q", "expected_ids": ["a"]}]
        self.assertTrue(goldfixture._load_fixture_gold(rows, {"a"}))
        with self.assertRaises(ValueError):
            goldfixture._load_fixture_gold(rows, {"b"})


class GoldFixtureLoaderTest(_FixtureFileMixin, unittest.TestCase):
    """`load_gold_fixture` — the file layer and the phase ORDER."""

    def test_goldfix_load_returns_entries_and_gold(self):
        path = self._write({"corpus": _OK_CORPUS, "gold": _OK_GOLD})
        entries, gold = goldfixture.load_gold_fixture(path)
        self.assertEqual([e["id"] for e in entries], ["a", "b"])
        self.assertEqual(gold, [{"query": "alpha", "expected_ids": ["a"]}])

    def test_goldfix_load_refuses_a_missing_file_by_path(self):
        missing = os.path.join(self.root, "nope.json")
        with self.assertRaises(ValueError) as ctx:
            goldfixture.load_gold_fixture(missing)
        self.assertIn("gold-fixture not found", str(ctx.exception))
        self.assertIn(missing, str(ctx.exception))

    def test_goldfix_load_refuses_unparseable_json_with_the_parser_reason(self):
        path = os.path.join(self.root, "bad.json")
        with open(path, "w", encoding="utf-8") as f:
            f.write("{ not json")
        with self.assertRaises(ValueError) as ctx:
            goldfixture.load_gold_fixture(path)
        self.assertIn("invalid JSON in gold-fixture", str(ctx.exception))

    def test_goldfix_load_refuses_a_non_object_document(self):
        path = self._write(["corpus", "gold"])
        with self.assertRaises(ValueError) as ctx:
            goldfixture.load_gold_fixture(path)
        self.assertIn("must be a JSON object", str(ctx.exception))

    def test_goldfix_load_reports_the_corpus_problem_before_the_gold_one(self):
        """THE ORDER TEST. This document is wrong in BOTH halves. The corpus
        must be the one reported, because the gold phase resolves ids against
        what the corpus declared and cannot run until it has. An implementation
        that validated gold first would pass every other test in this file."""
        path = self._write({"corpus": [], "gold": []})
        with self.assertRaises(ValueError) as ctx:
            goldfixture.load_gold_fixture(path)
        self.assertIn("'corpus' must be a non-empty list", str(ctx.exception))

    def test_goldfix_load_threads_the_corpus_ids_into_the_gold_phase(self):
        """The one value the two phases share. A gold row naming an id the
        corpus DID declare is accepted; the same row against a corpus that did
        not is refused — so the set is really flowing between them."""
        good = self._write({"corpus": [{"id": "only"}],
                            "gold": [{"query": "q", "expected_ids": ["only"]}]})
        self.assertTrue(goldfixture.load_gold_fixture(good))
        bad = self._write({"corpus": [{"id": "other"}],
                           "gold": [{"query": "q", "expected_ids": ["only"]}]},
                          "bad2.json")
        with self.assertRaises(ValueError) as ctx:
            goldfixture.load_gold_fixture(bad)
        self.assertIn("unknown id 'only'", str(ctx.exception))


class GoldFixtureShippedCorpusTest(unittest.TestCase):
    """The FROZEN corpus that ships with the package."""

    def test_goldfix_path_is_repo_anchored(self):
        path = goldfixture.gold_fixture_path()
        self.assertTrue(os.path.isabs(path))
        self.assertTrue(path.endswith(goldfixture._GOLD_FIXTURE_REL))

    def test_goldfix_shipped_corpus_loads(self):
        entries, gold = goldfixture.load_gold_fixture(
            goldfixture.gold_fixture_path())
        self.assertTrue(entries)
        self.assertTrue(gold)

    def test_goldfix_shipped_corpus_reaches_the_skill_pin(self):
        """The suite documents itself as the drift-free check that measures the
        R-RET-03 pin. The pin fires on `category == "skills"`, so a corpus with
        no skills document makes the instrument blind to the one thing it is
        for — which is exactly the defect the category contract was added to
        fix. Asserted here so it cannot silently return."""
        entries, _ = goldfixture.load_gold_fixture(
            goldfixture.gold_fixture_path())
        self.assertTrue(any(e.get("category") == "skills" for e in entries))

    def test_goldfix_shipped_corpus_ids_are_unique(self):
        entries, _ = goldfixture.load_gold_fixture(
            goldfixture.gold_fixture_path())
        ids = [e["id"] for e in entries]
        self.assertEqual(len(ids), len(set(ids)))


class GoldFixtureScorerTest(unittest.TestCase):
    """`evaluate_gold_fixture` — the drift-free scorer over the frozen corpus."""

    def setUp(self):
        self.kdir = tempfile.mkdtemp(prefix="goldfix-kb-")
        self.addCleanup(shutil.rmtree, self.kdir, True)

    def _score(self, strategy="tag", top_k=3, as_of="2026-01-15"):
        return goldfixture.evaluate_gold_fixture(
            self.kdir, goldfixture.gold_fixture_path(), strategy, top_k,
            as_of=as_of)

    def test_goldfix_score_tags_the_result_as_the_gold_fixture_suite(self):
        """The ledger keys comparability on `suite`; an untagged row would be
        compared against own-gold runs over a different corpus."""
        self.assertEqual(self._score()["suite"], goldfixture.GOLD_FIXTURE_SUITE)

    def test_goldfix_score_reports_determinism(self):
        """INV-1: the scorer re-runs and compares ranked ordering. Over a frozen
        corpus at a pinned as_of that must be True."""
        self.assertIs(self._score()["determinism_ok"], True)

    def test_goldfix_score_echoes_the_as_of_it_was_given(self):
        self.assertEqual(self._score(as_of="2020-02-02")["as_of"], "2020-02-02")

    def test_goldfix_score_fingerprint_is_independent_of_the_knowledge_dir(self):
        """The whole reason this suite exists: the corpus is frozen in the repo,
        so the fingerprint cannot move when the operator's KB grows. Scored
        against two DIFFERENT kdirs, it is the same value."""
        other = tempfile.mkdtemp(prefix="goldfix-kb2-")
        self.addCleanup(shutil.rmtree, other, True)
        first = self._score()["corpus_fingerprint"]
        second = goldfixture.evaluate_gold_fixture(
            other, goldfixture.gold_fixture_path(), "tag", 3,
            as_of="2026-01-15")["corpus_fingerprint"]
        self.assertEqual(first, second)


class GoldFixtureSurfaceTest(unittest.TestCase):

    def test_goldfix_exports_name_every_helper(self):
        """`_EXPORTS` is the module's published surface; a helper missing from
        it is one the facade cannot resolve by name."""
        for name in ("GOLD_FIXTURE_SUITE", "_GOLD_FIXTURE_REL",
                     "_DEFAULT_CATEGORY", "_corpus_entry",
                     "_load_fixture_corpus", "_load_fixture_gold",
                     "gold_fixture_path", "load_gold_fixture",
                     "evaluate_gold_fixture"):
            self.assertIn(name, goldfixture._EXPORTS)
            self.assertTrue(hasattr(goldfixture, name), name)


if __name__ == "__main__":
    unittest.main()
