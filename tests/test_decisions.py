"""Tests for the `> DECISION:` autonomous-decision machinery (feature
260626-autonomous-decisions-steering).

This mirrors the existing `> LEARNED:` plumbing: a `DECISION_RE` marker, an
ADDITIVE `worklog scan` gate (the completion promise is blocked while any
`> DECISION:` marker is unpromoted, per R-AUTO-05), and a `decide` writer that
registers a `decisions/`-category entry (auto-tagged `decision`) via the index —
NEVER by hand-editing index.json.

Written RED-first: until `DECISION_RE`, the extended scanner, and `cmd_decide` +
its subparser exist, the regex lookups return None / the unknown `decide`
subcommand raises SystemExit(2), so every assertion fails.

Runner:  python3 -m unittest tests.test_decisions -v
"""

import json
import os
import unittest

try:
    from tests._fixtures import SyntheticKBTestCase, load_cli
except ImportError:  # when run via `discover -s tests`, tests/ is on sys.path
    from _fixtures import SyntheticKBTestCase, load_cli


def _write(path, text):
    with open(path, "w", encoding="utf-8") as f:
        f.write(text)


class DecisionRegexTestCase(unittest.TestCase):
    """(a) DECISION_RE recognizes `> DECISION:` markers and nothing else."""

    def setUp(self):
        self.mod = load_cli()
        self.re = getattr(self.mod, "DECISION_RE", None)
        self.assertIsNotNone(
            self.re, "scripts/agentware must define DECISION_RE")

    def test_matches_canonical_marker(self):
        m = self.re.match("> DECISION: chose bm25 over acr")
        self.assertIsNotNone(m)
        self.assertEqual(m.group(1).strip(), "chose bm25 over acr")

    def test_matches_indented_and_no_caret(self):
        self.assertIsNotNone(self.re.match("   > DECISION: indented marker"))
        self.assertIsNotNone(self.re.match("DECISION: bare marker"))

    def test_ignores_learned_and_prose(self):
        self.assertIsNone(self.re.match("> LEARNED: this is a learning"))
        self.assertIsNone(self.re.match("a decision was made earlier"))


class DecisionScanTestCase(SyntheticKBTestCase):
    """(b)+(d) `worklog scan` reports unpromoted DECISION markers (additive to
    LEARNED) and stops flagging once the decision is promoted."""

    def _worklog(self, text):
        p = os.path.join(self.kdir, "wl.md")
        _write(p, text)
        return p

    def test_unpromoted_decision_is_reported_and_fails(self):
        wl = self._worklog(
            "# wl\n\n> DECISION: switch-retriever-default-to-bm25\n")
        code, out, _ = self.run_cli(["worklog", "scan", "--path", wl,
                                     "--format", "json"])
        payload = json.loads(out)
        # Combined top-level list keeps exit semantics; each item is labeled.
        kinds = {item.get("kind") for item in payload["unpromoted"]}
        texts = " ".join(item["text"] for item in payload["unpromoted"])
        self.assertIn("decision", kinds)
        self.assertIn("switch-retriever-default-to-bm25", texts)
        self.assertEqual(code, 1, "unpromoted DECISION must fail the scan")

    def test_learned_still_reported_backward_compatible(self):
        wl = self._worklog("# wl\n\n> LEARNED: some-unpromoted-finding\n")
        code, out, _ = self.run_cli(["worklog", "scan", "--path", wl,
                                     "--format", "json"])
        payload = json.loads(out)
        texts = " ".join(item["text"] for item in payload["unpromoted"])
        self.assertIn("some-unpromoted-finding", texts)
        self.assertEqual(code, 1)

    def test_clean_worklog_passes(self):
        wl = self._worklog("# wl\n\nno markers here\n")
        code, out, _ = self.run_cli(["worklog", "scan", "--path", wl,
                                     "--format", "json"])
        payload = json.loads(out)
        self.assertEqual(payload["unpromoted"], [])
        self.assertEqual(code, 0)

    def test_promoted_decision_not_flagged(self):
        topic = "switch-retriever-default-to-bm25"
        code, _, err = self.run_cli([
            "decide", "--topic", topic,
            "--summary", "Default retriever stays bm25 pending acr win",
            "--options", "keep bm25 | flip to acr",
            "--choice", "keep bm25",
            "--rationale", "acr has not beaten bm25 on the ledger yet",
            "--reversible", "yes — one ledger row flips it back"])
        self.assertEqual(code, 0, err)
        wl = self._worklog(
            "# wl\n\n> DECISION: [promoted -> %s] keep bm25\n" % topic)
        code, out, _ = self.run_cli(["worklog", "scan", "--path", wl,
                                     "--format", "json"])
        payload = json.loads(out)
        self.assertEqual(payload["unpromoted"], [], payload)
        self.assertEqual(code, 0)


class DecideWriterTestCase(SyntheticKBTestCase):
    """(c) `decide` writes a registered `decisions/`-category entry."""

    def _decide(self, topic, **over):
        argv = [
            "decide", "--topic", topic,
            "--summary", over.get("summary", "a one-line decision summary"),
            "--options", over.get("options", "A | B"),
            "--choice", over.get("choice", "A"),
            "--rationale", over.get("rationale", "A is reversible and in-scope"),
            "--reversible", over.get("reversible", "yes")]
        if "tags" in over:
            argv += ["--tags", over["tags"]]
        return self.run_cli(argv)

    def test_writes_file_and_registers_entry(self):
        code, out, err = self._decide("bounded-decision-example")
        self.assertEqual(code, 0, err)
        # File created under decisions/.
        abs_path = os.path.join(self.kdir, "decisions",
                                "bounded-decision-example.md")
        self.assertTrue(os.path.isfile(abs_path), abs_path)
        # Registered in the index with the right category + auto tag.
        data = self.read_index()
        entry = next((e for e in data["entries"]
                      if e["id"] == "decide-bounded-decision-example"), None)
        self.assertIsNotNone(entry, data["entries"])
        self.assertEqual(entry["category"], "decisions")
        self.assertIn("decision", entry["tags"])
        self.assertEqual(entry["path"], "decisions/bounded-decision-example.md")

    def test_body_captures_decision_fields(self):
        self._decide("field-capture", options="X | Y", choice="Y",
                     rationale="Y is cheaper", reversible="no")
        with open(os.path.join(self.kdir, "decisions", "field-capture.md"),
                  encoding="utf-8") as f:
            body = f.read()
        for needle in ("X | Y", "Y is cheaper", "Choice", "Rationale",
                       "Reversib"):
            self.assertIn(needle, body)

    def test_index_validate_accepts_decisions_entry(self):
        self._decide("validate-me")
        code, out, _ = self.run_cli(["index", "validate", "--format", "json"])
        payload = json.loads(out)
        self.assertTrue(payload["ok"], payload)
        self.assertEqual(payload["errors"], [])

    def test_duplicate_topic_refused(self):
        self._decide("dupe-topic")
        code, _, err = self._decide("dupe-topic")
        self.assertEqual(code, 1)
        self.assertIn("duplicate", (err or "").lower())


class DecideCliSurfaceTestCase(SyntheticKBTestCase):
    """`decide --help` is a real subcommand (exits 0 via argparse)."""

    def test_help_exits_zero(self):
        with self.assertRaises(SystemExit) as ctx:
            self.run_cli(["decide", "--help"])
        self.assertEqual(ctx.exception.code, 0)


if __name__ == "__main__":
    unittest.main()
