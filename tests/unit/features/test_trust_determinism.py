"""Determinism + gold-fixture no-regression GUARD for P3 trust & staleness
(feature 260713-p3-trust-staleness, Task 5).

Authors NO product code — it PINS the load-bearing invariants so the release gate
(R-PKG-06) and any future refactor cannot silently regress them:

  (a) INV-1  — `trust_signals` / `staleness_factor` are byte-identical across
               repeated in-process runs AND across processes (PYTHONHASHSEED) at a
               fixed `as_of` (no clock / set-iteration-order leak).
  (b) GOLD   — plain `bm25` id-order over a KB containing stale + superseded
               entries equals a captured golden AND is byte-identical whether or
               not `staleness_factor` is even importable (it is never called on the
               bm25 path — proven by monkeypatching it to explode).
  (c) INV-2  — recall (bm25 / bm25+acr / bm25+embed), `audit --stale`, and
               `audit --stale --propose` leave `index.json` + every KB `.md`
               (outside work/) byte-identical.
  (d) C-3    — a zero-BM25 stale/superseded entry is NEVER surfaced (the bounded
               factor cannot lift it).
  (e) BOUND  — `staleness_factor ∈ (0, 1]` for every entry shape.
"""

from tests._paths import TESTS_DIR

import hashlib
import json
import os
import subprocess
import sys

try:
    from tests._fixtures import (SyntheticKBTestCase, build_synthetic_kb,
                                 load_cli, run_cli)
except ImportError:  # allow `python3 -m unittest tests.unit.features.test_trust_determinism`
    from _fixtures import (SyntheticKBTestCase, build_synthetic_kb,
                           load_cli, run_cli)

CLI = load_cli()
AS_OF = "2026-06-25"
QUERY = "alpha beta gamma delta"
_BODY = "alpha beta gamma delta retrieval ranking note"


def _e(eid, name, category, lv, body=None, **extra):
    e = {
        "id": eid, "title": name, "category": category,
        "path": "%s/%s.md" % (category, name), "tags": ["alpha", "beta"],
        "created": lv, "last_verified": lv, "summary": "alpha beta gamma note",
        "body": "# %s\n\n%s" % (name, body or _BODY),
    }
    e.update(extra)
    return e


# Full spectrum: fresh, stale-volatile, a superseded pair, an old non-volatile,
# and a genuinely zero-BM25 entry (summary/tags/body share no query term).
_ENTRIES = [
    _e("learn-fresh", "fresh", "learnings", "2026-06-10"),
    _e("learn-stale", "stale", "learnings", "2024-01-01"),
    _e("learn-old", "old", "learnings", "2026-06-01"),
    _e("learn-new", "new", "learnings", "2026-06-05",
       relates=["supersedes:learn-old"]),
    _e("ref-oldnonvol", "oldnonvol", "references", "2020-01-01"),
    _e("learn-zerobm25", "zero", "learnings", "2024-01-01",
       body="wholly unrelated omega content", summary="omega unrelated note",
       tags=["omega", "kappa"]),
]

# Captured goldens (see the plan's Task-5 verify). Plain bm25 is the R-PKG-06
# gold-fixture path — it must NEVER change. bm25+acr shows the downweight reorder.
GOLDEN_BM25 = ["learn-fresh", "learn-new", "learn-old", "learn-stale",
               "ref-oldnonvol"]
GOLDEN_BM25_ACR = ["learn-fresh", "learn-new", "ref-oldnonvol", "learn-stale",
                   "learn-old"]


def _hash_kb(kdir):
    """Sha256 over index.json + every KB `.md` OUTSIDE work/ (the proposal path
    legitimately writes work/auto/<slug>/plan.md). Deterministic (sorted)."""
    h = hashlib.sha256()
    idx = os.path.join(kdir, "index.json")
    if os.path.isfile(idx):
        with open(idx, "rb") as f:
            h.update(b"index.json\0")
            h.update(f.read())
    md = []
    for root, _dirs, files in os.walk(kdir):
        rel = os.path.relpath(root, kdir)
        if rel == "work" or rel.startswith("work" + os.sep):
            continue
        for name in files:
            if name.endswith(".md"):
                md.append(os.path.join(root, name))
    for path in sorted(md):
        with open(path, "rb") as f:
            h.update(os.path.relpath(path, kdir).encode("utf-8") + b"\0")
            h.update(f.read())
    return h.hexdigest()


class TrustDeterminismTest(SyntheticKBTestCase):

    def setUp(self):
        super().setUp()
        self.index_data = build_synthetic_kb(self.kdir, entries=_ENTRIES)

    # (a) INV-1 -------------------------------------------------------------

    def test_trust_signals_deterministic_in_process(self):
        a = json.dumps(CLI.trust_signals(self.index_data, AS_OF), sort_keys=True)
        b = json.dumps(CLI.trust_signals(self.index_data, AS_OF), sort_keys=True)
        self.assertEqual(a, b)

    def test_staleness_factor_deterministic_in_process(self):
        sup = CLI.superseded_ids(self.index_data)
        for e in self.index_data["entries"]:
            self.assertEqual(CLI.staleness_factor(e, sup, AS_OF),
                             CLI.staleness_factor(e, sup, AS_OF))

    def test_trust_signals_deterministic_across_hash_seeds(self):
        # Set/dict iteration order varies by PYTHONHASHSEED between processes but
        # is stable within one — so this is the only place that class of INV-1
        # nondeterminism can surface. superseded_by is sorted; stale_ids is
        # membership-only.
        script = (
            "import json, sys\n"
            "sys.path.insert(0, %r)\n"
            "from _fixtures import load_cli, build_synthetic_kb\n"
            "import tempfile\n"
            "m = load_cli()\n"
            "e = [\n"
            "  {'id': 'x', 'category': 'learnings', 'last_verified': '2020-01-01'},\n"
            "  {'id': 'y', 'category': 'learnings', 'last_verified': %r,\n"
            "   'relates': ['supersedes:x']},\n"
            "  {'id': 'z', 'category': 'learnings', 'last_verified': %r,\n"
            "   'relates': ['supersedes:x']},\n"
            "  {'id': 'w', 'category': 'learnings', 'last_verified': %r,\n"
            "   'relates': ['supersedes:x']},\n"
            "]\n"
            "print(json.dumps(m.trust_signals({'entries': e}, %r), sort_keys=True))\n"
            % (TESTS_DIR, AS_OF, AS_OF, AS_OF,
               AS_OF))
        outs = []
        for seed in ("0", "1", "12345", "99999"):
            r = subprocess.run(
                [sys.executable, "-c", script],
                env=dict(os.environ, PYTHONHASHSEED=seed),
                capture_output=True, text=True)
            self.assertEqual(r.returncode, 0, r.stderr)
            outs.append(r.stdout)
        self.assertEqual(len(set(outs)), 1,
                         "trust_signals output varies with PYTHONHASHSEED")

    # (b) GOLD-FIXTURE invariant -------------------------------------------

    def test_plain_bm25_matches_golden(self):
        self.assertEqual(
            CLI.retrieve_bm25(self.kdir, self.index_data, QUERY), GOLDEN_BM25)
        # recall_ranked(bm25) agrees (shared core), scores desc order preserved.
        ranked = [e.get("id") for (e, _s, _t) in CLI.recall_ranked(
            self.kdir, self.index_data, QUERY, strategy="bm25", as_of=AS_OF)]
        self.assertEqual(ranked, GOLDEN_BM25)

    def test_plain_bm25_never_touches_staleness_factor(self):
        # Sabotage staleness_factor to raise: plain bm25 must be UNAFFECTED (proves
        # the gold-fixture invariant structurally, not just by value).
        orig = CLI.staleness_factor
        CLI.staleness_factor = (lambda *a, **k: (_ for _ in ()).throw(
            AssertionError("staleness_factor must NOT be on the bm25 path")))
        try:
            self.assertEqual(
                CLI.retrieve_bm25(self.kdir, self.index_data, QUERY), GOLDEN_BM25)
        finally:
            CLI.staleness_factor = orig

    def test_bm25_acr_matches_golden_and_reorders_superseded(self):
        got = CLI.retrieve_bm25_acr(self.kdir, self.index_data, QUERY, AS_OF)
        self.assertEqual(got, GOLDEN_BM25_ACR)
        # the downweight moved the superseded entry BELOW the fresh peers
        self.assertGreater(got.index("learn-old"), got.index("learn-fresh"))
        self.assertGreater(got.index("learn-old"), got.index("learn-new"))
        # same surfaced SET as plain bm25 (relevance-gated; only reordered)
        self.assertEqual(set(got), set(GOLDEN_BM25))

    # (c) INV-2 read-only ---------------------------------------------------

    def test_recall_and_audit_are_read_only(self):
        before = _hash_kb(self.kdir)
        for strat in ("bm25", "bm25+acr", "bm25+embed"):
            CLI.build_recall_payload(self.kdir, self.index_data, QUERY,
                                     strategy=strat, as_of=AS_OF)
        self.run_cli(["recall", QUERY, "--as-of", AS_OF, "--format", "json"])
        self.run_cli(["audit", "--stale", "--format", "json"])
        self.run_cli(["audit", "--stale", "--propose", "--format", "json"])
        self.assertEqual(_hash_kb(self.kdir), before,
                         "recall/audit/--propose must not mutate index.json or "
                         "any KB .md")

    def test_propose_only_writes_under_work(self):
        # The proposal's sole write is a work artifact, never a KB entry/index.
        idx = os.path.join(self.kdir, "index.json")
        with open(idx, "rb") as f:
            idx_before = f.read()
        _code, out, _err = self.run_cli(
            ["audit", "--stale", "--propose", "--format", "json"])
        self.assertTrue(json.loads(out)["proposal"]["proposed"])
        self.assertTrue(os.path.isdir(os.path.join(self.kdir, "work", "auto")))
        with open(idx, "rb") as f:
            self.assertEqual(f.read(), idx_before)

    # (d) C-3 zero-BM25 never surfaced -------------------------------------

    def test_zero_bm25_entry_never_surfaced(self):
        self.assertNotIn("learn-zerobm25",
                         CLI.retrieve_bm25(self.kdir, self.index_data, QUERY))
        self.assertNotIn("learn-zerobm25", CLI.retrieve_bm25_acr(
            self.kdir, self.index_data, QUERY, AS_OF))
        # even though it IS stale (its own trust signal says so) it is never lifted
        self.assertEqual(
            CLI.trust_signals(self.index_data, AS_OF)["learn-zerobm25"]["trust"],
            "stale")

    # (e) BOUND -------------------------------------------------------------

    def test_staleness_factor_bounded_for_every_shape(self):
        shapes = [
            {"id": "a", "category": "learnings", "last_verified": AS_OF},
            {"id": "b", "category": "learnings", "last_verified": "2020-01-01"},
            {"id": "c", "category": "references", "last_verified": "2020-01-01"},
            {"id": "d", "category": "learnings"},                # no date
            {"id": "e", "category": "configurations",
             "last_verified": "2020-01-01"},
            {"id": "f"},                                          # no category
            {"id": "g", "category": "learnings",
             "last_verified": "not-a-date"},
            {"id": "h", "category": "learnings",
             "last_verified": "2099-01-01"},                     # future
        ]
        for e in shapes:
            for sup in (set(), {e.get("id")}):
                f = CLI.staleness_factor(e, sup, AS_OF)
                self.assertGreater(f, 0.0, e)
                self.assertLessEqual(f, 1.0, e)


if __name__ == "__main__":
    import unittest
    unittest.main()
