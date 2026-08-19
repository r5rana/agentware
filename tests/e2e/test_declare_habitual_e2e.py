"""End-to-end composition proof for the declare-habitual follow-up
(feature 260717-p3-followup-declare-habitual, Task 3).

Walks the WHOLE payoff loop on ONE synthetic KB at a pinned `as_of`, proving the
two seams (dream `stale-propose` step + `relate` declare verb) COMPOSE with the
already-shipped read-time trust surface:

  (a) a dream `--steps s` cycle EMITS one tier-2 PROPOSED stale-review workorder
      (finding fenced in Context, non-auto-start) — surfacing computed suspicion;
  (b) `relate learn-y --supersedes learn-x` DECLARES the edge through the index
      sole-writer (before it, learn-x is trust:current);
  (c) recall now SURFACES learn-x trust:superseded superseded_by:[learn-y] and
      RANKS it below the fresh peer under bm25+acr, while plain bm25 order stays
      BYTE-IDENTICAL (no ranking code touched — R-PKG-06 by construction);
  (d) a SECOND fixture pre-loaded to the g3 draft cap makes the same dream step
      DEFER cleanly (no crash; index.json + work/auto byte-identical);
  (e) the dream emit + recall reads mutate NOTHING in the KB/index outside
      work/auto, and `relate` mutates ONLY the subject `.md` + index.json (every
      OTHER entry byte-identical).
"""

import hashlib
import json
import os

try:
    from tests._fixtures import SyntheticKBTestCase, build_synthetic_kb, load_cli
except ImportError:  # allow `python3 -m unittest tests.e2e.test_declare_habitual_e2e`
    from _fixtures import SyntheticKBTestCase, build_synthetic_kb, load_cli

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


# ZERO workorders; a fresh peer; a stale volatile learning; a to-be-superseded
# pair (x older, y newer) that does NOT yet declare the edge.
_ENTRIES = [
    _e("learn-fresh", "fresh", "2026-06-10"),
    _e("learn-stale", "stale", "2020-01-01"),
    _e("learn-x", "x", "2026-06-01"),
    _e("learn-y", "y", "2026-06-05"),
]


# Dirs the dream cycle + audit legitimately write (journal, metrics, derived
# caches, benchmarks ledger) — excluded from the "did the KB/index mutate?" witness
# so only SOURCE entries + index.json are compared.
_WITNESS_SKIP = ("work", "logs", ".cache", "benchmarks")


def _hash_kb_excl_work(kdir):
    """Sha256 over index.json + every SOURCE entry `.md` (read-only witness)."""
    h = hashlib.sha256()
    with open(os.path.join(kdir, "index.json"), "rb") as f:
        h.update(b"index.json\0")
        h.update(f.read())
    md = []
    for root, _dirs, files in os.walk(kdir):
        rel = os.path.relpath(root, kdir)
        top = rel.split(os.sep, 1)[0]
        if top in _WITNESS_SKIP:
            continue
        for name in files:
            if name.endswith(".md"):
                md.append(os.path.join(root, name))
    for path in sorted(md):
        with open(path, "rb") as f:
            h.update(os.path.relpath(path, kdir).encode("utf-8") + b"\0")
            h.update(f.read())
    return h.hexdigest()


class _NestedGuard:
    def _guard(self):
        prev = os.environ.get("AGENTWARE_NESTED_UNITTEST")
        os.environ["AGENTWARE_NESTED_UNITTEST"] = "1"
        self.addCleanup(
            lambda: os.environ.__setitem__("AGENTWARE_NESTED_UNITTEST", prev)
            if prev is not None
            else os.environ.pop("AGENTWARE_NESTED_UNITTEST", None))


class DeclareHabitualPayoffLoopTest(SyntheticKBTestCase, _NestedGuard):
    def setUp(self):
        super().setUp()
        self._guard()
        self.index_data = build_synthetic_kb(self.kdir, entries=_ENTRIES)

    def _data(self):
        data, err = CLI.load_index(self.kdir)
        self.assertIsNone(err)
        return data

    def _md_hash(self, eid):
        name = {"learn-fresh": "fresh", "learn-stale": "stale",
                "learn-x": "x", "learn-y": "y"}[eid]
        with open(os.path.join(self.kdir, "learnings", "%s.md" % name), "rb") as f:
            return hashlib.sha256(f.read()).hexdigest()

    def test_payoff_loop_composes(self):
        kb0 = _hash_kb_excl_work(self.kdir)

        # (a) dream --steps s EMITS one tier-2 PROPOSED draft, finding in Context.
        code, out, err = self.run_cli(
            ["dream", "--steps", "s", "--force", "--format", "json"])
        self.assertEqual(code, 0, err)
        step = next(s for s in json.loads(out)["steps"] if s["step"] == "s")
        self.assertFalse(step["deferred"])
        self.assertTrue(step["proposed"].startswith("auto/stale-review-"))
        auto = os.path.join(self.kdir, "work", "auto")
        self.assertEqual(len(os.listdir(auto)), 1)
        plan = os.path.join(auto, os.listdir(auto)[0], "plan.md")
        with open(plan, encoding="utf-8") as f:
            txt = f.read()
        self.assertIn("> Status: draft", txt)              # non-auto-start
        ctx = txt.split("## Context", 1)[1].split("## Target packages", 1)[0]
        self.assertIn("learn-stale", ctx)
        self.assertNotIn("learn-stale", txt.split("## Target packages", 1)[1])

        # (e-1) the emit mutated NOTHING outside work/auto.
        self.assertEqual(_hash_kb_excl_work(self.kdir), kb0,
                         "dream stale-propose must not mutate the KB/index")

        # BEFORE the declare: learn-x is current; capture plain-bm25 golden order.
        data0 = self._data()
        pay0 = CLI.build_recall_payload(
            self.kdir, data0, QUERY, top_k=10, token_budget=100000,
            strategy="bm25+acr", as_of=AS_OF)[0]
        by0 = {r["id"]: r for r in pay0["results"]}
        self.assertEqual(by0["learn-x"]["trust"], "current")
        bm25_before = CLI.retrieve_bm25(self.kdir, data0, QUERY)
        acr_before = CLI.retrieve_bm25_acr(self.kdir, data0, QUERY, AS_OF)
        # PRE-STATE for a non-vacuous downweight proof: before the declare the fresh
        # learn-x still outranks the merely-stale peer (index x < index stale).
        self.assertLess(acr_before.index("learn-x"), acr_before.index("learn-stale"))
        md_before = {e: self._md_hash(e) for e in
                     ("learn-fresh", "learn-stale", "learn-x")}
        self.assertEqual(_hash_kb_excl_work(self.kdir), kb0)  # recall is read-only

        # (b) DECLARE the edge in one call, routed through the index writer.
        code, out, err = self.run_cli(
            ["relate", "learn-y", "--supersedes", "learn-x", "--format", "json"])
        self.assertEqual(code, 0, err)
        self.assertTrue(json.loads(out)["changed"])

        # (e-2) relate mutated ONLY learn-y.md + index.json; peers byte-identical.
        for e in ("learn-fresh", "learn-stale", "learn-x"):
            self.assertEqual(self._md_hash(e), md_before[e],
                             "%s.md changed — relate over-reached" % e)

        # (c) recall now flips learn-x to superseded and sinks it under bm25+acr;
        #     plain bm25 order is BYTE-IDENTICAL.
        data1 = self._data()
        pay1 = CLI.build_recall_payload(
            self.kdir, data1, QUERY, top_k=10, token_budget=100000,
            strategy="bm25+acr", as_of=AS_OF)[0]
        by1 = {r["id"]: r for r in pay1["results"]}
        self.assertEqual(by1["learn-x"]["trust"], "superseded")
        self.assertEqual(by1["learn-x"]["superseded_by"], ["learn-y"])
        acr = CLI.retrieve_bm25_acr(self.kdir, data1, QUERY, AS_OF)
        # NON-VACUOUS downweight proof: the 0.3 superseded multiplier must FLIP an
        # ordering that was NOT already true — learn-x sinks BELOW the merely-stale
        # peer (false before, true after) AND strictly below its own pre-declare
        # rank. (The weaker "below the fresh peer" was already true pre-declare.)
        self.assertGreater(acr.index("learn-x"), acr.index("learn-stale"),
                           "superseded learn-x must sink below the merely-stale peer")
        self.assertGreater(acr.index("learn-x"), acr_before.index("learn-x"),
                           "the declare must strictly lower learn-x's bm25+acr rank")
        self.assertEqual(CLI.retrieve_bm25(self.kdir, data1, QUERY), bm25_before,
                         "plain bm25 order must be byte-identical (R-PKG-06)")


class DeclareHabitualDeferAtCapTest(SyntheticKBTestCase, _NestedGuard):
    def setUp(self):
        super().setUp()
        self._guard()
        self.index_data = build_synthetic_kb(self.kdir, entries=_ENTRIES)
        for i in range(5):
            d = os.path.join(self.kdir, "work", "auto", "wo-filler-%d" % i)
            os.makedirs(d, exist_ok=True)
            with open(os.path.join(d, "plan.md"), "w", encoding="utf-8") as f:
                f.write("# Plan — filler %d\n\n> Status: draft\n\n"
                        "## Tasks\n- ⬜ **1** x\n" % i)
        # the cap precondition that makes this test meaningful (F9).
        self.assertEqual(CLI._count_auto_open_workorders(self.kdir), 5)

    def _idx_bytes(self):
        with open(os.path.join(self.kdir, "index.json"), "rb") as f:
            return f.read()

    def test_defers_cleanly_at_cap(self):
        idx_before = self._idx_bytes()
        auto_before = sorted(os.listdir(os.path.join(self.kdir, "work", "auto")))
        code, out, err = self.run_cli(
            ["dream", "--steps", "s", "--force", "--format", "json"])
        self.assertEqual(code, 0, err)                     # a defer is not a fault
        step = next(s for s in json.loads(out)["steps"] if s["step"] == "s")
        self.assertTrue(step["deferred"])
        self.assertEqual(step["proposed"], "-")
        self.assertEqual(
            sorted(os.listdir(os.path.join(self.kdir, "work", "auto"))),
            auto_before, "at-cap must not emit a 6th workorder")
        self.assertEqual(self._idx_bytes(), idx_before)


if __name__ == "__main__":
    import unittest
    unittest.main()
