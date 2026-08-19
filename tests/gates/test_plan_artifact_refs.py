"""No committed file may cite a plan artifact the reader cannot obtain.

Feature 260813-modular-core-adoption, task 20. The donor tree annotated its own
source with references to the PRIVATE planning documents it was built from --
"(Plan B Task 1/3)", "(Plan B Task 14 — AUDIT R1)", "Plan A's Task-7 backfill",
"the SB4 default". Twenty-four of them, spread across shipping modules, the
generated module manifest, the router README and a session hook.

They are a defect in an open-source package for two independent reasons. A
reader cannot resolve them, so a comment that exists to explain WHY a rule
exists explains nothing; and they advertise an internal programme of work that
is not part of this repository. Task 20 rewrote every one into self-describing
prose, and this is the ratchet that keeps them out -- a hand sweep with no gate
behind it is a claim, not a property (`learn-a-token-you-never-added-to-the-
needle-list-reads-as-a-clean-pass`).

Deliberately NOT banned, and the boundary matters:

  * ``feature <YYMMDD-slug>`` provenance tags. Both trees carry them (60 in this
    repo before the merge), they name a work directory in the operator's own
    knowledge base under a documented convention, and the convention is how the
    loop itself names work. That is a shared vocabulary, not a leak.
  * ``wave-<n>`` / ``seam <n>`` decomposition bookkeeping and the ``C-<n>``
    constraint shorthands. They label steps of the change that produced the code
    they sit in, not an external document, and they resolve against the branch
    history. Retiring that vocabulary belongs to the contributor-documentation
    task, not to a neutrality sweep.

The scanner skips any file carrying MARKER so this module -- which must name
the shapes it bans -- does not report itself.

DECLARED is the shrink-only ledger for the one file that cannot be fixed here:
`scripts/agentware`, the monolith, which task 25 requires to land as the legacy
module BYTE-IDENTICAL to its `aw-pre-merge` content. Editing a comment in it now
would break that criterion, so its three hits are declared with the task that
removes them, and the ledger bites in both directions -- an undeclared hit fails,
and a declared location the tree no longer produces fails as stale.
"""

from tests._paths import TESTS_DIR

import os
import re
import subprocess
import unittest

HERE = TESTS_DIR
REPO_ROOT = os.path.dirname(HERE)

#: A file containing this string is the scanner or its fixture, and is skipped.
MARKER = "AGENTWARE-PLAN-ARTIFACT-SOURCE"

#: Each row is (label, pattern). Word-bounded so `Planning`, `SBOM` and a
#: heading called "Plan Authoring" do not fire.
BANNED = (
    ("private plan document", r"\bPlan [AB]\b"),
    ("private audit finding id", r"\bAUDIT R\d+\b"),
    ("private sub-brief id", r"\bSB\d+\b"),
)

COMPILED = tuple((label, re.compile(pattern)) for label, pattern in BANNED)

#: SHRINK-ONLY. {path: {"count": n, "owner": task, "reason": why}} for hits this
#: task provably cannot remove. A hit at any other path fails; a row the tree no
#: longer produces fails as stale.
DECLARED = {
    "scripts/agentware_legacy.py": {
        "count": 3,
        "owner": "task 56",
        "reason": "the monolith carries this tree's own instance of the same "
                  "leak -- a FANOUT NOTE citing the private auto-skill-"
                  "promotion plan by name. It cannot be fixed here: task 25 "
                  "landed this file as the legacy module and asserts it is "
                  "BYTE-IDENTICAL to its aw-pre-merge content, so any edit "
                  "reddens that criterion. The row was keyed "
                  "`scripts/agentware` until task 25 moved the body to "
                  "`scripts/agentware_legacy.py` behind the facade; the LOCATION "
                  "moved, the hits did not, and the ledger stays shrink-only "
                  "because the count is unchanged. The successor module that "
                  "replaces the comment (features/proposals.py) was cleaned by "
                  "task 20 in the same change, so the fix is already staged; "
                  "task 56 deletes the monolith and this row with it.",
        # RETIRES WITH THE FILE (task 94). The reason above ends "task 56
        # deletes the monolith and this row with it" -- so once the monolith is
        # gone the row must stop being in force, or the ledger's own staleness
        # check reddens on a row that is doing exactly what it promised. The
        # row is not DELETED, because deleting it while the file still exists
        # would silently un-declare three live hits; it is conditioned on the
        # file, and `active_rows` is where the two states are told apart.
        "terminal_with": "scripts/agentware_legacy.py",
        # The IDENTITY ANCHOR, and the reason this is not just a presence
        # check. A row keyed by PATH is inherited by whatever lands at that
        # path next -- task 93 found exactly that shape in the dream-package
        # exemption, where a resurrection at the retired path would have been
        # granted the exemption SILENTLY. Here the inheritance would grant an
        # impostor up to three unexplained plan-artifact citations. So the row
        # also declares the exact hits it covers: re-activation admits only
        # these, and any other citation at the same path is undeclared.
        "hits": (("private plan document", "Plan A"),) * 3,
    },
}


def active_rows(root=None):
    """The ledger rows IN FORCE for this tree.

    A row carrying `terminal_with` is in force only while that file exists. Its
    absence is a state the ledger must name, not a state it may fail on: the
    monolith's deletion is what the row's own reason nominates as its retirement
    trigger, and a gate that reddens when its subject is removed teaches the
    reviewer to delete the gate.
    """
    root = REPO_ROOT if root is None else root
    live = {}
    for path, row in DECLARED.items():
        terminal = row.get("terminal_with")
        if terminal and not os.path.exists(os.path.join(root, terminal)):
            continue
        live[path] = row
    return live

#: Extensions worth scanning: everything a human or an agent reads.
SCANNED_EXTS = (".py", ".sh", ".md", ".json", ".txt", ".yml", ".yaml", "")

MAX_BYTES = 2 * 1024 * 1024


#: Never walked when a tree has no git index (a staged/copied donor tree).
WALK_PRUNE_DIRS = {".git", "node_modules", "__pycache__", ".venv", "dist",
                   ".pytest_cache", ".mypy_cache"}


def tracked_files(root):
    """Every file the tree ships: tracked paths, else a walk (no git index)."""
    proc = subprocess.run(["git", "-C", root, "ls-files", "-z"],
                          stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    if proc.returncode == 0 and proc.stdout.strip():
        return sorted(rel for rel in
                      proc.stdout.decode("utf-8", "replace").split("\0") if rel)
    out = []
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = sorted(d for d in dirnames if d not in WALK_PRUNE_DIRS)
        for name in sorted(filenames):
            out.append(os.path.relpath(os.path.join(dirpath, name), root))
    return sorted(out)


def _read(path):
    try:
        if os.path.getsize(path) > MAX_BYTES:
            return None
        with open(path, "r", encoding="utf-8") as handle:
            return handle.read()
    except (OSError, UnicodeDecodeError):
        return None


def scan_text(body, location):
    """[(location, label, matched_text)] for every banned reference in `body`."""
    if MARKER in body:
        return []
    hits = []
    for number, line in enumerate(body.split("\n"), start=1):
        for label, pattern in COMPILED:
            for match in pattern.finditer(line):
                hits.append(("%s:%d" % (location, number), label,
                             match.group(0)))
    return hits


def scan_tree(root):
    hits = []
    for rel in tracked_files(root):
        if os.path.splitext(rel)[1] not in SCANNED_EXTS:
            continue
        body = _read(os.path.join(root, rel))
        if body is None:
            continue
        hits.extend(scan_text(body, rel))
    return hits


class DetectionTest(unittest.TestCase):
    """The scanner must bite; a silent pass is the failure mode."""

    def test_each_banned_shape_is_caught(self):
        for text in ("stage via the allowlist (Plan B Task 1)",
                     "the fast path (Plan B Task 14 - AUDIT R1 fix)",
                     "Plan A's Task-7 backfill sets these",
                     "shared skills are always-on (the SB4 default)"):
            self.assertTrue(scan_text(text, "probe.md"), text)

    def test_each_row_reports_its_label_and_the_matched_text(self):
        hits = scan_text("see (Plan B Task 9) and AUDIT R5 and SB4", "p.md")
        self.assertEqual({h[1] for h in hits},
                         {"private plan document", "private audit finding id",
                          "private sub-brief id"})
        self.assertEqual({h[2] for h in hits}, {"Plan B", "AUDIT R5", "SB4"})

    def test_the_line_number_is_reported(self):
        hits = scan_text("clean\nclean\nsee (Plan B Task 9)", "p.md")
        self.assertEqual([h[0] for h in hits], ["p.md:3"])

    def test_the_boundary_cases_do_not_fire(self):
        for text in ("Plan AUTHORING (skeleton/new/add-task)",
                     "the planner writes a plan before executing",
                     "feature 260721-org-kb-fleet-observability, Task 6",
                     "moved to ops.kbsync (wave-6 seam 20)",
                     "degrade, never block (C-4)",
                     "an SBOM lists every dependency",
                     "Plans B and C were both rejected"):
            self.assertEqual(scan_text(text, "p.md"), [], text)

    def test_a_file_carrying_the_marker_is_skipped(self):
        text = "see (Plan B Task 9) -- " + MARKER
        self.assertEqual(scan_text(text, "p.md"), [])


class ThisRepoTest(unittest.TestCase):

    def test_the_scanner_sees_a_real_corpus(self):
        self.assertGreater(len(tracked_files(REPO_ROOT)), 50)

    def test_no_undeclared_file_cites_a_private_plan_artifact(self):
        live = active_rows()
        undeclared = [h for h in scan_tree(REPO_ROOT)
                      if h[0].rsplit(":", 1)[0] not in live]
        self.assertEqual(undeclared, [],
                         "\n".join("%s  %s  %r" % h for h in undeclared))

    def test_the_declared_ledger_shrinks_and_never_goes_stale(self):
        counts = {}
        for location, _, _ in scan_tree(REPO_ROOT):
            path = location.rsplit(":", 1)[0]
            counts[path] = counts.get(path, 0) + 1
        for path, row in active_rows().items():
            self.assertIn(path, counts,
                          "stale row: %s no longer cites a plan artifact; "
                          "delete the row" % path)
            self.assertLessEqual(counts[path], row["count"],
                                 "%s grew from %d to %d hits"
                                 % (path, row["count"], counts[path]))

    def test_every_declared_row_names_an_owner_and_a_reason(self):
        for path, row in DECLARED.items():
            self.assertTrue(row["owner"].startswith("task "), path)
            self.assertGreater(len(row["reason"]), 80, path)
            self.assertGreater(row["count"], 0, path)

    # --- the terminal state, asserted rather than tolerated (task 94) -------
    def test_a_terminal_row_is_IN_FORCE_exactly_while_its_file_exists(self):
        """Both states named, so neither is silent.

        The whole hazard of a terminal row is that "retired" and "never
        mattered" look identical from the outside. Assert the mapping in both
        directions against a scratch root, so the row's retirement is a
        property of the FILE's absence and not of the scan happening to find
        nothing.
        """
        import shutil
        import tempfile
        for path, row in DECLARED.items():
            terminal = row.get("terminal_with")
            if not terminal:
                continue
            root = tempfile.mkdtemp(prefix="plan-artifact-terminal-")
            self.addCleanup(shutil.rmtree, root, True)
            target = os.path.join(root, terminal)
            os.makedirs(os.path.dirname(target), exist_ok=True)
            self.assertNotIn(path, active_rows(root),
                             "%s must retire when %s is absent" % (path, terminal))
            with open(target, "w", encoding="utf-8") as handle:
                handle.write("# placeholder\n")
            self.assertIn(path, active_rows(root),
                          "%s must be in force while %s exists" % (path, terminal))

    def test_a_retired_row_leaves_NO_unscanned_hit_behind(self):
        """Retiring the row may not retire the coverage.

        A row conditioned on a file is only safe because that file's hits go
        away with it. If the tree still produced citations at that path while
        the row was retired, they would be silently unowned -- so prove the
        undeclared scan is what catches them, in the state where the row is
        no longer in force.
        """
        for path, row in DECLARED.items():
            if not row.get("terminal_with"):
                continue
            if path in active_rows():
                continue
            live_hits = [h for h in scan_tree(REPO_ROOT)
                         if h[0].rsplit(":", 1)[0] == path]
            self.assertEqual(live_hits, [],
                             "%s is retired but still cites plan artifacts: %s"
                             % (path, live_hits))

    def test_a_reactivated_row_admits_ONLY_the_hits_it_declared(self):
        """The impostor state: a path-keyed row is inherited by its successor.

        Task 93 found this exact shape in the dream-package exemption, where a
        module landing at the retired path would have inherited the exemption
        with nothing said. Here the inheritance would grant a NEW file at
        `scripts/agentware_legacy.py` up to three unexplained citations. The
        declared `hits` are the anchor: whatever is in force, only these
        (label, text) pairs are covered.
        """
        for path, row in active_rows().items():
            declared = sorted(row["hits"])
            found = sorted((h[1], h[2]) for h in scan_tree(REPO_ROOT)
                           if h[0].rsplit(":", 1)[0] == path)
            self.assertEqual(found, declared,
                             "%s cites something the row does not declare; a "
                             "row keyed by PATH may not cover a successor's "
                             "own leak" % path)

    def test_this_module_is_exempt_from_its_own_content_scan(self):
        body = _read(os.path.abspath(__file__))
        self.assertIn(MARKER, body)
        self.assertEqual(scan_text(body, "self"), [])


if __name__ == "__main__":
    unittest.main()
