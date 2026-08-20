"""Task-83 suite for the WRITE-SIDE neutrality redactor.

The redactor's whole claim is that it cannot disagree with the scanner that
grades it, so most of this suite is about that seam rather than about string
replacement. Three things it is careful about:

**Nothing here uses the private needle set.** Every probe brings its OWN needle
file with a token invented for the test, so the suite runs identically on a
machine with no `.aw-migration-safety` directory and on CI. The two tests that
must speak about the REAL captures assert a property that holds either way.

**The placeholder's opacity is asserted, not assumed.** Collapsing every needle
to one `<neutralised>` was a decision with a stated alternative (a truncated
digest), and a decision nobody tests is a decision the next author will reverse
by accident. `test_two_different_needles_are_indistinguishable_after_redaction`
is that decision written down as a red-if-reversed test.

**Every fail-loud path is exercised as a fail.** A redactor that silently
returns its input when it cannot resolve needles is worse than no redactor: it
produces a clean-looking artifact and a green check over an unscanned file.
"""

from tests._paths import TESTS_DIR

import ast
import io
import json
import os
import shutil
import sys
import tempfile
import unittest
from unittest import mock

try:
    from tests import _neutrality_redact as nr
    from tests import _portability_gate as pg
except ImportError:  # pragma: no cover - direct-script invocation
    import _neutrality_redact as nr
    import _portability_gate as pg

HERE = TESTS_DIR
REPO = os.path.dirname(HERE)

#: Invented tokens. They are ordinary words that appear nowhere else in the
#: tree, so a probe cannot accidentally pass because the real needle set
#: happens to contain something similar.
TOK_A = "zibbleworks"
TOK_B = "quorndrive"


def unresolvable_needles():
    """Simulate a machine where the private `environment` file does not exist.

    Patched at `resolve_needle_file` rather than by passing a non-existent
    ``--needles`` path, because that path is a CANDIDATE and not a demand: the
    gate's resolver falls through to the knowledge-dir and workspace locations,
    so on this operator's machine an "absent" explicit path silently resolves to
    the real private file and the probe would test nothing. (That fall-through is
    a task-12 gate behaviour and a finding recorded for the task-84 review; it is
    not this suite's to change.)
    """
    return mock.patch.object(nr.gate, "resolve_needle_file",
                             return_value=None)


def needle_file(directory, tokens=(TOK_A, TOK_B), match=pg.SUBSTRING):
    path = os.path.join(directory, "needles.json")
    with open(path, "w", encoding="utf-8") as handle:
        json.dump({"schema": pg.NEEDLE_SCHEMA,
                   "needles": [{"token": t, "match": match,
                                "hint": "synthetic test needle", "exempt": []}
                               for t in tokens]}, handle)
    return path


class RedactTextTest(unittest.TestCase):

    """The pure transform: what it replaces, and what it refuses to tell you."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="aw-nrtxt-")
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self.compiled, self.meta = nr.load_compiled(needle_file(self.tmp))

    def test_a_needle_is_replaced_by_the_placeholder(self):
        out = nr.redact_text("build with %s today" % TOK_A, self.compiled)
        self.assertEqual(out, "build with %s today" % nr.PLACEHOLDER)

    def test_matching_is_case_insensitive_like_the_scan_that_grades_it(self):
        """A token and its capitalised spelling are ONE needle to the gate.

        A case-sensitive redactor cleans the spelling the author happened to
        write and leaves the one the gate reports, which is the exact shape of
        "cleaned against a narrower rule than it is graded by".
        """
        out = nr.redact_text(TOK_A.capitalize() + " and " + TOK_A.upper(),
                             self.compiled)
        self.assertEqual(out, "%s and %s" % (nr.PLACEHOLDER, nr.PLACEHOLDER))

    def test_every_occurrence_goes_not_just_the_first(self):
        out = nr.redact_text(" ".join([TOK_A] * 5), self.compiled)
        self.assertEqual(out.count(nr.PLACEHOLDER), 5)
        self.assertNotIn(TOK_A, out)

    def test_surrounding_text_survives_byte_for_byte(self):
        """Only the token moves; the row keeps its shape, which is the point."""
        out = nr.redact_text("test_dir_is_%s_package" % TOK_A, self.compiled)
        self.assertEqual(out, "test_dir_is_%s_package" % nr.PLACEHOLDER)

    def test_redaction_is_idempotent(self):
        once = nr.redact_text("a %s b %s c" % (TOK_A, TOK_B), self.compiled)
        self.assertEqual(nr.redact_text(once, self.compiled), once)

    def test_the_placeholder_is_not_itself_a_needle(self):
        """Idempotence is only free while the placeholder is clean; assert it."""
        found = pg.scan_text(nr.PLACEHOLDER, self.compiled, pg.CONTENTS, "x")
        self.assertEqual(found, [])

    def test_two_different_needles_are_indistinguishable_after_redaction(self):
        """THE DECISION, as a test.

        A per-token digest would keep these distinguishable -- and would leak,
        because a truncated SHA-256 of a short dictionary word is confirmable by
        anyone who guesses it. If a future author reintroduces a distinguishing
        suffix, this reddens and they have to argue with the reason rather than
        with nothing.
        """
        self.assertEqual(nr.redact_text(TOK_A, self.compiled),
                         nr.redact_text(TOK_B, self.compiled))

    def test_falsy_input_is_returned_unchanged(self):
        for value in ("", None):
            self.assertEqual(nr.redact_text(value, self.compiled), value)

    def test_a_clean_string_is_returned_unchanged(self):
        self.assertEqual(nr.redact_text("nothing to see", self.compiled),
                         "nothing to see")

    def test_word_match_needles_respect_their_boundary(self):
        """The gate compiles `word` needles with a boundary; so must this.

        A redactor that treated every needle as a substring would over-redact a
        `word` needle -- destroying rows the gate never objected to.
        """
        compiled, _ = nr.load_compiled(
            needle_file(self.tmp, (TOK_A,), pg.WORD))
        self.assertEqual(nr.redact_text(TOK_A + "ing", compiled),
                         TOK_A + "ing")
        self.assertEqual(nr.redact_text("a " + TOK_A + " b", compiled),
                         "a %s b" % nr.PLACEHOLDER)


class SharedNeedleSourceTest(unittest.TestCase):

    """The seam that makes the redactor trustworthy: ONE definition of a token."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="aw-nrsrc-")
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)

    def test_a_needle_added_to_the_private_file_is_seen_by_both_sides(self):
        """Behavioural, not structural: add a token, both sides must move.

        This is the property the docstring claims. It is asserted by ADDING a
        needle nobody hardcoded and demanding that the scanner reports it and
        the redactor removes it -- so a redactor that grew its own private list
        would fail here even though it imported the same module.
        """
        path = needle_file(self.tmp, ("neverbeforeseen",))
        compiled, _ = nr.load_compiled(path)
        self.assertTrue(pg.scan_text("x neverbeforeseen y", compiled,
                                     pg.CONTENTS, "f"))
        self.assertNotIn("neverbeforeseen",
                         nr.redact_text("x neverbeforeseen y", compiled))

    def test_the_zero_tolerance_class_is_redacted_without_any_needle_file(self):
        """The builtin class must not depend on a private file being present."""
        with unresolvable_needles():
            compiled, meta = nr.load_compiled()
        product = "".join(("whats", "app"))
        self.assertNotIn(product, nr.redact_text("see %s here" % product,
                                                 compiled))
        self.assertEqual(meta[pg.ZERO_TOLERANCE]["status"], "builtin")

    def test_an_absent_needle_file_is_reported_unavailable_never_clean(self):
        with unresolvable_needles():
            _compiled, meta = nr.load_compiled()
        self.assertEqual(nr.unavailable_classes(meta), [pg.ENVIRONMENT])

    def test_the_redactor_loads_needles_through_the_gate_and_not_a_copy(self):
        """Structural backstop for the behavioural test above.

        `load_compiled` must delegate; a local re-implementation would still
        pass the behavioural test on the day it was written and drift the day
        the gate's loader changes.
        """
        tree = ast.parse(open(os.path.join(HERE, "_neutrality_redact.py"),
                              encoding="utf-8").read())
        fn = next(n for n in ast.walk(tree)
                  if isinstance(n, ast.FunctionDef) and n.name == "load_compiled")
        calls = {ast.unparse(n.func) for n in ast.walk(fn)
                 if isinstance(n, ast.Call)}
        self.assertIn("gate.load_needles", calls)
        self.assertIn("gate.compile_needles", calls)


class RedactIdsTest(unittest.TestCase):

    """A node-id freeze is a SET whose cardinality is the claim."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="aw-nrids-")
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self.compiled, _ = nr.load_compiled(needle_file(self.tmp))

    def test_cardinality_and_order_survive(self):
        ids = ["a.b.test_%s_one" % TOK_A, "a.b.test_two", "a.b.test_three"]
        out = nr.redact_ids(ids, self.compiled)
        self.assertEqual(len(out), 3)
        self.assertEqual(out[1:], ids[1:])
        self.assertNotIn(TOK_A, out[0])

    def test_two_ids_differing_only_inside_the_redacted_span_raise(self):
        """The failure the opaque placeholder makes possible, refused loudly.

        Without this, a freeze would silently drop a member and then report the
        loss it caused itself as a regression in the tree under test.
        """
        with self.assertRaises(nr.RedactionCollision) as ctx:
            nr.redact_ids(["m.test_%s" % TOK_A, "m.test_%s" % TOK_B],
                          self.compiled)
        self.assertIn("merges two distinct ids", str(ctx.exception))

    def test_an_exact_duplicate_is_not_a_collision(self):
        """A list that already repeats an id repeats it after redaction too."""
        ids = ["m.test_%s" % TOK_A, "m.test_%s" % TOK_A]
        self.assertEqual(len(nr.redact_ids(ids, self.compiled)), 2)

    def test_clean_ids_are_returned_identical(self):
        ids = ["m.test_a", "m.test_b"]
        self.assertEqual(nr.redact_ids(ids, self.compiled), ids)


class RedactJsonTest(unittest.TestCase):

    """A token in a KEY leaks exactly as loudly as one in a value."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="aw-nrjson-")
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self.compiled, _ = nr.load_compiled(needle_file(self.tmp))

    def test_values_keys_and_nested_structures_are_all_reached(self):
        doc = {TOK_A: [{"deep": "a %s b" % TOK_A}], "n": 1, "t": True}
        out = nr.redact_json(doc, self.compiled)
        self.assertIn(nr.PLACEHOLDER, out)
        self.assertEqual(out[nr.PLACEHOLDER][0]["deep"],
                         "a %s b" % nr.PLACEHOLDER)

    def test_non_string_scalars_survive_their_type(self):
        out = nr.redact_json({"n": 1, "f": 1.5, "b": False, "z": None},
                             self.compiled)
        self.assertEqual(out, {"n": 1, "f": 1.5, "b": False, "z": None})


class FileRoundTripTest(unittest.TestCase):

    """`--apply` must fix the token and change nothing else about the file."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="aw-nrfile-")
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self.needles = needle_file(self.tmp)
        self.compiled, _ = nr.load_compiled(self.needles)

    def _write(self, name, body):
        path = os.path.join(self.tmp, name)
        with open(path, "w", encoding="utf-8") as handle:
            handle.write(body)
        return path

    def test_a_clean_json_file_is_reported_clean_and_left_alone(self):
        path = self._write("c.json", json.dumps({"a": ["b"]}, indent=1) + "\n")
        before = open(path, encoding="utf-8").read()
        self.assertEqual(nr.apply_paths([path], self.compiled), [])
        self.assertEqual(open(path, encoding="utf-8").read(), before)

    def test_a_dirty_json_file_is_rewritten_and_then_clean(self):
        path = self._write("d.json",
                           json.dumps({"a": ["x %s y" % TOK_A]}, indent=1) + "\n")
        self.assertEqual(nr.apply_paths([path], self.compiled), [path])
        self.assertEqual(nr.check_paths([path], self.compiled), [])

    def test_apply_is_idempotent(self):
        path = self._write("d.json",
                           json.dumps({"a": TOK_A}, indent=1) + "\n")
        nr.apply_paths([path], self.compiled)
        body = open(path, encoding="utf-8").read()
        self.assertEqual(nr.apply_paths([path], self.compiled), [])
        self.assertEqual(open(path, encoding="utf-8").read(), body)

    def test_the_generators_indent_style_is_preserved(self):
        """A redactor that reformatted the document would bury its own diff.

        The three real captures are 1877, 2784 and 6470 lines; a re-indent would
        rewrite every one of them and hide the three lines that actually moved.
        """
        for indent in (1, 2, 4):
            path = self._write("i%d.json" % indent,
                               json.dumps({"a": {"b": TOK_A}},
                                          indent=indent) + "\n")
            nr.apply_paths([path], self.compiled)
            body = open(path, encoding="utf-8").read()
            self.assertIn("\n" + " " * indent + '"a"', body)

    def test_a_non_json_file_is_handled_as_text(self):
        path = self._write("n.md", "# heading\nuses %s here\n" % TOK_A)
        self.assertEqual(nr.apply_paths([path], self.compiled), [path])
        self.assertEqual(open(path, encoding="utf-8").read(),
                         "# heading\nuses %s here\n" % nr.PLACEHOLDER)

    def test_a_missing_path_is_a_hit_never_a_silent_pass(self):
        hits = nr.check_paths([os.path.join(self.tmp, "gone.json")],
                              self.compiled)
        self.assertEqual([reason for _, reason in hits], ["MISSING"])


class CliTest(unittest.TestCase):

    """Exit codes, because a harness reads those and not the prose."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="aw-nrcli-")
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self.needles = needle_file(self.tmp)

    def _run(self, argv):
        out, err = io.StringIO(), io.StringIO()
        keep = sys.stdout, sys.stderr
        sys.stdout, sys.stderr = out, err
        try:
            rc = nr.main(argv)
        finally:
            sys.stdout, sys.stderr = keep
        return rc, out.getvalue(), err.getvalue()

    def _dirty(self):
        path = os.path.join(self.tmp, "x.json")
        with open(path, "w", encoding="utf-8") as handle:
            json.dump({"a": TOK_A}, handle, indent=1)
        return path

    def test_check_exits_1_and_names_the_file(self):
        path = self._dirty()
        rc, out, _ = self._run(["--needles", self.needles, path])
        self.assertEqual(rc, 1)
        self.assertIn("x.json", out)

    def test_apply_exits_0_and_a_recheck_exits_0(self):
        path = self._dirty()
        self.assertEqual(
            self._run(["--needles", self.needles, "--apply", path])[0], 0)
        self.assertEqual(self._run(["--needles", self.needles, path])[0], 0)

    def test_require_class_on_an_unavailable_class_exits_2_not_0(self):
        """"Could not look" must never share an exit code with "looked, clean"."""
        with unresolvable_needles():
            rc, _, err = self._run(["--require-class", pg.ENVIRONMENT,
                                    self._dirty()])
        self.assertEqual(rc, 2)
        self.assertIn(pg.ENVIRONMENT, err)

    def test_json_mode_reports_the_unavailable_classes(self):
        with unresolvable_needles():
            rc, out, _ = self._run(
                ["--json", os.path.join(self.tmp, "nothing.json")])
        payload = json.loads(out)
        self.assertEqual(payload["unavailable"], [pg.ENVIRONMENT])

    def test_a_malformed_needle_file_exits_2_rather_than_running_with_none(self):
        bad = os.path.join(self.tmp, "bad.json")
        with open(bad, "w", encoding="utf-8") as handle:
            json.dump({"schema": "wrong"}, handle)
        self.assertEqual(self._run(["--needles", bad])[0], 2)


class LiveTreeTest(unittest.TestCase):

    """The pin: the committed captures carry no needle, whichever class runs."""

    def test_every_declared_capture_artifact_exists(self):
        for rel in nr.CAPTURE_ARTIFACTS:
            self.assertTrue(os.path.isfile(os.path.join(REPO, rel)), rel)

    def test_the_committed_captures_are_clean(self):
        """Runs with whatever needle classes resolve here.

        On a machine holding the private file this is the real assertion; on CI
        it still exercises the zero-tolerance class, which is builtin. Either
        way it never reports clean without having looked -- `load_compiled`
        always returns at least the builtin needles.
        """
        compiled, meta = nr.load_compiled()
        self.assertGreater(len(compiled), 0)
        self.assertEqual(nr.check_paths(nr.resolve_paths(None), compiled), [])

    def test_planting_a_needle_in_a_copy_is_caught(self):
        """The clean result above is not vacuous.

        A needle is planted in a COPY of a real capture -- never the tree -- and
        the same check must report it.
        """
        tmp = tempfile.mkdtemp(prefix="aw-nrlive-")
        self.addCleanup(shutil.rmtree, tmp, ignore_errors=True)
        needles = needle_file(tmp)
        compiled, _ = nr.load_compiled(needles)
        src = os.path.join(REPO, "tests", "fixtures", "nodeids-donor.json")
        dst = os.path.join(tmp, "nodeids-donor.json")
        payload = json.load(open(src, encoding="utf-8"))
        payload["node_ids"].append("m.C.test_%s_probe" % TOK_A)
        with open(dst, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, indent=1)
        self.assertEqual([p for p, _ in nr.check_paths([dst], compiled)], [dst])

    def test_the_collector_neutralises_before_any_consumer_sees_an_id(self):
        """BEHAVIOURAL, over the boundary every id set actually crosses.

        The rule lives in `_preserved_scan.collect_node_ids` rather than in each
        writer, because two committed artifacts are fed from it (the freeze's
        two fixtures and the suite-delta baselines) and a rule in one writer
        leaves the other leaking. Proven by driving the collector's own
        post-subprocess step -- not by reading the source, and not by paying for
        a real 900-second collection.
        """
        try:
            from tests import _preserved_scan as scan
        except ImportError:  # pragma: no cover
            import _preserved_scan as scan
        product = "".join(("whats", "app"))
        out = scan.neutralise_ids(["m.C.test_%s_probe" % product, "m.C.test_x"])
        self.assertNotIn(product, out[0])
        self.assertEqual(out[1], "m.C.test_x")

    def test_the_baseline_capture_neutralises_every_recorded_tail(self):
        """The other write boundary: a captured traceback quotes assertions."""
        try:
            from tests import _baseline_capture as bc
        except ImportError:  # pragma: no cover
            import _baseline_capture as bc
        product = "".join(("whats", "app"))
        self.assertNotIn(product, bc.tail("AssertionError: %s missing" % product))


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
