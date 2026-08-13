"""The donor's EXACT-SCORE BM25 pin: ranking order is not enough.

Feature 260813-modular-core-adoption, task 79 (parity row
`retrieval:bm25-exact-score-pin`, dimension `retrieval_policy`, declared at
task 59).

WHY THIS FILE EXISTS
--------------------
The parity manifest has carried this row since task 10 with the capability
stated plainly: *"the donor pins BM25 scores exactly -- order-based assertions
alone let a mutated K1 or B survive every test in this tree."* Task 79's
mutation sweep ran that claim for the first time and it was TRUE: perturbing
`_BM25_K1` from 1.5 to 1.45 left the entire `tests/unit/retrieval` tier green,
336 tests, and the whole suite with it. The two parameters that decide how term
frequency saturates and how length normalisation is applied were unguarded --
any edit, refactor or merge could have changed retrieval behaviour for every
query the operator ever runs and nothing would have gone red.

The reason the tier could not see it is structural, not an oversight: BM25's
`k1` and `b` are monotone knobs. Moving them slightly rescales every score
without reordering anything, so assertions of the form "the right document ranks
first" are blind to them by construction. Only an assertion about the NUMBER can
see it, and this repository had none.

THE THREE ARMS
--------------
  A  GOLDEN -- a fixed four-document corpus and a two-term query, scored to
     seventeen significant digits. Regenerating this corpus is a deliberate act;
     the numbers are not a snapshot of whatever the code currently returns
     because arm B derives them independently.
  B  INDEPENDENT -- the Okapi formula re-implemented here from the literals
     1.5 and 0.75, agreeing with the shipped scorer to 1e-12. A golden nobody
     can re-derive is a photograph of a bug as easily as of a behaviour; two
     implementations agreeing is a claim about the formula.
  C  REFUTATION -- the property that makes arms A and B non-redundant, asserted
     rather than argued: perturb `k1` (and separately `b`) by a fraction, and
     the ranking ORDER of the corpus is unchanged while every non-zero score
     moves. That is the exact shape of the mutation this tier could not catch,
     so it is pinned as a fact about BM25 rather than a comment.

Stdlib `unittest` only. No knowledge base, no temp files, no network: the corpus
is four literal strings.
"""

from tests._paths import REPO_ROOT

import math
import os
import sys
import unittest

if os.path.join(REPO_ROOT, "scripts") not in sys.path:
    sys.path.insert(0, os.path.join(REPO_ROOT, "scripts"))

from agentware_lib.retrieval import bm25 as B          # noqa: E402
from agentware_lib.retrieval import corpus as C        # noqa: E402

#: Four documents chosen so the query terms land in three of them with different
#: term frequencies and different lengths -- `d1` is short with a repeated term,
#: `d3` is long with a heavily repeated one, `d2` is minimal, `d4` shares no
#: query term at all and must score exactly zero.
CORPUS = (
    ("d1", "alpha beta gamma alpha"),
    ("d2", "alpha delta"),
    ("d3", "beta beta beta gamma delta epsilon"),
    ("d4", "zeta eta theta iota kappa lambda"),
)

QUERY = "alpha beta"

#: THE GOLDEN. Okapi BM25 with k1=1.5, b=0.75 over CORPUS for QUERY.
EXPECTED = {
    "d1": 1.7565133230563914,
    "d2": 0.92419624074659379,
    "d3": 1.0663802777845313,
    "d4": 0.0,
}

#: The parameters themselves. Pinned separately from the scores so a diff that
#: changes one of them is named by BOTH a value assertion and a behaviour one.
EXPECTED_K1 = 1.5
EXPECTED_B = 0.75

TOLERANCE = 1e-12


def _reference_scores(corpus, query_tokens, k1, b):
    """Okapi BM25, re-derived here. Deliberately NOT importing the shipped one.

    idf = ln(1 + (N - df + 0.5)/(df + 0.5)); the term contribution saturates
    through k1 and is length-normalised through b.
    """
    docs = []
    df = {}
    total = 0
    for entry, text in corpus:
        toks = C.tokenize(text)
        tf = {}
        for tok in toks:
            tf[tok] = tf.get(tok, 0) + 1
        docs.append((entry, tf, len(toks)))
        total += len(toks)
        for tok in tf:
            df[tok] = df.get(tok, 0) + 1
    n = len(docs)
    avgdl = (total / n) if n else 0.0
    terms = set(query_tokens)
    out = []
    for entry, tf, dl in docs:
        score = 0.0
        for term in terms:
            freq = tf.get(term, 0)
            if not freq:
                continue
            n_t = df.get(term, 0)
            idf = math.log(1.0 + (n - n_t + 0.5) / (n_t + 0.5))
            norm = freq * (k1 + 1.0) / (
                freq + k1 * (1.0 - b + b * (dl / avgdl if avgdl else 0.0)))
            score += idf * norm
        out.append((entry, score))
    return out


def _live_scores():
    return dict(B.bm25_scores(list(CORPUS), C.tokenize(QUERY)))


def _ranking(scored):
    """Entries best-first, ties broken by id, exactly as a reader would read it."""
    return [entry for entry, score in sorted(scored.items(),
                                             key=lambda kv: (-kv[1], kv[0]))]


class Bm25ParametersArePinnedTest(unittest.TestCase):
    """Arm A (part 1) -- the two constants that decide the ranking's shape."""

    def test_k1_and_b_are_the_documented_okapi_defaults(self):
        self.assertEqual(
            (B._BM25_K1, B._BM25_B), (EXPECTED_K1, EXPECTED_B),
            "the Okapi parameters moved. They are ranking policy for every "
            "recall this operator runs; changing them is a decision, not a "
            "refactor. Re-derive EXPECTED with the new values and record why.")


class Bm25ExactScoreGoldenTest(unittest.TestCase):
    """Arm A (part 2) -- the numbers, not the order."""

    def test_every_document_scores_its_pinned_value(self):
        live = _live_scores()
        self.assertEqual(sorted(live), sorted(EXPECTED),
                         "the scorer returned a different document set")
        for entry in sorted(EXPECTED):
            self.assertAlmostEqual(
                live[entry], EXPECTED[entry], delta=TOLERANCE,
                msg="%s scored %.17g, pinned at %.17g -- a scoring change this "
                    "small is invisible to every order-based assertion in the "
                    "tier" % (entry, live[entry], EXPECTED[entry]))

    def test_a_document_sharing_no_query_term_scores_exactly_zero(self):
        # Not almost-zero: the scorer's docstring promises exactly 0.0, and an
        # epsilon here would hide a stray additive term.
        self.assertEqual(_live_scores()["d4"], 0.0)


class Bm25IndependentDerivationTest(unittest.TestCase):
    """Arm B -- a second implementation, so the golden is derived not snapshotted."""

    def test_the_shipped_scorer_matches_an_independent_okapi_implementation(self):
        reference = dict(_reference_scores(CORPUS, C.tokenize(QUERY),
                                           EXPECTED_K1, EXPECTED_B))
        live = _live_scores()
        for entry in sorted(reference):
            self.assertAlmostEqual(
                live[entry], reference[entry], delta=TOLERANCE,
                msg="%s: shipped %.17g vs independent %.17g" % (
                    entry, live[entry], reference[entry]))

    def test_the_golden_table_is_what_the_independent_derivation_produces(self):
        reference = dict(_reference_scores(CORPUS, C.tokenize(QUERY),
                                           EXPECTED_K1, EXPECTED_B))
        for entry in sorted(EXPECTED):
            self.assertAlmostEqual(reference[entry], EXPECTED[entry],
                                   delta=TOLERANCE, msg=entry)


class OrderBasedAssertionsCannotSeeTheseParametersTest(unittest.TestCase):
    """Arm C -- the refutation that makes this file non-redundant."""

    PERTURBATIONS = ((-0.05, 0.0), (0.05, 0.0), (0.0, -0.05), (0.0, 0.05))

    def test_perturbing_k1_or_b_moves_every_score_and_no_position(self):
        base_scores = dict(_reference_scores(CORPUS, C.tokenize(QUERY),
                                             EXPECTED_K1, EXPECTED_B))
        base_order = _ranking(base_scores)
        for dk1, db in self.PERTURBATIONS:
            k1, b = EXPECTED_K1 + dk1, EXPECTED_B + db
            moved = dict(_reference_scores(CORPUS, C.tokenize(QUERY), k1, b))
            self.assertEqual(
                _ranking(moved), base_order,
                "k1=%s b=%s REORDERED the corpus, so this perturbation is not "
                "the invisible kind and the arm proves nothing" % (k1, b))
            changed = [e for e in base_scores
                       if abs(moved[e] - base_scores[e]) > TOLERANCE]
            self.assertEqual(
                sorted(changed), ["d1", "d2", "d3"],
                "k1=%s b=%s left a non-zero score untouched: %r" % (k1, b, moved))

    def test_the_zero_scoring_document_stays_zero_under_every_perturbation(self):
        for dk1, db in self.PERTURBATIONS:
            moved = dict(_reference_scores(CORPUS, C.tokenize(QUERY),
                                           EXPECTED_K1 + dk1, EXPECTED_B + db))
            self.assertEqual(moved["d4"], 0.0)


if __name__ == "__main__":
    unittest.main()
