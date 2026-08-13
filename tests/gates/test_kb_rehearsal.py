"""Proofs that the task-64 losslessness harness BITES.

`tests/_kb_rehearsal.py` is the instrument the migration rehearsal's central
claim rests on — "no content was lost" is true only if the differ can SEE every
way content is lost. So each loss class is staged here as a real mutation on a
real temp tree, and asserted to land in the right bucket: a same-size byte flip,
a deletion, a pruned EMPTY DIRECTORY, a lost executable bit, a retargeted
symlink, a file replaced by a directory.

The empty-directory case is not hypothetical padding. A /tmp working copy of the
donor tree silently lost exactly the three empty directories two plan tasks were
written to exclude (`learn-tmp-copies-lose-empty-dirs-not-files`), and a
file-only manifest — which is what `find -type f | xargs shasum` gives you —
reports that decayed tree as byte-identical.

The verdict rules get the same treatment from the other side: an allowlist is
where a real loss goes to hide, so the tests assert that a REMOVAL cannot be
forgiven by any allowlist entry, that a forgiven change is still reported by
name, and that an `--allow-added teams` prefix does not silently forgive a
sibling called `teams-old`.

Everything runs in a temp directory. The real tree, the operator's config and
the live knowledge base are never read.
"""

from tests._paths import TESTS_DIR

import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest

HERE = TESTS_DIR
REPO = os.path.dirname(HERE)
if HERE not in sys.path:
    sys.path.insert(0, HERE)

import _kb_rehearsal as reh  # noqa: E402


def _write(path, text, mode=None):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as handle:
        handle.write(text)
    if mode is not None:
        os.chmod(path, mode)


def _tree(root):
    """A miniature knowledge base: entries, an empty dir, a link, a script."""
    _write(os.path.join(root, "MAIN.md"), "# MAIN\n")
    _write(os.path.join(root, "learnings", "a.md"), "---\nid: a\n---\nbody A\n")
    _write(os.path.join(root, "learnings", "b.md"), "---\nid: b\n---\nbody B\n")
    _write(os.path.join(root, "logs", "run.sh"), "#!/bin/sh\ntrue\n", mode=0o755)
    os.makedirs(os.path.join(root, "work", "empty-feature"), exist_ok=True)
    os.symlink("learnings/a.md", os.path.join(root, "latest.md"))
    return root


class ManifestShapeTest(unittest.TestCase):
    """What the manifest records — the half a file-only listing gets wrong."""

    def setUp(self):
        self.dir = tempfile.mkdtemp(prefix="aw-reh-")
        self.addCleanup(shutil.rmtree, self.dir, True)
        self.root = _tree(os.path.join(self.dir, "kb"))
        self.man = reh.capture(self.root)

    def test_every_node_kind_is_recorded_under_a_relative_key(self):
        self.assertEqual(self.man["MAIN.md"]["kind"], "file")
        self.assertEqual(self.man["learnings"]["kind"], "dir")
        self.assertEqual(self.man["latest.md"]["kind"], "symlink")
        self.assertNotIn(self.root, " ".join(self.man))

    def test_an_empty_directory_is_a_row_of_its_own(self):
        """The loss class a `find -type f` manifest cannot express."""
        self.assertIn("work/empty-feature", self.man)
        self.assertEqual(self.man["work/empty-feature"]["kind"], "dir")

    def test_a_file_record_carries_content_mode_and_size(self):
        row = self.man["logs/run.sh"]
        self.assertEqual(row["mode"], "0755")
        self.assertEqual(row["size"], len("#!/bin/sh\ntrue\n"))
        self.assertEqual(len(row["sha256"]), 64)

    def test_a_symlink_records_its_target_and_is_never_followed(self):
        row = self.man["latest.md"]
        self.assertEqual(row["target"], "learnings/a.md")
        self.assertNotIn("sha256", row)

    def test_a_dangling_symlink_is_a_record_not_a_crash(self):
        os.symlink("nowhere.md", os.path.join(self.root, "broken.md"))
        man = reh.capture(self.root)
        self.assertEqual(man["broken.md"]["target"], "nowhere.md")

    def test_capture_is_deterministic_and_fingerprints_agree(self):
        again = reh.capture(self.root)
        self.assertEqual(self.man, again)
        self.assertEqual(reh.fingerprint(self.man), reh.fingerprint(again))

    def test_nothing_is_pruned_unless_asked(self):
        """A default prune set would be a silent allowlist."""
        _write(os.path.join(self.root, ".git", "HEAD"), "ref: x\n")
        self.assertIn(".git/HEAD", reh.capture(self.root))
        self.assertNotIn(".git/HEAD", reh.capture(self.root, prune=(".git",)))
        self.assertNotIn(".git", reh.capture(self.root, prune=(".git",)))


class LossClassTest(unittest.TestCase):
    """Each way a tree loses content, staged and asserted into its bucket."""

    def setUp(self):
        self.dir = tempfile.mkdtemp(prefix="aw-reh-")
        self.addCleanup(shutil.rmtree, self.dir, True)
        self.root = _tree(os.path.join(self.dir, "kb"))
        self.before = reh.capture(self.root)

    def _delta(self):
        return reh.diff_manifests(self.before, reh.capture(self.root))

    def test_an_identical_tree_diffs_to_nothing(self):
        delta = self._delta()
        self.assertEqual((delta["added"], delta["removed"], delta["changed"]),
                         ([], [], {}))

    def test_a_same_size_byte_flip_is_changed(self):
        """The case a size-or-mtime compare misses: one character, same length."""
        _write(os.path.join(self.root, "learnings", "a.md"),
               "---\nid: a\n---\nbody Z\n")
        delta = self._delta()
        self.assertEqual(list(delta["changed"]), ["learnings/a.md"])
        self.assertTrue(any(d.startswith("sha256:")
                            for d in delta["changed"]["learnings/a.md"]))
        self.assertFalse(any(d.startswith("size:")
                             for d in delta["changed"]["learnings/a.md"]))

    def test_a_deleted_file_is_removed(self):
        os.remove(os.path.join(self.root, "learnings", "b.md"))
        self.assertEqual(self._delta()["removed"], ["learnings/b.md"])

    def test_a_pruned_empty_directory_is_removed(self):
        """`learn-tmp-copies-lose-empty-dirs-not-files`, mechanically caught."""
        os.rmdir(os.path.join(self.root, "work", "empty-feature"))
        self.assertEqual(self._delta()["removed"], ["work/empty-feature"])

    def test_a_lost_executable_bit_is_changed(self):
        os.chmod(os.path.join(self.root, "logs", "run.sh"), 0o644)
        delta = self._delta()
        self.assertEqual(delta["changed"]["logs/run.sh"],
                         ["mode: '0755' -> '0644'"])

    def test_a_retargeted_symlink_is_changed(self):
        link = os.path.join(self.root, "latest.md")
        os.remove(link)
        os.symlink("learnings/b.md", link)
        self.assertIn("target:", self._delta()["changed"]["latest.md"][0])

    def test_a_symlink_is_unchanged_when_only_its_target_changes(self):
        """Following the link would attribute the target's edit to the link."""
        _write(os.path.join(self.root, "learnings", "a.md"), "rewritten\n")
        self.assertNotIn("latest.md", self._delta()["changed"])

    def test_a_file_replaced_by_a_directory_is_changed(self):
        path = os.path.join(self.root, "MAIN.md")
        os.remove(path)
        os.makedirs(path)
        self.assertTrue(any(d.startswith("kind:")
                            for d in self._delta()["changed"]["MAIN.md"]))

    def test_a_new_file_is_added(self):
        _write(os.path.join(self.root, "shared", "MAIN.md"), "# shared\n")
        self.assertEqual(self._delta()["added"], ["shared", "shared/MAIN.md"])

    def test_the_fingerprint_moves_for_every_loss_class(self):
        """One line a report can cite; it must not be blind where the diff sees."""
        base = reh.fingerprint(self.before)
        os.rmdir(os.path.join(self.root, "work", "empty-feature"))
        self.assertNotEqual(base, reh.fingerprint(reh.capture(self.root)))


class VerdictTest(unittest.TestCase):
    """The allowlist rules — where a real loss would go to hide."""

    def _judge(self, added=(), removed=(), changed=(), **kw):
        delta = {"added": list(added), "removed": list(removed),
                 "changed": {c: ["sha256: 'x' -> 'y'"] for c in changed}}
        return delta, reh.verdict(delta, **kw)

    def test_a_clean_diff_is_lossless_and_additive(self):
        _, judged = self._judge()
        self.assertTrue(judged["lossless"])
        self.assertTrue(judged["additive_only"])

    def test_a_removal_is_never_forgiven_by_any_allowlist(self):
        """There is deliberately no allow-removed; naming it elsewhere must not work."""
        _, judged = self._judge(removed=["learnings/a.md"],
                                allow_changed=("learnings/a.md",),
                                allow_added=("learnings/a.md",))
        self.assertFalse(judged["lossless"])
        self.assertEqual(judged["removed"], ["learnings/a.md"])

    def test_an_unlisted_change_is_unexpected(self):
        _, judged = self._judge(changed=["index.json"])
        self.assertFalse(judged["lossless"])
        self.assertEqual(judged["changed_unexpected"], ["index.json"])

    def test_a_forgiven_change_is_still_reported_by_name(self):
        _, judged = self._judge(changed=[".schema"], allow_changed=(".schema",))
        self.assertTrue(judged["lossless"])
        self.assertEqual(judged["changed_forgiven"], [".schema"])
        self.assertEqual(judged["allow_changed"], [".schema"])

    def test_an_allowlist_prefix_covers_a_subtree_but_not_a_sibling(self):
        _, judged = self._judge(added=["teams/atlas/MAIN.md", "teams-old/x.md"],
                                allow_added=("teams",))
        self.assertEqual(judged["added_expected"], ["teams/atlas/MAIN.md"])
        self.assertEqual(judged["added_unexpected"], ["teams-old/x.md"])
        self.assertFalse(judged["additive_only"])

    def test_additive_only_is_false_when_an_addition_is_unexpected(self):
        _, judged = self._judge(added=["surprise.md"])
        self.assertTrue(judged["lossless"])
        self.assertFalse(judged["additive_only"])

    def test_render_names_the_verdict_and_caps_a_long_list(self):
        rows = ["learnings/%03d.md" % i for i in range(50)]
        delta, judged = self._judge(removed=rows)
        text = reh.render(delta, judged, limit=5)
        self.assertIn("... 45 more", text)
        self.assertIn("LOSSLESS: False", text)


class CommandLineTest(unittest.TestCase):
    """The harness as the rehearsal actually invokes it: two captures and a gate."""

    def setUp(self):
        self.dir = tempfile.mkdtemp(prefix="aw-reh-")
        self.addCleanup(shutil.rmtree, self.dir, True)
        self.root = _tree(os.path.join(self.dir, "kb"))

    def _run(self, *argv):
        return subprocess.run(
            [sys.executable, os.path.join(REPO, "tests", "_kb_rehearsal.py")]
            + list(argv), capture_output=True, text=True, cwd=REPO)

    def _capture(self, name):
        out = os.path.join(self.dir, name)
        proc = self._run("capture", self.root, "--out", out)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        return out, proc

    def test_capture_writes_json_and_reports_its_fingerprint(self):
        out, proc = self._capture("before.json")
        with open(out, "r", encoding="utf-8") as handle:
            man = json.load(handle)
        self.assertIn("MAIN.md", man)
        self.assertIn(reh.fingerprint(man), proc.stderr)

    def test_an_untouched_tree_compares_clean_with_rc_zero(self):
        before, _ = self._capture("before.json")
        after, _ = self._capture("after.json")
        proc = self._run("compare", before, after)
        self.assertEqual(proc.returncode, 0, proc.stdout)
        self.assertIn("LOSSLESS: True", proc.stdout)

    def test_a_deletion_exits_nonzero_and_names_the_path(self):
        before, _ = self._capture("before.json")
        os.remove(os.path.join(self.root, "learnings", "b.md"))
        after, _ = self._capture("after.json")
        proc = self._run("compare", before, after)
        self.assertEqual(proc.returncode, 1)
        self.assertIn("learnings/b.md", proc.stdout)

    def test_an_allowed_change_plus_an_expected_add_exits_zero(self):
        """The migration's own shape: `.schema` restamped, `teams/` scaffolded."""
        before, _ = self._capture("before.json")
        _write(os.path.join(self.root, ".schema"), "2\n")
        _write(os.path.join(self.root, "teams", "atlas", "MAIN.md"), "# t\n")
        after, _ = self._capture("after.json")
        proc = self._run("compare", before, after, "--allow-changed", ".schema",
                         "--allow-added", "teams", "--allow-added", ".schema")
        self.assertEqual(proc.returncode, 0, proc.stdout)
        self.assertIn("ADDITIVE-ONLY: True", proc.stdout)

    def test_an_unexpected_add_exits_nonzero_once_a_prefix_is_declared(self):
        before, _ = self._capture("before.json")
        _write(os.path.join(self.root, "teams", "atlas", "MAIN.md"), "# t\n")
        _write(os.path.join(self.root, "surprise.md"), "?\n")
        after, _ = self._capture("after.json")
        proc = self._run("compare", before, after, "--allow-added", "teams")
        self.assertEqual(proc.returncode, 1)
        self.assertIn("surprise.md", proc.stdout)

    def test_json_format_carries_the_verdict_and_the_raw_diff(self):
        before, _ = self._capture("before.json")
        os.remove(os.path.join(self.root, "learnings", "b.md"))
        after, _ = self._capture("after.json")
        proc = self._run("compare", before, after, "--format", "json")
        payload = json.loads(proc.stdout)
        self.assertFalse(payload["lossless"])
        self.assertEqual(payload["diff"]["removed"], ["learnings/b.md"])
        self.assertEqual(payload["counts"]["before"],
                         payload["counts"]["after"] + 1)


if __name__ == "__main__":
    unittest.main()
