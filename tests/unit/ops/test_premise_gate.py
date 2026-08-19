"""Tests for the premise-grounding gate (feature 260717-premise-grounding-gate).

`plan verify-premises` re-verifies a plan's `## Premises` block against LIVE state
using a CLOSED check-kind grammar (cite-live / config-eq / path / corpus-metric)
with ZERO command-execution surface. Deterministic (INV-1) + read-only (INV-2).

Covers plan Tasks 1 (parser), 2 (the four evaluators), 3 (the verb), and 8 (the
hermetic replay of the four falsified premises from the 260714 run + a grounded
control). Stdlib `unittest` only; every KB is a throwaway tempdir (R-LOC-03).

Runner:  python3 -m unittest tests.unit.ops.test_premise_gate -v
"""

import json
import os
import shutil
import tempfile
import unittest

try:
    from tests._fixtures import load_cli, run_cli, build_synthetic_kb
except ImportError:  # `discover -s tests` puts tests/ on sys.path
    from _fixtures import load_cli, run_cli, build_synthetic_kb

M = load_cli()

# Task 39: the premise evaluators moved to agentware_lib.ops.premisecheck, which
# reads CONFIG_PATHS LATE-BOUND through the core.config AGGREGATOR — the documented
# home of the two mutable globals the suite monkeypatches (see
# agentware_lib/core/config/configkeys.py). Rebinding `M.CONFIG_PATHS` alone only
# moves the LEGACY module global and can no longer reach the evaluator, so the
# redirect below sets both. Import-only diff: the assertions are unchanged.
import agentware_lib.core.config as _libconfig   # noqa: E402

MINER_STATE_REL = "logs/steering/.transcript-miner.state.json"


# --- helpers -----------------------------------------------------------------

def _prompts_log(records):
    """records: [(sid, turn, body)] -> a prompts.log chunk in the exact header
    format `_PROMPTS_HEADER_RE` accepts (origin=human)."""
    out = []
    for sid, turn, body in records:
        out.append("[2026-07-10T00:00:00Z] [session %s] [cwd /tmp] "
                   "[origin=human] [turn=%s]" % (sid, turn))
        out.append(body)
        out.append("")
    return "\n".join(out) + "\n"


def _live_jsonl(records):
    return "".join(json.dumps(r) + "\n" for r in records)


def _read(path):
    with open(path, encoding="utf-8") as f:
        return f.read()


def _plan(premises_block, self_ext=True, title="fixture"):
    """A minimal plan carrying a `## Premises` section. verify-premises parses only
    that block, so the rest is skeletal."""
    typ = ("**self-extension (package change)**" if self_ext else "feature work")
    return (
        "# Plan — %s\n\n"
        "> Feature: `test`\n"
        "> Type: %s\n\n"
        "---\n\n"
        "## Premises\n\n"
        "%s\n"
        "---\n\n"
        "## Tasks\n\n"
        "- ⬜ **1** do the thing\n"
        "  *Verify:* `true`\n\n"
        "---\n\n"
        "## Acceptance criteria\n\n"
        "- [ ] it works\n\n"
        "<promise>X</promise>\n" % (title, typ, premises_block))


class PremiseKB(unittest.TestCase):
    """A throwaway synthetic KB with a controllable config.env (so config-eq and
    the miner recurrence gate resolve to KNOWN values, never the operator's real
    machine config — hermetic + deterministic)."""

    def setUp(self):
        self.kdir = tempfile.mkdtemp(prefix="agentware-premise-kb-")
        self.addCleanup(shutil.rmtree, self.kdir, True)
        build_synthetic_kb(self.kdir)
        # Redirect CONFIG_PATHS to a KB-local config so _read_config_key never
        # reads ~/.agentware/config.env. Restored on teardown.
        self._cfg = os.path.join(self.kdir, "config.env")
        with open(self._cfg, "w", encoding="utf-8") as f:
            f.write("")
        self._orig_config_paths = (M.CONFIG_PATHS, _libconfig.CONFIG_PATHS)
        M.CONFIG_PATHS = (self._cfg,)
        _libconfig.CONFIG_PATHS = (self._cfg,)
        self.addCleanup(self._restore_config)

    def _restore_config(self):
        M.CONFIG_PATHS, _libconfig.CONFIG_PATHS = self._orig_config_paths

    def set_config(self, text):
        with open(self._cfg, "w", encoding="utf-8") as f:
            f.write(text)

    def write_plan(self, premises_block, self_ext=True, name="plan.md"):
        path = os.path.join(self.kdir, name)
        with open(path, "w", encoding="utf-8") as f:
            f.write(_plan(premises_block, self_ext=self_ext))
        return path

    def seed_prompts(self, records):
        p = os.path.join(self.kdir, "logs", "prompts.log")
        os.makedirs(os.path.dirname(p), exist_ok=True)
        with open(p, "w", encoding="utf-8") as f:
            f.write(_prompts_log(records))

    def seed_live(self, sid, records):
        d = os.path.join(self.kdir, "logs", "sessions", sid)
        os.makedirs(d, exist_ok=True)
        with open(os.path.join(d, "live.jsonl"), "w", encoding="utf-8") as f:
            f.write(_live_jsonl(records))

    def seed_state_file(self):
        p = os.path.join(self.kdir, MINER_STATE_REL)
        os.makedirs(os.path.dirname(p), exist_ok=True)
        with open(p, "w", encoding="utf-8") as f:
            f.write("{}\n")
        return p

    def verify(self, path, strict=True):
        argv = ["plan", "verify-premises", "--path", path, "--format", "json"]
        if strict:
            argv.append("--strict")
        code, out, err = run_cli(argv, self.kdir)
        try:
            data = json.loads(out)
        except ValueError:
            data = None
        return code, data, out, err


# --- Task 1: parser ----------------------------------------------------------

class TestParser(PremiseKB):

    def test_section_parses_records(self):
        block = ("- premise: alpha holds\n"
                 "  cite: learn-geofence-reminders\n"
                 "  check: cite-live\n"
                 "- premise: beta holds\n"
                 "  cite: config-python-runtime\n"
                 "  check: config-eq FOO bar\n")
        recs = M.plan_premises(_plan(block))
        self.assertEqual(len(recs), 2)
        self.assertEqual(recs[0]["premise"], "alpha holds")
        self.assertEqual(recs[0]["cite"], "learn-geofence-reminders")
        self.assertEqual(recs[0]["parsed"]["kind"], "cite-live")
        self.assertEqual(recs[1]["parsed"]["kind"], "config-eq")

    def test_fenced_block_parses(self):
        text = ("# Plan — x\n\n## Context\n\n```premises\n"
                "- premise: gamma\n  cite: ref-bm25-ranking\n  check: cite-live\n"
                "```\n\n## Tasks\n")
        recs = M.plan_premises(text)
        self.assertEqual(len(recs), 1)
        self.assertEqual(recs[0]["parsed"]["kind"], "cite-live")

    def test_absent_section_returns_empty(self):
        text = "# Plan — x\n\n## Tasks\n\n- ⬜ **1** x\n  *Verify:* y\n"
        self.assertEqual(M.plan_premises(text), [])

    def test_metachar_check_rejected_never_runnable(self):
        # The marquee injection assertion: `; rm -rf /` is a PARSE reject, kind
        # __rejected__, and is never stored as a runnable check.
        block = ("- premise: evil\n  cite: x\n  check: ; rm -rf /\n")
        recs = M.plan_premises(_plan(block))
        self.assertEqual(recs[0]["parsed"]["kind"], "__rejected__")
        for bad in ("$(whoami)", "a | b", "`id`", "a && b"):
            self.assertEqual(M._parse_premise_check(bad)["kind"], "__rejected__",
                             "%r must be rejected" % bad)

    def test_unknown_and_malformed_kinds(self):
        self.assertEqual(M._parse_premise_check("bogus foo")["kind"], "__unknown__")
        self.assertEqual(M._parse_premise_check("config-eq ONLYKEY")["kind"],
                         "__malformed__")
        self.assertEqual(
            M._parse_premise_check("corpus-metric recurrence-keys-at-n ~ 1")["kind"],
            "__malformed__")

    def test_comparison_ops_are_not_metachars(self):
        for op in (">=", "<=", ">", "<", "==", "!="):
            p = M._parse_premise_check("corpus-metric claude-error-records %s 3" % op)
            self.assertEqual(p["kind"], "corpus-metric", "op %s should parse" % op)


# --- Task 2: the four evaluators (deterministic + read-only) ------------------

class TestEvaluators(PremiseKB):

    def test_cite_live_indexed_and_fs_and_missing(self):
        # indexed id
        r = M.evaluate_premise({"cite": "learn-geofence-reminders",
                                "check": "cite-live",
                                "parsed": M._parse_premise_check("cite-live")},
                               self.kdir)
        self.assertEqual(r["verdict"], "grounded")
        # filesystem fallback (a real file under the KB, not indexed)
        os.makedirs(os.path.join(self.kdir, "work", "x"), exist_ok=True)
        with open(os.path.join(self.kdir, "work", "x", "RCA.md"), "w") as f:
            f.write("rca")
        r = M.evaluate_premise({"cite": "work/x/RCA.md", "check": "cite-live",
                                "parsed": M._parse_premise_check("cite-live")},
                               self.kdir)
        self.assertEqual(r["verdict"], "grounded")
        # missing -> uncheckable with the unresolved_cite flag (D5 fuel)
        r = M.evaluate_premise({"cite": "nope-does-not-exist", "check": "cite-live",
                                "parsed": M._parse_premise_check("cite-live")},
                               self.kdir)
        self.assertEqual(r["verdict"], "uncheckable")
        self.assertTrue(r.get("unresolved_cite"))
        # path traversal escaping the KB is refused (unresolvable)
        r = M.evaluate_premise({"cite": "../../etc/passwd", "check": "cite-live",
                                "parsed": M._parse_premise_check("cite-live")},
                               self.kdir)
        self.assertEqual(r["verdict"], "uncheckable")

    def test_config_eq(self):
        self.set_config("AGENTWARE_DREAM=on\n")
        g = M._parse_premise_check("config-eq AGENTWARE_DREAM on")
        self.assertEqual(M._eval_check_config_eq(g)["verdict"], "grounded")
        f = M._parse_premise_check("config-eq AGENTWARE_DREAM off")
        self.assertEqual(M._eval_check_config_eq(f)["verdict"], "falsified")
        u = M._parse_premise_check("config-eq AGENTWARE_NOPE on")
        self.assertEqual(M._eval_check_config_eq(u)["verdict"], "uncheckable")

    def test_path_modes(self):
        self.seed_state_file()
        exists = M._parse_premise_check("path exists %s" % MINER_STATE_REL)
        self.assertEqual(M._eval_check_path(exists, self.kdir)["verdict"], "grounded")
        absent = M._parse_premise_check("path absent %s" % MINER_STATE_REL)
        self.assertEqual(M._eval_check_path(absent, self.kdir)["verdict"], "falsified")
        gone = M._parse_premise_check("path absent logs/nope.json")
        self.assertEqual(M._eval_check_path(gone, self.kdir)["verdict"], "grounded")
        # mtime-before with an author-supplied ISO in the future -> grounded
        fut = M._parse_premise_check("path mtime-before 2099-01-01T00:00:00 %s"
                                     % MINER_STATE_REL)
        self.assertEqual(M._eval_check_path(fut, self.kdir)["verdict"], "grounded")
        past = M._parse_premise_check("path mtime-before 2000-01-01T00:00:00 %s"
                                      % MINER_STATE_REL)
        self.assertEqual(M._eval_check_path(past, self.kdir)["verdict"], "falsified")

    def test_corpus_metric_recurrence(self):
        # a key repeated across 2 distinct sessions -> recurrence-keys-at-n == 1
        body = "always pin dependency versions in every lockfile"
        self.seed_prompts([("s1", "1", body), ("s2", "1", body),
                           ("s3", "1", "a distinct one-off unrelated instruction")])
        self.assertEqual(
            M._premise_corpus_metric_value("recurrence-keys-at-n", self.kdir), 1)
        # no recurrence -> 0 (each body unique)
        self.seed_prompts([("s1", "1", "unique alpha instruction number one here"),
                           ("s2", "1", "unique beta instruction number two here")])
        self.assertEqual(
            M._premise_corpus_metric_value("recurrence-keys-at-n", self.kdir), 0)

    def test_corpus_metric_errors(self):
        self.seed_live("s1", [{"status": "ok"}, {"status": "ERR"},
                              {"status": "error"}])
        self.assertEqual(
            M._premise_corpus_metric_value("claude-error-records", self.kdir), 2)
        self.seed_live("s2", [{"status": "ok"}, {"status": "ok"}])
        self.assertEqual(
            M._premise_corpus_metric_value("claude-error-records", self.kdir), 2)

    def test_determinism_same_state_same_verdict(self):
        body = "always run the full integration suite before merge to main"
        self.seed_prompts([("s1", "1", body), ("s2", "1", body)])
        a = M._premise_corpus_metric_value("recurrence-keys-at-n", self.kdir)
        b = M._premise_corpus_metric_value("recurrence-keys-at-n", self.kdir)
        self.assertEqual(a, b)
        self.assertEqual(a, 1)

    def test_read_only_no_state_mutation(self):
        # INV-2: a corpus-metric eval must not write miner state, prompts, or index.
        body = "always pin dependency versions in every lockfile"
        self.seed_prompts([("s1", "1", body), ("s2", "1", body)])
        state_path = self.seed_state_file()
        state_before = _read(state_path)
        idx_before = _read(os.path.join(self.kdir, "index.json"))
        M._premise_corpus_metric_value("recurrence-keys-at-n", self.kdir)
        M._premise_corpus_metric_value("claude-error-records", self.kdir)
        self.assertEqual(_read(state_path), state_before)
        self.assertEqual(_read(os.path.join(self.kdir, "index.json")), idx_before)


# --- Task 3: the verb --------------------------------------------------------

class TestVerb(PremiseKB):

    def test_grounded_plan_exits_zero(self):
        self.seed_state_file()
        block = ("- premise: rca on file\n  cite: learn-geofence-reminders\n"
                 "  check: cite-live\n"
                 "- premise: miner ran\n  cite: learn-geofence-reminders\n"
                 "  check: path exists %s\n" % MINER_STATE_REL)
        path = self.write_plan(block)
        code, data, out, err = self.verify(path)
        self.assertEqual(code, 0, out + err)
        self.assertTrue(data["ok"])
        self.assertTrue(all(p["verdict"] == "grounded" for p in data["premises"]))

    def test_falsified_plan_exits_one_and_names_premise(self):
        self.set_config("AGENTWARE_DREAM=on\n")
        block = ("- premise: dream is off\n  cite: learn-geofence-reminders\n"
                 "  check: config-eq AGENTWARE_DREAM off\n")
        path = self.write_plan(block)
        code, data, out, err = self.verify(path)
        self.assertEqual(code, 1, out + err)
        self.assertFalse(data["ok"])
        self.assertTrue(data["falsified"])
        p = data["premises"][0]
        self.assertEqual(p["verdict"], "falsified")
        self.assertEqual(p["actual"], "on")     # expected-vs-live delta printed
        self.assertEqual(p["expected"], "off")

    def test_fail_open_on_uncheckable(self):
        # an unknown/uncheckable kind WARNs but never halts (no DoS-the-loop).
        block = ("- premise: mystery\n  cite: x\n  check: totally-bogus-kind z\n")
        path = self.write_plan(block)
        code, data, _, _ = self.verify(path)
        self.assertEqual(code, 0)
        self.assertEqual(data["premises"][0]["verdict"], "uncheckable")

    def test_fail_closed_only_selfext_and_strict(self):
        # D5: an unresolvable citation fails CLOSED only for a self-extension plan
        # AND only under --strict.
        block = ("- premise: cites a ghost\n  cite: ghost-entry-not-real\n"
                 "  check: cite-live\n")
        selfext = self.write_plan(block, self_ext=True, name="se.md")
        self.assertEqual(self.verify(selfext, strict=True)[0], 1)
        self.assertEqual(self.verify(selfext, strict=False)[0], 0)
        nonself = self.write_plan(block, self_ext=False, name="ns.md")
        self.assertEqual(self.verify(nonself, strict=True)[0], 0)

    def test_no_premises_is_noop_exit_zero(self):
        path = os.path.join(self.kdir, "trivial.md")
        with open(path, "w") as f:
            f.write("# Plan — trivial\n\n## Tasks\n\n- ⬜ **1** x\n"
                    "  *Verify:* y\n")
        code, data, _, _ = self.verify(path)
        self.assertEqual(code, 0)
        self.assertEqual(data["premises"], [])

    def test_verb_is_read_only(self):
        self.seed_state_file()
        self.seed_prompts([("s1", "1", "always pin dependency versions here now"),
                           ("s2", "1", "always pin dependency versions here now")])
        block = ("- premise: recurs\n  cite: x\n"
                 "  check: corpus-metric recurrence-keys-at-n >= 1\n")
        path = self.write_plan(block)
        idx_before = _read(os.path.join(self.kdir, "index.json"))
        plan_before = _read(path)
        self.verify(path)
        self.assertEqual(_read(os.path.join(self.kdir, "index.json")), idx_before)
        self.assertEqual(_read(path), plan_before)


# --- Task 8: hermetic replay of the four 260714 false premises + control ------

class TestFourFalsePremises(PremiseKB):

    def test_1_exact_vs_fuzzy_recurrence_reproduces_zero(self):
        # PoC assumed recurrence fires; live exact-match reproduces 0.
        self.seed_prompts([("s1", "1", "one distinct instruction alpha here now"),
                           ("s2", "1", "another distinct instruction beta here")])
        block = ("- premise: recurrence detection delivers >=1 real hit\n"
                 "  cite: learn-geofence-reminders\n"
                 "  check: corpus-metric recurrence-keys-at-n >= 1\n")
        code, data, out, _ = self.verify(self.write_plan(block))
        self.assertEqual(code, 1, out)
        self.assertEqual(data["premises"][0]["verdict"], "falsified")
        self.assertEqual(data["premises"][0]["actual"], 0)

    def test_2_failure_signal_absent(self):
        self.seed_live("s1", [{"status": "ok"}, {"status": "ok"}])
        block = ("- premise: the Claude failure signal is present in the corpus\n"
                 "  cite: learn-geofence-reminders\n"
                 "  check: corpus-metric claude-error-records > 0\n")
        code, data, out, _ = self.verify(self.write_plan(block))
        self.assertEqual(code, 1, out)
        self.assertEqual(data["premises"][0]["verdict"], "falsified")
        self.assertEqual(data["premises"][0]["actual"], 0)

    def test_3_recurrence_value_zero_true_positives(self):
        # marquee: the recurrence arm's value premise reproduces 0.
        self.seed_prompts([("s1", "1", "reply with exactly probe ok token value")])
        block = ("- premise: recurrence produced true positives worth shipping\n"
                 "  cite: learn-geofence-reminders\n"
                 "  check: corpus-metric recurrence-keys-at-n >= 1\n")
        code, data, out, _ = self.verify(self.write_plan(block))
        self.assertEqual(code, 1, out)
        self.assertEqual(data["premises"][0]["verdict"], "falsified")

    def test_4_dream_off_miner_never_ran(self):
        # Two independent checks both falsify: dream is ON (not off) AND the miner
        # state file EXISTS (so `path absent` fails).
        self.set_config("AGENTWARE_DREAM=on\n")
        self.seed_state_file()
        block = ("- premise: dream is off\n  cite: learn-geofence-reminders\n"
                 "  check: config-eq AGENTWARE_DREAM off\n"
                 "- premise: the miner never ran (no state file)\n"
                 "  cite: learn-geofence-reminders\n"
                 "  check: path absent %s\n" % MINER_STATE_REL)
        code, data, out, _ = self.verify(self.write_plan(block))
        self.assertEqual(code, 1, out)
        self.assertTrue(all(p["verdict"] == "falsified" for p in data["premises"]))

    def test_grounded_control_exits_zero(self):
        self.set_config("AGENTWARE_DREAM=on\n")
        self.seed_state_file()
        body = "always pin dependency versions in every lockfile for safety"
        self.seed_prompts([("s1", "1", body), ("s2", "1", body)])
        self.seed_live("s1", [{"status": "ERR"}])
        block = ("- premise: recurrence corpus has >=1 key\n"
                 "  cite: learn-geofence-reminders\n"
                 "  check: corpus-metric recurrence-keys-at-n >= 1\n"
                 "- premise: error records exist\n"
                 "  cite: learn-geofence-reminders\n"
                 "  check: corpus-metric claude-error-records > 0\n"
                 "- premise: dream is on\n  cite: learn-geofence-reminders\n"
                 "  check: config-eq AGENTWARE_DREAM on\n"
                 "- premise: miner state present\n"
                 "  cite: learn-geofence-reminders\n"
                 "  check: path exists %s\n" % MINER_STATE_REL)
        code, data, out, _ = self.verify(self.write_plan(block))
        self.assertEqual(code, 0, out)
        self.assertTrue(all(p["verdict"] == "grounded" for p in data["premises"]))

    def test_falsified_verdict_is_byte_stable(self):
        self.seed_prompts([("s1", "1", "one distinct instruction alpha here now"),
                           ("s2", "1", "another distinct instruction beta here")])
        block = ("- premise: recurrence fires\n  cite: learn-geofence-reminders\n"
                 "  check: corpus-metric recurrence-keys-at-n >= 1\n")
        path = self.write_plan(block)
        _, _, out1, _ = self.verify(path)
        _, _, out2, _ = self.verify(path)
        self.assertEqual(out1, out2)   # INV-1: same state -> byte-identical output


# --- Adversarial-review fixes (regression locks) -----------------------------

class TestReviewFixes(PremiseKB):

    def test_empty_section_does_not_suppress_populated_fence(self):
        # An EMPTY `## Premises` heading must not hide a populated ```premises fence.
        text = ("# Plan — x\n\n## Premises\n\n(intentionally empty)\n\n"
                "## Context\n\n```premises\n"
                "- premise: real one\n  cite: learn-geofence-reminders\n"
                "  check: cite-live\n```\n\n## Tasks\n")
        recs = M.plan_premises(text)
        self.assertEqual(len(recs), 1)
        self.assertEqual(recs[0]["premise"], "real one")

    def test_missing_prompts_log_is_uncheckable_not_zero(self):
        # logs/ present but prompts.log absent -> None (fail-open), never 0.
        os.makedirs(os.path.join(self.kdir, "logs"), exist_ok=True)
        self.assertIsNone(
            M._premise_corpus_metric_value("recurrence-keys-at-n", self.kdir))

    def test_no_live_jsonl_is_uncheckable_not_zero(self):
        os.makedirs(os.path.join(self.kdir, "logs", "sessions"), exist_ok=True)
        self.assertIsNone(
            M._premise_corpus_metric_value("claude-error-records", self.kdir))

    def test_corpus_metric_missing_source_fails_open_in_verb(self):
        # A self-extension plan whose corpus source rotated away must WARN (exit 0),
        # never DoS the loop (D5 fail-open).
        os.makedirs(os.path.join(self.kdir, "logs"), exist_ok=True)
        block = ("- premise: recurrence was >=1 at authoring\n"
                 "  cite: learn-geofence-reminders\n"
                 "  check: corpus-metric recurrence-keys-at-n >= 1\n")
        code, data, out, _ = self.verify(self.write_plan(block))
        self.assertEqual(code, 0, out)
        self.assertEqual(data["premises"][0]["verdict"], "uncheckable")

    def test_verify_premises_never_rebuilds_index_inv2(self):
        # INV-2: a missing index.json must NOT be rebuilt by the read-only verb.
        idx = os.path.join(self.kdir, "index.json")
        os.remove(idx)
        block = ("- premise: rca on file\n  cite: learnings/geofence-reminders.md\n"
                 "  check: cite-live\n")
        code, data, out, _ = self.verify(self.write_plan(block))
        self.assertFalse(os.path.exists(idx),
                         "verify-premises must not rebuild index.json (INV-2)")
        # cite still resolves via the filesystem fallback (grounded), no rebuild.
        self.assertEqual(data["premises"][0]["verdict"], "grounded")

    @unittest.skipIf(hasattr(os, "geteuid") and os.geteuid() == 0,
                     "root bypasses permission checks")
    def test_unreadable_path_is_uncheckable_not_falsified(self):
        # A permission error on an ancestor must yield uncheckable, not a false
        # 'falsified' (os.path.exists would swallow it to False).
        sub = os.path.join(self.kdir, "locked")
        os.makedirs(sub, exist_ok=True)
        with open(os.path.join(sub, "target"), "w") as f:
            f.write("x")
        os.chmod(sub, 0)
        self.addCleanup(os.chmod, sub, 0o755)
        parsed = M._parse_premise_check("path exists locked/target")
        self.assertEqual(M._eval_check_path(parsed, self.kdir)["verdict"],
                         "uncheckable")


if __name__ == "__main__":
    unittest.main()
