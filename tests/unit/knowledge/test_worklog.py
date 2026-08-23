"""Owning unit file for `agentware_lib.knowledge.worklog` — task 99 wave 18.

The module carried a `test-ownership` row, and the residual's own description
fits it exactly: this module is heavily exercised and owned by nothing. Its
promotion MATCHING is pinned by `test_worklog_promotion` (a behaviour suite named
for the bug it closed), the loop drives `worklog scan` at every post-hook, and
`ops.failureladder` reads its per-kind tally — so the module reddens when a
PROMOTION rule breaks, and stayed green for the two stem SOURCES that decide
which promotions are visible at all.

What is asserted here is the module's PROMISE, not its shape:

  * THE STEM SET IS A UNION OF TWO INDEPENDENT SOURCES. The index knows entries
    that were registered; the directory knows files that exist. An entry written
    but not yet registered is visible only to the second, and a registered entry
    whose file was moved only to the first. Each half is asserted to contribute
    ALONE, so a body that dropped one would redden even though the other keeps
    the common case working.
  * THE EMPTY STEM IS NEVER A STEM. An id that is exactly the prefix, or a file
    literally named `.md`, reduces to `""` — and `""` compares equal to the slug
    of any marker whose prose slugifies to nothing, which would close markers at
    random. This is the one filter the union applies.
  * AN ENTRY CONTRIBUTES BOTH ITS FILENAME STEM AND ITS ID STEM, because a
    marker may name either, and the two disagree more often than they should.
  * THE CATEGORY FILTER IS EXACT. A learnings-only stem set that admitted a
    decision would close a `> LEARNED:` marker against an unrelated decision.
  * A HOSTILE INDEX IS SURVIVED, NOT TRUSTED: a non-dict entry, a null path, a
    null id and a missing category are all skipped rather than raising, because
    this runs inside the completion gate and a crash there fails the run.
  * A `> WAIVED:` LINE CLOSES THE MOST RECENT STILL-OPEN MARKER, and closes
    NOTHING when every marker so far is promoted — the difference between an
    explicit "not worth promoting" and a free pass.
  * THE SCAN IS LENIENT ABOUT ITS FILE AND STRICT ABOUT ITS IDS: an absent or
    unreadable worklog is an empty scan, while a promotion reference resolves by
    EXACT id and never by substring.

Hermetic: every knowledge dir is a fresh mkdtemp. Nothing here reads the
operator's KB or writes outside its own temp tree.
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

import agentware_lib.knowledge.worklog as worklog      # noqa: E402


def _entry(eid=None, path=None, category=None, **kw):
    e = dict(kw)
    if eid is not None:
        e["id"] = eid
    if path is not None:
        e["path"] = path
    if category is not None:
        e["category"] = category
    return e


class WorklogKdirTestCase(unittest.TestCase):
    """A scratch knowledge dir with a writable learnings/decisions tree."""

    def setUp(self):
        self.kdir = tempfile.mkdtemp(prefix="aw-wlstems-")
        self.addCleanup(shutil.rmtree, self.kdir, True)

    def write(self, rel, text=""):
        path = os.path.join(self.kdir, rel)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            f.write(text)
        return path

    def index(self, entries):
        self.write("index.json", json.dumps({"version": 1, "entries": entries}))
        return {"entries": entries}


class WorklogIndexedStemsTest(WorklogKdirTestCase):
    """`_indexed_category_stems`: the INDEX half of the union."""

    def test_wlstems_an_empty_index_contributes_nothing(self):
        self.assertEqual(
            worklog._indexed_category_stems({}, "learnings", "learn-"), set())
        self.assertEqual(
            worklog._indexed_category_stems({"entries": None}, "learnings",
                                            "learn-"), set())

    def test_wlstems_an_entry_contributes_its_filename_stem(self):
        data = {"entries": [_entry("learn-x", "learnings/a-thing.md",
                                   "learnings")]}
        self.assertIn(
            "a-thing",
            worklog._indexed_category_stems(data, "learnings", "learn-"))

    def test_wlstems_an_entry_contributes_its_prefix_stripped_id(self):
        data = {"entries": [_entry("learn-x", "learnings/a-thing.md",
                                   "learnings")]}
        self.assertIn(
            "x", worklog._indexed_category_stems(data, "learnings", "learn-"))

    def test_wlstems_both_stems_are_kept_when_they_disagree(self):
        # A marker may name EITHER, and in a real corpus they diverge whenever an
        # entry is renamed on one side only.
        data = {"entries": [_entry("learn-old-name", "learnings/new-name.md",
                                   "learnings")]}
        self.assertEqual(
            worklog._indexed_category_stems(data, "learnings", "learn-"),
            {"old-name", "new-name"})

    def test_wlstems_the_category_filter_is_exact(self):
        data = {"entries": [_entry("decide-y", "decisions/y.md", "decisions")]}
        self.assertEqual(
            worklog._indexed_category_stems(data, "learnings", "learn-"), set())
        self.assertEqual(
            worklog._indexed_category_stems(data, "decisions", "decide-"),
            {"y"})

    def test_wlstems_an_id_without_the_prefix_contributes_no_id_stem(self):
        data = {"entries": [_entry("zzz-x", "learnings/a.md", "learnings")]}
        self.assertEqual(
            worklog._indexed_category_stems(data, "learnings", "learn-"), {"a"})

    def test_wlstems_stems_are_lowercased(self):
        data = {"entries": [_entry("learn-UPPER", "learnings/Mixed-Case.md",
                                   "learnings")]}
        self.assertEqual(
            worklog._indexed_category_stems(data, "learnings", "learn-"),
            {"upper", "mixed-case"})

    def test_wlstems_a_non_md_path_contributes_no_filename_stem(self):
        data = {"entries": [_entry("learn-x", "learnings/a.txt", "learnings")]}
        self.assertEqual(
            worklog._indexed_category_stems(data, "learnings", "learn-"), {"x"})

    def test_wlstems_a_hostile_entry_is_skipped_rather_than_raising(self):
        data = {"entries": ["a string", ["a", "list"], None, 7,
                            _entry(None, None, "learnings"),
                            _entry("learn-ok", "learnings/ok.md", "learnings")]}
        self.assertEqual(
            worklog._indexed_category_stems(data, "learnings", "learn-"),
            {"ok"})

    def test_wlstems_the_empty_prefix_admits_every_id_whole(self):
        data = {"entries": [_entry("learn-x", "learnings/a.md", "learnings")]}
        self.assertEqual(
            worklog._indexed_category_stems(data, "learnings", ""),
            {"a", "learn-x"})


class WorklogDirStemsTest(WorklogKdirTestCase):
    """`_dir_md_stems`: the ON-DISK half of the union."""

    def test_wlstems_an_absent_directory_contributes_nothing(self):
        self.assertEqual(worklog._dir_md_stems(self.kdir, "no-such-dir"), set())

    def test_wlstems_a_path_that_is_a_file_contributes_nothing(self):
        self.write("MAIN.md", "# scratch\n")
        self.assertEqual(worklog._dir_md_stems(self.kdir, "MAIN.md"), set())

    def test_wlstems_every_md_file_contributes_its_stem(self):
        self.write("learnings/a-thing.md")
        self.write("learnings/another.md")
        self.assertEqual(worklog._dir_md_stems(self.kdir, "learnings"),
                         {"a-thing", "another"})

    def test_wlstems_index_md_is_excluded_by_name(self):
        self.write("learnings/index.md")
        self.write("learnings/real.md")
        self.assertEqual(worklog._dir_md_stems(self.kdir, "learnings"),
                         {"real"})

    def test_wlstems_a_non_md_file_is_excluded_by_extension(self):
        self.write("learnings/notes.txt")
        self.write("learnings/real.md")
        self.assertEqual(worklog._dir_md_stems(self.kdir, "learnings"),
                         {"real"})

    def test_wlstems_a_subdirectory_is_not_a_stem(self):
        os.makedirs(os.path.join(self.kdir, "learnings", "nested"))
        self.assertEqual(worklog._dir_md_stems(self.kdir, "learnings"), set())

    def test_wlstems_disk_stems_are_lowercased(self):
        self.write("learnings/Mixed-Case.md")
        self.assertEqual(worklog._dir_md_stems(self.kdir, "learnings"),
                         {"mixed-case"})

    def test_wlstems_a_file_named_dot_md_yields_the_empty_stem(self):
        # The half of the empty-stem source that lives on disk. The filter is
        # applied by the UNION, not here, so this is what the union has to catch.
        self.write("learnings/.md")
        self.assertEqual(worklog._dir_md_stems(self.kdir, "learnings"), {""})


class WorklogCategoryStemsTest(WorklogKdirTestCase):
    """`_category_stems`: the filtered UNION of the two sources."""

    def test_wlstems_the_index_half_contributes_alone(self):
        data = {"entries": [_entry("learn-x", "learnings/indexed.md",
                                   "learnings")]}
        self.assertEqual(
            worklog._category_stems(self.kdir, data, "learnings", "learn-",
                                    "no-such-dir"),
            {"indexed", "x"})

    def test_wlstems_the_disk_half_contributes_alone(self):
        self.write("learnings/on-disk.md")
        self.assertEqual(
            worklog._category_stems(self.kdir, {}, "learnings", "learn-",
                                    "learnings"),
            {"on-disk"})

    def test_wlstems_the_two_halves_are_unioned_not_intersected(self):
        self.write("learnings/on-disk.md")
        data = {"entries": [_entry("learn-x", "learnings/indexed.md",
                                   "learnings")]}
        self.assertEqual(
            worklog._category_stems(self.kdir, data, "learnings", "learn-",
                                    "learnings"),
            {"on-disk", "indexed", "x"})

    def test_wlstems_a_stem_present_in_both_appears_once(self):
        self.write("learnings/shared.md")
        data = {"entries": [_entry("learn-shared", "learnings/shared.md",
                                   "learnings")]}
        self.assertEqual(
            worklog._category_stems(self.kdir, data, "learnings", "learn-",
                                    "learnings"),
            {"shared"})

    def test_wlstems_the_empty_stem_is_filtered_from_the_index_half(self):
        # An id that is EXACTLY the prefix.
        data = {"entries": [_entry("learn-", "learnings/a.md", "learnings")]}
        self.assertEqual(
            worklog._category_stems(self.kdir, data, "learnings", "learn-",
                                    "no-such-dir"),
            {"a"})

    def test_wlstems_the_empty_stem_is_filtered_from_the_disk_half(self):
        self.write("learnings/.md")
        self.write("learnings/real.md")
        self.assertEqual(
            worklog._category_stems(self.kdir, {}, "learnings", "learn-",
                                    "learnings"),
            {"real"})

    def test_wlstems_a_result_never_contains_the_empty_string(self):
        self.write("learnings/.md")
        data = {"entries": [_entry("learn-", "learnings/.md", "learnings")]}
        stems = worklog._category_stems(self.kdir, data, "learnings", "learn-",
                                        "learnings")
        self.assertNotIn("", stems)

    def test_wlstems_learning_and_decision_stems_read_their_own_category(self):
        self.write("learnings/l-disk.md")
        self.write("decisions/d-disk.md")
        data = {"entries": [
            _entry("learn-l", "learnings/l-indexed.md", "learnings"),
            _entry("decide-d", "decisions/d-indexed.md", "decisions")]}
        self.assertEqual(worklog.learning_stems(self.kdir, data),
                         {"l-disk", "l-indexed", "l"})
        self.assertEqual(worklog.decision_stems(self.kdir, data),
                         {"d-disk", "d-indexed", "d"})

    def test_wlstems_the_stems_read_nothing_and_write_nothing(self):
        self.write("learnings/a.md")
        idx = self.write("index.json", json.dumps({"version": 1, "entries": []}))
        before = os.path.getmtime(idx)
        worklog.learning_stems(self.kdir, {"entries": []})
        self.assertEqual(os.path.getmtime(idx), before)


class WorklogPromotionUniverseTest(WorklogKdirTestCase):
    """`all_entry_ids` / `_real_ids` / `skill_promotion_ids`: what a reference
    may resolve to."""

    def test_wlstems_real_ids_carries_both_the_bare_and_prefixed_form(self):
        self.assertEqual(worklog._real_ids({"x"}, "learn-"), {"x", "learn-x"})

    def test_wlstems_all_entry_ids_includes_ids_and_filename_stems(self):
        data = {"entries": [_entry("learn-x", "learnings/a-thing.md",
                                   "learnings")]}
        ids = worklog.all_entry_ids(self.kdir, data)
        self.assertIn("learn-x", ids)
        self.assertIn("a-thing", ids)

    def test_wlstems_all_entry_ids_spans_categories(self):
        data = {"entries": [
            _entry("learn-x", "learnings/x.md", "learnings"),
            _entry("decide-y", "decisions/y.md", "decisions"),
            _entry("skill-z", "skills/z/SKILL.md", "skills")]}
        ids = worklog.all_entry_ids(self.kdir, data)
        self.assertTrue({"learn-x", "decide-y", "skill-z"} <= ids)

    def test_wlstems_a_skill_on_disk_resolves_without_being_indexed(self):
        self.write("skills/a-skill.md")
        ids = worklog.all_entry_ids(self.kdir, {"entries": []})
        self.assertIn("a-skill", ids)
        self.assertIn("skill-a-skill", ids)

    def test_wlstems_a_skill_directory_clears_a_learned_marker(self):
        self.write("skills/a-skill/SKILL.md")
        ids = worklog.skill_promotion_ids(self.kdir, {"entries": []})
        self.assertIn("skill-a-skill", ids)

    def test_wlstems_an_indexed_skill_is_a_promotion_target(self):
        data = {"entries": [_entry("skill-z", "skills/z/SKILL.md", "skills")]}
        self.assertIn("skill-z", worklog.skill_promotion_ids(self.kdir, data))

    def test_wlstems_a_hostile_index_does_not_break_the_universe(self):
        data = {"entries": ["str", None, 7, _entry(None, None, None)]}
        self.assertIsInstance(worklog.all_entry_ids(self.kdir, data), set)
        self.assertIsInstance(worklog.skill_promotion_ids(self.kdir, data), set)


class WorklogIsPromotedTest(WorklogKdirTestCase):
    """`promoted_ids` / `_is_promoted`: exact resolution, never substring."""

    def test_wlstems_every_reference_form_is_recognised(self):
        for text in ("(Promoted: learn-x)", "[promoted -> learn-x]",
                     "[promoted → learn-x]", "[kb: learn-x]"):
            self.assertEqual(worklog.promoted_ids(text), ["learn-x"], text)

    def test_wlstems_a_reference_is_lowercased(self):
        self.assertEqual(worklog.promoted_ids("(Promoted: LEARN-X)"),
                         ["learn-x"])

    def test_wlstems_no_reference_yields_no_ids(self):
        self.assertEqual(worklog.promoted_ids("just some prose"), [])
        self.assertEqual(worklog.promoted_ids(None), [])

    def test_wlstems_a_resolving_reference_promotes(self):
        self.assertTrue(worklog._is_promoted("(Promoted: learn-x) prose",
                                             set(), {"learn-x"}))

    def test_wlstems_a_dangling_reference_does_not_promote(self):
        self.assertFalse(worklog._is_promoted("(Promoted: learn-ghost) prose",
                                              {"x"}, {"learn-x"}))

    def test_wlstems_a_cross_category_reference_resolves_via_all_ids(self):
        self.assertFalse(worklog._is_promoted("(Promoted: skill-z) prose",
                                              set(), {"learn-x"}))
        self.assertTrue(worklog._is_promoted("(Promoted: skill-z) prose",
                                             set(), {"learn-x"}, {"skill-z"}))

    def test_wlstems_the_slug_fallback_needs_exact_equality(self):
        # BOTH directions of the old substring heuristic, because each was a
        # separate bug: a stem LONGER than the slug (the marker is a prefix of
        # an unrelated entry) and a stem SHORTER than it (the false positive
        # `> LEARNED: ci` closing against `learn-ci-cache-warming`). A test
        # carrying only the first passes against `stem in slug`.
        self.assertTrue(worklog._is_promoted("a thing", {"a-thing"}, set()))
        self.assertFalse(worklog._is_promoted("a thing", {"a-thing-extended"},
                                              set()))
        self.assertFalse(worklog._is_promoted("a thing extended",
                                              {"a-thing"}, set()))

    def test_wlstems_a_marker_with_a_reference_never_falls_back_to_the_slug(self):
        # A dangling reference is a MISTAKE the author made; silently promoting
        # it because the prose happens to slug-match would hide it.
        self.assertFalse(worklog._is_promoted("(Promoted: learn-ghost) a thing",
                                              {"a-thing"}, set()))

    def test_wlstems_prose_that_slugifies_to_nothing_is_not_promoted(self):
        self.assertFalse(worklog._is_promoted("...", {""}, set()))


class WorklogScanMarkersTest(WorklogKdirTestCase):
    """`scan_worklog_markers`: the completion gate's one scanner."""

    def _scan(self, body, entries=None):
        data = {"entries": entries or []}
        path = self.write("work/260701-f/worklog.md", body)
        return worklog.scan_worklog_markers(path, self.kdir, data)

    def test_wlstems_an_absent_file_scans_empty(self):
        res = worklog.scan_worklog_markers(
            os.path.join(self.kdir, "nope.md"), self.kdir, {"entries": []})
        self.assertEqual(res["total"], 0)
        self.assertEqual(res["unpromoted"], [])
        self.assertEqual(res["waived"], 0)

    def test_wlstems_a_directory_in_place_of_a_worklog_scans_empty(self):
        os.makedirs(os.path.join(self.kdir, "as-a-dir"))
        res = worklog.scan_worklog_markers(
            os.path.join(self.kdir, "as-a-dir"), self.kdir, {"entries": []})
        self.assertEqual(res["total"], 0)

    def test_wlstems_both_marker_families_are_counted(self):
        res = self._scan("> LEARNED: one\n> DECISION: two\n")
        self.assertEqual(res["total"], 2)
        self.assertEqual(res["per_kind"]["learned"]["total"], 1)
        self.assertEqual(res["per_kind"]["decision"]["total"], 1)

    def test_wlstems_a_bullet_prefixed_marker_is_seen(self):
        # The gate FAILS OPEN if this form is missed: zero unpromoted markers,
        # promise goes through, learning lost.
        res = self._scan("- > LEARNED: a bulleted marker\n")
        self.assertEqual(res["total"], 1)

    def test_wlstems_a_bare_marker_without_the_blockquote_is_seen(self):
        self.assertEqual(self._scan("LEARNED: bare\n")["total"], 1)

    def test_wlstems_a_marker_with_no_text_is_not_a_marker(self):
        self.assertEqual(self._scan("> LEARNED:\n> LEARNED:   \n")["total"], 0)

    def test_wlstems_an_unpromoted_marker_carries_its_line_and_kind(self):
        res = self._scan("# head\n\n> LEARNED: a thing\n")
        self.assertEqual(res["unpromoted"],
                         [{"line": 3, "text": "a thing", "kind": "learned"}])

    def test_wlstems_a_promoted_marker_is_not_reported(self):
        res = self._scan(
            "> LEARNED: (Promoted: learn-x) a thing\n",
            entries=[_entry("learn-x", "learnings/x.md", "learnings")])
        self.assertEqual(res["unpromoted"], [])
        self.assertEqual(res["per_kind"]["learned"],
                         {"total": 1, "unpromoted": 0})

    def test_wlstems_a_stem_promotion_needs_the_stem_to_exist(self):
        self.write("learnings/a-thing.md")
        res = self._scan("> LEARNED: a thing\n")
        self.assertEqual(res["unpromoted"], [])

    def test_wlstems_a_waived_line_closes_the_most_recent_open_marker(self):
        res = self._scan("> LEARNED: one\n> LEARNED: two\n> WAIVED: not durable\n")
        self.assertEqual(res["waived"], 1)
        self.assertEqual([u["text"] for u in res["unpromoted"]], ["one"])
        self.assertEqual(res["per_kind"]["learned"],
                         {"total": 2, "unpromoted": 1})

    def test_wlstems_a_waived_line_with_nothing_open_closes_nothing(self):
        res = self._scan("> WAIVED: nothing above me\n> LEARNED: one\n")
        self.assertEqual(res["waived"], 0)
        self.assertEqual(len(res["unpromoted"]), 1)

    def test_wlstems_a_waiver_after_a_promoted_marker_closes_nothing(self):
        # The waiver pops the still-OPEN stack, and a promoted marker never
        # entered it — so this must not silently forgive an earlier marker.
        res = self._scan(
            "> LEARNED: one\n"
            "> LEARNED: (Promoted: learn-x) two\n"
            "> WAIVED: about the promoted one\n",
            entries=[_entry("learn-x", "learnings/x.md", "learnings")])
        self.assertEqual(res["waived"], 1)
        self.assertEqual([u["text"] for u in res["unpromoted"]], [])

    def test_wlstems_a_waiver_closes_across_kinds(self):
        res = self._scan("> DECISION: one\n> WAIVED: duplicate\n")
        self.assertEqual(res["per_kind"]["decision"],
                         {"total": 1, "unpromoted": 0})

    def test_wlstems_a_line_naming_both_families_is_counted_under_the_first(self):
        # The two regexes are both anchored at the line start, so no line can
        # match both and the scanner's `break` is an optimisation rather than a
        # decision (measured: mutating it to `continue` changes nothing). What
        # IS decidable, and asserted here, is that the prose of the second
        # keyword does not leak into a second family's tally.
        res = self._scan("> LEARNED: DECISION: a confusing line\n")
        self.assertEqual(res["total"], 1)
        self.assertEqual(res["per_kind"]["learned"]["total"], 1)
        self.assertEqual(res["per_kind"]["decision"]["total"], 0)
        self.assertEqual(res["unpromoted"][0]["kind"], "learned")

    def test_wlstems_the_scan_is_deterministic_over_the_same_inputs(self):
        body = "> LEARNED: one\n> DECISION: two\n> WAIVED: three\n"
        first = json.dumps(self._scan(body), sort_keys=True)
        second = json.dumps(self._scan(body), sort_keys=True)
        self.assertEqual(first, second)

    def test_wlstems_undecodable_bytes_do_not_stop_the_scan(self):
        path = os.path.join(self.kdir, "work", "260701-b", "worklog.md")
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "wb") as f:
            f.write(b"> LEARNED: caf\xe9 latte\n> LEARNED: second\n")
        res = worklog.scan_worklog_markers(path, self.kdir, {"entries": []})
        self.assertEqual(res["total"], 2)

    def test_wlstems_the_back_compat_wrapper_returns_the_pair(self):
        path = self.write("work/260701-w/worklog.md", "> LEARNED: one\n")
        unpromoted, total = worklog.scan_worklog(path, self.kdir,
                                                 {"entries": []})
        self.assertEqual(total, 1)
        self.assertEqual([u["kind"] for u in unpromoted], ["learned"])


class WorklogAssessmentIdsTest(WorklogKdirTestCase):
    """`scan_assessment_ids`: the ADVISORY sidecar, with its own id semantics."""

    def test_wlstems_an_absent_assessment_yields_nothing(self):
        self.assertEqual(
            worklog.scan_assessment_ids(os.path.join(self.kdir, "nope.md"),
                                        {"entries": []}), [])
        self.assertEqual(worklog.scan_assessment_ids(None, {"entries": []}), [])

    def test_wlstems_a_suggested_id_that_never_landed_is_reported(self):
        path = self.write("work/f/assessment.md",
                          "- **Suggested ID:** `learn-ghost`\n")
        self.assertEqual(
            [r["id"] for r in worklog.scan_assessment_ids(path,
                                                          {"entries": []})],
            ["learn-ghost"])

    def test_wlstems_a_suggested_id_that_landed_is_not_reported(self):
        path = self.write("work/f/assessment.md",
                          "- **Suggested ID**: `learn-real` *(promoted)*\n")
        data = {"entries": [_entry("learn-real", "learnings/real.md",
                                   "learnings")]}
        self.assertEqual(worklog.scan_assessment_ids(path, data), [])

    def test_wlstems_the_sidecar_resolves_by_index_id_and_not_by_stem(self):
        # Deliberately STRICTER than `_is_promoted`: the suggested id is the id
        # the author said they would register, so a filename stem is not it.
        path = self.write("work/f/assessment.md",
                          "- **Suggested ID:** `real`\n")
        data = {"entries": [_entry("learn-real", "learnings/real.md",
                                   "learnings")]}
        self.assertEqual([r["id"] for r in worklog.scan_assessment_ids(path,
                                                                       data)],
                         ["real"])


class WorklogExportsTest(unittest.TestCase):
    """The module's published surface."""

    def test_wlstems_every_exported_name_exists(self):
        for name in worklog._EXPORTS:
            self.assertTrue(hasattr(worklog, name),
                            "_EXPORTS names %r, which the module does not define"
                            % name)

    def test_wlstems_the_marker_regexes_are_anchored_at_the_line_start(self):
        self.assertIsNone(worklog.LEARNED_RE.match("prose > LEARNED: x"))
        self.assertIsNone(worklog.DECISION_RE.match("prose > DECISION: x"))

    def test_wlstems_the_verb_is_declared_in_this_module(self):
        self.assertIn("worklog", [v[0] for v in worklog.__verbs__])


if __name__ == "__main__":
    unittest.main()
