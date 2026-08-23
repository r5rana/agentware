"""`cmd_learn` / `cmd_decide` end to end — task 99 wave 13, part two.

The second half of `features/curationcmd`'s owning unit file (`_curationcmd_
support.py` carries the fixture; `test_curationcmd.py` carries the four shared
input contracts). Split at the 600-line corpus cap along a class boundary.

Wave 13 lifted the two handlers' duplicated write chain into
`features/curationwrite`. That module's own file proves the chain; these tests
prove the two CALLERS still assemble it the way each verb's contract requires,
which is the half no unit test of the extracted helpers can see:

  LOCK SPAN   — `cmd_learn` emits AFTER releasing the index lock; `cmd_decide`
                emits INSIDE it. Pre-existing on both sides of the merge, and
                the two are pinned as an ordered event log rather than left to
                drift, because a refactor that "tidied" one to match the other
                would change how long a global lock is held and nothing else
                in the suite would notice.
  ORDER       — `cmd_learn` validates its topic BEFORE taking the lock;
                `cmd_decide` takes the lock first. Both orders are asserted
                through their observable consequence, not read off the source.
  NO PARTIAL  — every refusal is asserted to leave the index AND the learnings
                directory exactly as it found them. "It returned 1" is a
                weaker claim than "it wrote nothing", and only the second one
                is what the operator cares about.
  TAG RULE    — `decide` prepends `decision` when absent and does not duplicate
                it when present. Three fixtures, because the two arms plus the
                no-tags default are three different paths through one line.
"""

from tests._paths import TESTS_DIR                             # noqa: F401

import argparse
import contextlib
import json
import os
import unittest

try:
    from tests.unit.features._curationcmd_support import (
        ScratchKB, decide_args, learn_args, run)
except ImportError:  # pragma: no cover - direct-run fallback
    from _curationcmd_support import ScratchKB, decide_args, learn_args, run

import agentware_lib.features.curationcmd as curationcmd       # noqa: E402
import agentware_lib.knowledge.index as index                  # noqa: E402
from agentware_lib.features.curationcmd import (               # noqa: E402
    cmd_decide, cmd_learn)


class _LockSpy(ScratchKB):
    """Records an ordered event log of lock enter / emit / lock exit."""

    def _events_for(self, fn, args):
        events = []
        real_lock = index._index_lock
        real_emit = curationcmd._emit_curation_entry

        @contextlib.contextmanager
        def _spy(kdir):
            events.append("enter")
            with real_lock(kdir):
                yield
            events.append("exit")

        def _emit(*a, **k):
            events.append("emit")
            return real_emit(*a, **k)

        index._index_lock = _spy
        curationcmd._index_lock = _spy
        curationcmd._emit_curation_entry = _emit
        try:
            rc, _out, _err = run(fn, args)
        finally:
            index._index_lock = real_lock
            curationcmd._index_lock = real_lock
            curationcmd._emit_curation_entry = real_emit
        self.assertEqual(rc, 0)
        return events


class CmdLearnTest(ScratchKB):
    """The learning half, through the seams the split left in place."""

    def test_a_clean_write_registers_and_reports(self):
        rc, out, err = run(cmd_learn, learn_args())
        self.assertEqual((rc, err), (0, ""))
        self.assertIn("LEARNED entry 'learn-a-fresh-learning' -> ", out)
        self.assertIn("learn-a-fresh-learning", self.ids())

    def test_the_written_file_carries_the_derived_title(self):
        run(cmd_learn, learn_args(topic="iOS Signing Note"))
        path = os.path.join(self.kdir, "learnings", "ios-signing-note.md")
        with open(path, encoding="utf-8") as f:
            text = f.read()
        self.assertIn("title: Ios Signing Note", text)

    def test_an_explicit_title_reaches_the_file(self):
        run(cmd_learn, learn_args(title="A Chosen Title"))
        path = os.path.join(self.kdir, "learnings", "a-fresh-learning.md")
        with open(path, encoding="utf-8") as f:
            self.assertIn("title: A Chosen Title", f.read())

    def test_the_source_flag_reaches_the_frontmatter(self):
        run(cmd_learn, learn_args(source="user"))
        path = os.path.join(self.kdir, "learnings", "a-fresh-learning.md")
        with open(path, encoding="utf-8") as f:
            self.assertIn("source: user", f.read())

    def test_a_duplicate_id_refuses_and_changes_nothing(self):
        run(cmd_learn, learn_args())
        before = json.dumps(self.index(), sort_keys=True)
        rc, _out, err = run(cmd_learn, learn_args(
            content="An entirely different body about tax forms."))
        self.assertEqual(rc, 1)
        self.assertIn("duplicate id 'learn-a-fresh-learning'", err)
        self.assertEqual(json.dumps(self.index(), sort_keys=True), before)

    def test_an_unindexed_file_on_disk_is_never_overwritten(self):
        path = os.path.join(self.kdir, "learnings", "a-fresh-learning.md")
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            f.write("hand-written, unindexed\n")
        rc, _out, err = run(cmd_learn, learn_args())
        self.assertEqual(rc, 1)
        self.assertIn("learning file already exists", err)
        with open(path, encoding="utf-8") as f:
            self.assertEqual(f.read(), "hand-written, unindexed\n")

    def test_the_json_arm_reports_the_registered_entry(self):
        rc, out, _err = run(cmd_learn, learn_args(format="json"))
        payload = json.loads(out)
        self.assertEqual(rc, 0)
        self.assertEqual(payload["learning"]["id"], "learn-a-fresh-learning")
        self.assertIs(payload["ok"], True)

    def test_a_bad_topic_refuses_before_the_index_is_touched(self):
        before = json.dumps(self.index(), sort_keys=True)
        rc, _out, _err = run(cmd_learn, learn_args(topic="***"))
        self.assertEqual(rc, 1)
        self.assertEqual(json.dumps(self.index(), sort_keys=True), before)

    def test_a_bad_source_refuses_before_the_file_is_written(self):
        rc, _out, _err = run(cmd_learn, learn_args(source="hearsay"))
        self.assertEqual(rc, 1)
        self.assertFalse(os.path.exists(os.path.join(
            self.kdir, "learnings", "a-fresh-learning.md")))

    def test_a_bad_author_refuses_before_the_file_is_written(self):
        rc, _out, err = run(cmd_learn, learn_args(author="!!!"))
        self.assertEqual(rc, 1)
        self.assertIn("invalid --author", err)
        self.assertFalse(os.path.exists(os.path.join(
            self.kdir, "learnings", "a-fresh-learning.md")))

    def test_empty_content_refuses_before_anything_is_resolved(self):
        rc, _out, err = run(cmd_learn, learn_args(content="  "))
        self.assertEqual(rc, 1)
        self.assertEqual(err, "agentware: --content must not be empty\n")

    def test_a_topic_is_validated_BEFORE_the_lock_is_taken(self):
        """The counterpart of `cmd_decide`'s order test. Driven by a tripwire
        lock that RAISES: a handler that took the lock first would raise here
        instead of refusing, which no return-code assertion could tell apart."""
        real_lock = index._index_lock

        def _tripwire(kdir):
            raise AssertionError("the lock was taken before the topic check")

        index._index_lock = _tripwire
        curationcmd._index_lock = _tripwire
        try:
            rc, _out, err = run(cmd_learn, learn_args(topic="***"))
        finally:
            index._index_lock = real_lock
            curationcmd._index_lock = real_lock
        self.assertEqual(rc, 1)
        self.assertIn("has no slug-safe characters", err)


class CmdDecideTest(ScratchKB):
    """The decision half: its own tag rule, its own ordering."""

    def test_a_clean_write_registers_and_reports(self):
        rc, out, err = run(cmd_decide, decide_args())
        self.assertEqual((rc, err), (0, ""))
        self.assertIn("DECISION entry 'decide-a-fresh-decision' -> ", out)
        self.assertIn("decide-a-fresh-decision", self.ids())

    def test_the_rendered_body_carries_the_four_decision_fields(self):
        run(cmd_decide, decide_args(options="(a) keep; (b) drop",
                                    choice="(a) keep",
                                    rationale="Cheaper to keep.",
                                    reversible="Reversible in a day."))
        path = os.path.join(self.kdir, "decisions", "a-fresh-decision.md")
        with open(path, encoding="utf-8") as f:
            text = f.read()
        for needle in ("(a) keep; (b) drop", "(a) keep", "Cheaper to keep.",
                       "Reversible in a day."):
            self.assertIn(needle, text)

    def test_the_decision_tag_is_prepended_when_absent(self):
        run(cmd_decide, decide_args(tags="alpha,beta"))
        self.assertEqual(self.row("decide-a-fresh-decision")["tags"],
                         ["decision", "alpha", "beta"])

    def test_the_decision_tag_is_not_duplicated_when_present(self):
        run(cmd_decide, decide_args(tags="decision,alpha"))
        self.assertEqual(self.row("decide-a-fresh-decision")["tags"],
                         ["decision", "alpha"])

    def test_no_tags_still_yields_the_decision_tag(self):
        run(cmd_decide, decide_args(tags=None))
        self.assertEqual(self.row("decide-a-fresh-decision")["tags"],
                         ["decision"])

    def test_a_duplicate_id_refuses_and_changes_nothing(self):
        run(cmd_decide, decide_args())
        before = json.dumps(self.index(), sort_keys=True)
        rc, _out, err = run(cmd_decide, decide_args(rationale="Other."))
        self.assertEqual(rc, 1)
        self.assertIn("duplicate id 'decide-a-fresh-decision'", err)
        self.assertEqual(json.dumps(self.index(), sort_keys=True), before)

    def test_an_unindexed_file_on_disk_is_never_overwritten(self):
        path = os.path.join(self.kdir, "decisions", "a-fresh-decision.md")
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            f.write("hand-written, unindexed\n")
        rc, _out, err = run(cmd_decide, decide_args())
        self.assertEqual(rc, 1)
        self.assertIn("decision file already exists", err)

    def test_the_json_arm_reports_the_registered_entry(self):
        rc, out, _err = run(cmd_decide, decide_args(format="json"))
        payload = json.loads(out)
        self.assertEqual(rc, 0)
        self.assertEqual(payload["decision"]["id"], "decide-a-fresh-decision")

    def test_a_malformed_author_still_records_the_decision(self):
        """The asymmetry with `learn`, asserted where it MATTERS: the loop
        promotes `> DECISION:` markers unattended, so a bad handle must cost
        the handle and not the decision."""
        rc, _out, _err = run(cmd_decide, decide_args(author="!!!"))
        self.assertEqual(rc, 0)
        self.assertIn("decide-a-fresh-decision", self.ids())

    def test_the_lock_is_taken_BEFORE_the_topic_is_validated(self):
        """Pre-existing order, pinned rather than left to drift: `decide`
        loads the index before it validates the topic. Driven with a tripwire
        lock so the ORDER is what fails, not the return code."""
        real_lock = index._index_lock
        taken = []

        @contextlib.contextmanager
        def _spy(kdir):
            taken.append(kdir)
            with real_lock(kdir):
                yield

        index._index_lock = _spy
        curationcmd._index_lock = _spy
        try:
            rc, _out, err = run(cmd_decide, decide_args(topic="###"))
        finally:
            index._index_lock = real_lock
            curationcmd._index_lock = real_lock
        self.assertEqual(rc, 1)
        self.assertIn("has no slug-safe characters", err)
        self.assertEqual(taken, [self.kdir])


class EmitAndLockSpanTest(_LockSpy):
    """The two handlers really do differ here, and both are pinned."""

    def test_learn_emits_AFTER_releasing_the_lock(self):
        self.assertEqual(self._events_for(cmd_learn, learn_args()),
                         ["enter", "exit", "emit"])

    def test_decide_emits_INSIDE_the_lock(self):
        self.assertEqual(self._events_for(cmd_decide, decide_args()),
                         ["enter", "emit", "exit"])


class VerbDeclarationTest(unittest.TestCase):
    """The registrars travel with the handlers."""

    def test_both_verbs_are_declared_here(self):
        self.assertEqual(curationcmd.__verbs__,
                         (("learn", 900, "register_learn"),
                          ("decide", 1000, "register_decide")))

    def test_the_registrars_bind_their_handlers(self):
        parser = argparse.ArgumentParser()
        fmt = argparse.ArgumentParser(add_help=False)
        fmt.add_argument("--format", default="text")
        sub = parser.add_subparsers()
        curationcmd.register_learn(sub, fmt)
        curationcmd.register_decide(sub, fmt)
        learn = parser.parse_args(["learn", "--topic", "t", "--summary", "s",
                                   "--tags", "a", "--content", "c"])
        decide = parser.parse_args(["decide", "--topic", "t", "--summary", "s",
                                    "--options", "o", "--choice", "c",
                                    "--rationale", "r", "--reversible", "y"])
        self.assertIs(learn.func, cmd_learn)
        self.assertIs(decide.func, cmd_decide)

    def test_only_learn_carries_a_title_flag(self):
        """`_curation_title`'s override is `learn`-only; a `--title` that
        appeared on `decide` would be parsed and silently ignored."""
        parser = argparse.ArgumentParser()
        fmt = argparse.ArgumentParser(add_help=False)
        fmt.add_argument("--format", default="text")
        sub = parser.add_subparsers()
        curationcmd.register_learn(sub, fmt)
        curationcmd.register_decide(sub, fmt)
        self.assertTrue(hasattr(
            parser.parse_args(["learn", "--topic", "t", "--summary", "s",
                               "--tags", "a", "--content", "c"]), "title"))
        self.assertFalse(hasattr(
            parser.parse_args(["decide", "--topic", "t", "--summary", "s",
                               "--options", "o", "--choice", "c",
                               "--rationale", "r", "--reversible", "y"]),
            "title"))

    def test_the_facade_exports_the_two_handlers(self):
        self.assertEqual(curationcmd._EXPORTS, ["cmd_learn", "cmd_decide"])


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
