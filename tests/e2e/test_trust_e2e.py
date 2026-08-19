"""End-to-end integration proof for P3 trust & staleness
(feature 260713-p3-trust-staleness, Task 6).

Walks the WHOLE feature on ONE synthetic KB (fresh + stale + superseded pair) at a
pinned `as_of`, proving the pieces compose:

  (a) recall SURFACES trust:stale and trust:superseded + superseded_by:[Y] under
      every reachable strategy (bm25, bm25+acr, bm25+embed);
  (b) under bm25+acr the stale AND superseded entries rank BELOW the fresh peer,
      while plain bm25 order is BYTE-IDENTICAL to the golden;
  (c) audit --stale --propose emits a tier-2 PROPOSED draft workorder with the
      finding fenced in Context;
  (d) recall / audit --stale / --propose mutate NOTHING in the KB/index.

The per-task suites cover each mechanism in isolation; this is the composition
proof on a single flow. The heavyweight gate chain (steering lint / gate release /
eval --record --gate) is run OUTSIDE the harness (see the worklog).
"""

import hashlib
import json
import os

try:
    from tests._fixtures import (SyntheticKBTestCase, build_synthetic_kb,
                                 load_cli, run_cli)
except ImportError:  # allow `python3 -m unittest tests.e2e.test_trust_e2e`
    from _fixtures import (SyntheticKBTestCase, build_synthetic_kb,
                           load_cli, run_cli)

CLI = load_cli()
AS_OF = "2026-06-25"
QUERY = "cache invalidation race"
_BODY = "cache invalidation race stale value ordering note"


def _e(eid, name, lv, **extra):
    e = {
        "id": eid, "title": name, "category": "learnings",
        "path": "learnings/%s.md" % name, "tags": ["cache", "race"],
        "created": lv, "last_verified": lv,
        "summary": "cache invalidation race note",
        "body": "# %s\n\n%s" % (name, _BODY),
    }
    e.update(extra)
    return e


_ENTRIES = [
    _e("learn-fresh", "fresh", "2026-06-10"),
    _e("learn-stale", "stale", "2024-01-01"),
    _e("learn-x", "x", "2026-06-01"),
    _e("learn-y", "y", "2026-06-05", relates=["supersedes:learn-x"]),
]

GOLDEN_BM25 = ["learn-fresh", "learn-y", "learn-x", "learn-stale"]


def _hash_kb(kdir):
    """Sha256 over index.json + every KB `.md` OUTSIDE work/."""
    h = hashlib.sha256()
    with open(os.path.join(kdir, "index.json"), "rb") as f:
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


class TrustEndToEndTest(SyntheticKBTestCase):

    def setUp(self):
        super().setUp()
        self.index_data = build_synthetic_kb(self.kdir, entries=_ENTRIES)

    def test_full_trust_staleness_story(self):
        kb_hash_before = _hash_kb(self.kdir)

        # (a) SURFACE under every reachable recall strategy.
        for strategy in ("bm25", "bm25+acr", "bm25+embed"):
            payload, _n = CLI.build_recall_payload(
                self.kdir, self.index_data, QUERY, top_k=10,
                token_budget=100000, strategy=strategy, as_of=AS_OF)
            by_id = {r["id"]: r for r in payload["results"]}
            self.assertEqual(by_id["learn-stale"]["trust"], "stale", strategy)
            self.assertTrue(by_id["learn-stale"]["stale"], strategy)
            self.assertEqual(by_id["learn-x"]["trust"], "superseded", strategy)
            self.assertEqual(by_id["learn-x"]["superseded_by"], ["learn-y"],
                             strategy)
            self.assertEqual(by_id["learn-fresh"]["trust"], "current", strategy)
            self.assertEqual(by_id["learn-y"]["trust"], "current", strategy)

        # (b) DOWNWEIGHT under bm25+acr; plain bm25 BYTE-IDENTICAL to golden.
        self.assertEqual(
            CLI.retrieve_bm25(self.kdir, self.index_data, QUERY), GOLDEN_BM25)
        acr = CLI.retrieve_bm25_acr(self.kdir, self.index_data, QUERY, AS_OF)
        self.assertEqual(set(acr), set(GOLDEN_BM25))  # same surfaced set
        # stale AND superseded rank below the fresh peer
        self.assertGreater(acr.index("learn-stale"), acr.index("learn-fresh"))
        self.assertGreater(acr.index("learn-x"), acr.index("learn-fresh"))
        # superseded sinks below the (equally-matching) merely-stale peer
        self.assertGreater(acr.index("learn-x"), acr.index("learn-stale"))
        # plain bm25 order is UNCHANGED even after running bm25+acr
        self.assertEqual(
            CLI.retrieve_bm25(self.kdir, self.index_data, QUERY), GOLDEN_BM25)

        # (c) PROPOSE a tier-2 draft workorder, finding fenced in Context.
        code, out, err = self.run_cli(
            ["audit", "--stale", "--propose", "--format", "json"])
        proposal = json.loads(out)["proposal"]
        self.assertTrue(proposal["proposed"], err)
        feature = proposal["feature"]
        self.assertTrue(feature.startswith("auto/stale-review-"))
        plan = os.path.join(
            self.kdir, feature.replace("auto/", "work/auto/", 1), "plan.md")
        with open(plan, encoding="utf-8") as f:
            txt = f.read()
        self.assertIn("> Status: draft", txt)          # non-auto-start
        context = txt.split("## Context", 1)[1].split("## Target packages", 1)[0]
        after = txt.split("## Target packages", 1)[1]
        # the superseded + stale findings are quoted ONLY inside Context
        for fid in ("learn-x", "learn-stale", "learn-y"):
            self.assertIn(fid, context)
            self.assertNotIn(fid, after)
        # the report surfaces the declared supersede pair
        rep = json.loads(out)["stale_report"]
        self.assertEqual(
            {s["id"]: s["superseded_by"] for s in rep["superseded"]},
            {"learn-x": ["learn-y"]})

        # (d) the whole flow mutated NOTHING in the KB/index (INV-2).
        self.assertEqual(_hash_kb(self.kdir), kb_hash_before,
                         "recall/audit/--propose must not mutate the KB/index")


if __name__ == "__main__":
    import unittest
    unittest.main()
