"""Tests for the operator-turn provenance precision gate (feature
260719-prefer-precision-and-reject, Task 2).

This session's false positives were preference CUES that appeared only inside text
the operator QUOTED BACK from the agent as markdown blockquotes (`>`-led lines).
The shared `_pref_operator_text` gate strips those quote-backs before the cue is
evaluated, wired into BOTH the classifier (`_pref_is_capturable`) and the Stop-hook
producer (`_prefer_queue_scan`). TIGHTEN-ONLY: it can only reject more, never
capture more; deterministic/offline (INV-1).
"""

import json
import os
import shutil
import tempfile
import unittest

try:
    from tests._fixtures import load_cli, build_synthetic_kb
except ImportError:  # allow `python3 -m unittest tests.unit.ops.test_prefer_precision`
    from _fixtures import load_cli, build_synthetic_kb

CLI = load_cli()

# The exact marquee false positive from this session (a `>`-quoted agent line whose
# cue word `never` is NOT the operator's own instruction).
QUOTED_NOISE = "> But you should know now that the honest answer is never simple"
STATEMENT_QUESTION = "i dont understand what this is"
CLI_BANNER = "▐▛███▜▌  Claude Code v2.1.210  Opus 4.8"
GENUINE_PREF = "always pin dependency versions in the lockfile"


class TestOperatorTextResidual(unittest.TestCase):
    def test_strips_blockquote_lines(self):
        self.assertEqual(CLI._pref_operator_text("> quoted\nreal line"), "real line")

    def test_all_blockquote_yields_empty(self):
        self.assertEqual(CLI._pref_operator_text("> a\n>  b\n>c"), "")

    def test_leading_space_before_marker_still_stripped(self):
        self.assertEqual(CLI._pref_operator_text("   > quoted\nkept"), "kept")

    def test_no_blockquote_is_unchanged_modulo_strip(self):
        self.assertEqual(CLI._pref_operator_text("always pin versions"),
                         "always pin versions")

    def test_non_str_is_empty(self):
        self.assertEqual(CLI._pref_operator_text(None), "")
        self.assertEqual(CLI._pref_operator_text(123), "")


class TestIsCapturableGate(unittest.TestCase):
    def test_quoted_cue_is_not_capturable(self):
        # cue `never` lives only inside the `>`-blockquote -> not the operator's pref
        self.assertFalse(CLI._pref_is_capturable(QUOTED_NOISE))

    def test_no_cue_confusion_is_not_capturable(self):
        # a statement of confusion has no preference cue -> not capturable (handled
        # by the cue check, NOT an over-reaching leading-word guard).
        self.assertFalse(CLI._pref_is_capturable(STATEMENT_QUESTION))

    def test_cli_banner_is_not_capturable(self):
        self.assertFalse(CLI._pref_is_capturable(CLI_BANNER))

    def test_genuine_operator_preference_is_capturable(self):
        self.assertTrue(CLI._pref_is_capturable(GENUINE_PREF))

    def test_wh_or_temporal_prefixed_prefs_still_capture(self):
        # REGRESSION GUARD (#4/#10): a genuine preference that OPENS with a temporal/
        # relative/wh word (when/where/which/how/what) must still capture — the gate
        # must not silently drop it as a "question".
        for pref in ("When merging, always run the full linter",
                     "Where possible, always prefer pnpm over npm",
                     "Which manager we use — always pin the exact versions"):
            self.assertTrue(CLI._pref_is_capturable(pref), pref)

    def test_directive_after_a_question_or_confusion_opener_still_captures(self):
        # REGRESSION GUARD (round-2 #2): a turn that OPENS with a question / confusion
        # clause but STATES a strong directive must still capture — the guard must
        # not swallow the whole turn just because it starts with "Can we…"/"Do you…".
        for pref in ("Can we always use spaces going forward",
                     "Do you rebuild on every save? From now on, always run the linter",
                     "i'm not sure we need it, but from now on always run the linter"):
            self.assertTrue(CLI._pref_is_capturable(pref), pref)
        # a confusion/question with NO strong directive stays non-capturable
        self.assertFalse(CLI._pref_is_capturable("i dont understand what this is"))

    def test_operator_cue_survives_even_when_quoting_agent(self):
        # the operator DID author the cue (line 1); a quoted agent line follows.
        mixed = "never use open version ranges\n> the agent replied: always pin"
        self.assertTrue(CLI._pref_is_capturable(mixed))

    def test_tighten_only_trailing_quoted_question_stays_rejected(self):
        # #5/#8: the residual gate is ADDITIVE — it must NOT flip a turn the base
        # bar rejects (the FULL text ends with '?') into a capture just because the
        # '?' lives on a stripped `>`-line.
        self.assertFalse(CLI._pref_is_capturable(
            "always pin versions\n> did the agent actually do it?"))

    def test_gate_truth_table(self):
        # explicit expected values (not a vacuous f(x)==f(x)) — the deterministic
        # contract for fixed inputs.
        self.assertFalse(CLI._pref_is_capturable(QUOTED_NOISE))
        self.assertFalse(CLI._pref_is_capturable(STATEMENT_QUESTION))
        self.assertFalse(CLI._pref_is_capturable(CLI_BANNER))
        self.assertTrue(CLI._pref_is_capturable(GENUINE_PREF))


def _rec(ts, sid, cwd, origin, turn, prompt):
    return ("[%s] [session %s] [cwd %s] [origin=%s] [turn=%s]\n%s\n\n"
            % (ts, sid, cwd, origin, turn, prompt))


class TestProducerHonorsGate(unittest.TestCase):
    """The Stop-hook producer `_prefer_queue_scan` must not enqueue a cue that lives
    only in quoted-back text, while still enqueuing a genuine operator preference."""

    def setUp(self):
        self.kdir = tempfile.mkdtemp(prefix="aw_prefer_precision_")
        self.addCleanup(shutil.rmtree, self.kdir, True)
        build_synthetic_kb(self.kdir)
        self.mod = load_cli()
        self.sid = "sess-AAAA"
        os.makedirs(os.path.join(self.kdir, "logs"), exist_ok=True)
        self.logp = os.path.join(self.kdir, "logs", "prompts.log")
        self.qpath = os.path.join(self.kdir, self.mod.PREFER_QUEUE_REL)

    def _write_log(self, records):
        with open(self.logp, "w", encoding="utf-8") as f:
            f.write("".join(records))

    def _queue(self):
        if not os.path.isfile(self.qpath):
            return []
        with open(self.qpath, encoding="utf-8") as f:
            return [json.loads(ln) for ln in f if ln.strip()]

    def test_quoted_back_cue_turn_is_not_enqueued(self):
        self._write_log([_rec("2026-07-13T00:00:00Z", self.sid, "/tmp/x",
                              "human", "t1",
                              "> the agent said we should never use ranges")])
        n = self.mod._prefer_queue_scan(self.kdir, self.sid, "",
                                        prompts_path=self.logp)
        self.assertEqual(n, 0, "a `>`-quoted-only cue must not enqueue")
        self.assertEqual(self._queue(), [])

    def test_genuine_preference_turn_is_still_enqueued(self):
        self._write_log([_rec("2026-07-13T00:00:00Z", self.sid, "/tmp/x",
                              "human", "t1",
                              "Always pin dependency versions, never use ranges.")])
        n = self.mod._prefer_queue_scan(self.kdir, self.sid, "",
                                        prompts_path=self.logp)
        self.assertEqual(n, 1, "a genuine operator preference must still enqueue")
        self.assertEqual(len(self._queue()), 1)


if __name__ == "__main__":
    unittest.main()
