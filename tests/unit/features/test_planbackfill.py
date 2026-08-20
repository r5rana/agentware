"""Owning unit file for `agentware_lib.features.planbackfill` (R-MOD-06).

Migration 001 — plan state-backfill (feature 260710, Task 4). Verifies the
dogfood migration that stamps a derived `> Status:` on every legacy plan: derives
done vs draft, never overwrites an existing status, skips the live loop feature
and unparseable plans, covers work/auto/ workorders, is idempotent, and runs
through `migrate --apply` (additive tier).

RENAMED from `test_backfill_migration` at task 90, when migration 001's body moved
out of the registry module into `features/planbackfill` (registering migration 003
pushed `migratecmd` past its 600-line cap, and the split went where the file's own
docstring already declared two obligations). The rename is a RE-AIM, not new
coverage: this file was always this body's oracle, and it is now named for the
module it owns, so the ownership gate can see it. Recorded in
`tests/_test_layout.RELOCATIONS`, because a node id embeds its module path and the
preserved-surface freeze reads a moved id as a lost one.

Test names are prefixed `test_backfill_` (a distinct -k handle) and are deliberately
NOT renamed with the file: the freeze cannot tell a rename from a deletion.

TASK 97 added four methods to the SAME class rather than a subclass or a second
file: the annotated-status defect is a property of this body's already-stamped
guard, and every one of them needs the identical synthetic-KB harness. They pin
that "NEVER overwrites an existing status" is asked with the WRITER's own
predicate — the read/write disagreement that let migration 001 DELETE the two
hand-annotated `> Status:` lines the task-64 rehearsal found on the operator's
real corpus.

Runner: python3 -m unittest tests.unit.features.test_planbackfill -v
"""

import os
import shutil
import tempfile
import time
import unittest

try:
    from tests._fixtures import run_cli as _raw_run_cli, build_synthetic_kb, load_lib
except ImportError:
    from _fixtures import run_cli as _raw_run_cli, build_synthetic_kb, load_lib

#: Migration 001's body moved to the lib with the runner at task 37, and out of the
#: registry module into its own at task 90. This is the module under test.
MIGRATE_MOD = "agentware_lib.features.planbackfill"
#: The plan primitives the assertions read are OWNED elsewhere and only reached
#: through the migration: naming their modules keeps the ownership visible.
PLANEDIT_MOD = "agentware_lib.ops.planauthor.planedit"
PLANSTATE_MOD = "agentware_lib.ops.planauthor.planstate"
#: The REGISTRY that schedules this body — reached only for the schema version the
#: `migrate --apply` assertion reads, never for the body itself (task 90).
REGISTRY_MOD = "agentware_lib.features.migratecmd"


def _legacy_plan(feature, done=True):
    """A minimal plan-shaped file WITHOUT a `> Status:` header (legacy)."""
    marks = ("- ✅ **1** task one\n  *Verify:* ok\n"
             "- ✅ **2** task two\n  *Verify:* ok\n") if done else (
             "- ✅ **1** task one\n  *Verify:* ok\n"
             "- ⬜ **2** task two\n  *Verify:* ok\n")
    return ("# Plan — %s\n\n> Feature: `%s`\n> Created: 2026-01-01\n"
            "> Type: feature work\n\n---\n\n## Tasks\n\n%s\n---\n\n"
            "## Acceptance criteria\n\n- [ ] it works\n\n"
            "<promise>%s_COMPLETE</promise>\n"
            % (feature, feature, marks, feature.upper().replace("-", "_")))


#: The two REAL hand-annotated status lines migration 001 destroyed when the
#: task-64 rehearsal ran it over the operator's 1,157-entry corpus, verbatim from
#: `work/260813-modular-core-adoption/evidence/task64/flat-from-0/diff.txt`. They
#: are the fixture rather than a paraphrase because the defect is a REGEX
#: disagreement: a hand-written approximation is exactly the well-formed input on
#: which the two patterns agree, and would prove nothing.
#:
#: ONE token is changed: the operator's handle is replaced by the task-83
#: `<neutralised>` placeholder, because the repo ships no operator identity
#: (R-LOC-03) and the F-arm of `tests/_repo_standard.py` reports it. Nothing the
#: regexes discriminate on moves — the parenthetical, the comma, the bold markup
#: and the backticked flags are all still there, and those are what the strict
#: end-anchored reader cannot parse.
ANNOTATED_STATUS_LINES = (
    "> Status: draft (not ready to be executed, TBD checked by <neutralised>, "
    "created in a single batch by agent)",
    "> Status: **ready** (lints clean `--strict`, premises ground `--strict`; "
    "all cited refs re-verified against live code)",
)


class BackfillTestCase(unittest.TestCase):
    def setUp(self):
        self.kdir = tempfile.mkdtemp(prefix="agentware-backfill-")
        self.addCleanup(shutil.rmtree, self.kdir, True)
        build_synthetic_kb(self.kdir)
        self.mod = load_lib(MIGRATE_MOD)
        self.planedit = load_lib(PLANEDIT_MOD)
        self.planstate = load_lib(PLANSTATE_MOD)
        self.registry = load_lib(REGISTRY_MOD)

    def run_cli(self, argv):
        try:
            return _raw_run_cli(argv, self.kdir)
        except SystemExit as exc:
            code = exc.code if isinstance(exc.code, int) else 1
            return code, "", "SystemExit(%r)" % (exc.code,)

    def make(self, feature, text=None, done=True):
        d = os.path.join(self.kdir, "work", feature)
        os.makedirs(d, exist_ok=True)
        with open(os.path.join(d, "plan.md"), "w", encoding="utf-8") as f:
            f.write(text if text is not None else _legacy_plan(feature, done=done))
        return os.path.join(d, "plan.md")

    def status_of(self, feature):
        with open(self.planedit._plan_path(self.kdir, feature)) as f:
            return self.planstate._read_plan_status(f.read())

    def test_backfill_derives_done_for_all_checked(self):
        self.make("260101-done", done=True)
        self.mod._run_state_backfill(self.kdir, now_iso="2026-07-10T00:00:00Z")
        state, cnt = self.status_of("260101-done")
        self.assertEqual((state, cnt), ("done", 1))

    def test_backfill_derives_draft_for_open_markers(self):
        self.make("260101-open", done=False)
        self.mod._run_state_backfill(self.kdir, now_iso="2026-07-10T00:00:00Z")
        state, _c = self.status_of("260101-open")
        self.assertEqual(state, "draft")

    def test_backfill_appends_status_log_line(self):
        self.make("260101-log", done=True)
        self.mod._run_state_backfill(self.kdir, now_iso="2026-07-10T00:00:00Z")
        with open(self.planedit._plan_path(self.kdir, "260101-log")) as f:
            text = f.read()
        self.assertIn("## Status log", text)
        self.assertIn("(none) -> done by migration-001 reason: backfilled by "
                      "migration-001", text)

    def test_backfill_never_overwrites_existing_status(self):
        # a plan that already carries a status (e.g. running)
        base = _legacy_plan("260101-run", done=True).replace(
            "> Created: 2026-01-01\n",
            "> Created: 2026-01-01\n> Status: running\n")
        self.make("260101-run", text=base)
        summary = self.mod._run_state_backfill(self.kdir)
        self.assertIn("260101-run", summary["skipped_existing"])
        self.assertEqual(self.status_of("260101-run")[0], "running")

    def test_backfill_skips_live_feature(self):
        self.make("260101-live", done=True)
        loop = os.path.join(self.kdir, "work", "260101-live", ".loop")
        os.makedirs(loop, exist_ok=True)
        with open(os.path.join(loop, "live-stream.log"), "w") as f:
            f.write("x")   # fresh liveness -> is_session_active returns it
        summary = self.mod._run_state_backfill(self.kdir)
        self.assertIn("260101-live", summary["skipped_live"])
        self.assertEqual(self.status_of("260101-live")[1], 0)   # no status stamped

    def test_backfill_covers_auto_workorders(self):
        self.make(os.path.join("auto", "260101-wo"), done=True)
        self.mod._run_state_backfill(self.kdir)
        state, cnt = self.status_of("auto/260101-wo")
        self.assertEqual((state, cnt), ("done", 1))

    def test_backfill_skips_unparseable(self):
        d = os.path.join(self.kdir, "work", "260101-junk")
        os.makedirs(d)
        with open(os.path.join(d, "plan.md"), "w") as f:
            f.write("this is not a plan at all, no headers\n")
        summary = self.mod._run_state_backfill(self.kdir)
        self.assertIn("260101-junk", summary["skipped_unparseable"])

    def test_backfill_idempotent(self):
        self.make("260101-a", done=True)
        self.make("260101-b", done=False)
        first = self.mod._run_state_backfill(self.kdir)
        second = self.mod._run_state_backfill(self.kdir)
        self.assertEqual(len(first["stamped"]), 2)
        self.assertEqual(len(second["stamped"]), 0)

    def test_backfill_runs_via_migrate_apply(self):
        """`migrate --apply` runs migration 001 and stamps the KB up to date.

        The schema assertion is against `KB_SCHEMA_VERSION`, not the literal 1
        it was written as: this file owns migration 001's behaviour, and 001 is
        additive, so a LATER registered migration (task 63's team-mode scaffold
        is the first) legitimately advances the same stamp. Pinning the literal
        made this file fail for a change it does not describe, while asserting
        nothing 001 is responsible for — the `> Status:` line above is what
        proves 001 ran, and it is unchanged.
        """
        self.make("260101-mig", done=True)
        code, out, _e = self.run_cli(["migrate", "--apply"])
        self.assertEqual(code, 0, out)
        self.assertEqual(self.status_of("260101-mig")[0], "done")
        with open(os.path.join(self.kdir, ".schema")) as f:
            self.assertEqual(int(f.read().strip()),
                             self.registry.KB_SCHEMA_VERSION)

    def _annotated_plan(self, feature, status_line):
        base = _legacy_plan(feature, done=True).replace(
            "> Created: 2026-01-01\n",
            "> Created: 2026-01-01\n%s\n" % status_line)
        return self.make(feature, text=base)

    def test_backfill_skips_annotated_status_byte_identical(self):
        """Each reproduced line: reported skipped_existing, file untouched."""
        for i, line in enumerate(ANNOTATED_STATUS_LINES):
            feat = "260101-annot%d" % i
            path = self._annotated_plan(feat, line)
            with open(path, "rb") as f:
                before = f.read()
            summary = self.mod._run_state_backfill(
                self.kdir, now_iso="2026-07-10T00:00:00Z")
            self.assertIn(feat, summary["skipped_existing"], line)
            self.assertNotIn(feat, [s["feature"] for s in summary["stamped"]])
            with open(path, "rb") as f:
                self.assertEqual(f.read(), before, "migration rewrote %s" % line)

    def test_backfill_annotated_status_survives_full_migration_chain(self):
        """The two real plans survive a schema-0 -> HEAD `migrate --apply` chain.

        The plan's criterion says "schema-0 -> 2"; the chain now runs to
        `KB_SCHEMA_VERSION` (003 landed at task 90), and asserting the whole chain
        is the superset — no later migration may rewrite these bytes either.
        """
        paths = {}
        for i, line in enumerate(ANNOTATED_STATUS_LINES):
            feat = "260101-chain%d" % i
            paths[feat] = self._annotated_plan(feat, line)
        before = {}
        for feat, path in paths.items():
            with open(path, "rb") as f:
                before[feat] = f.read()
        code, out, _e = self.run_cli(["migrate", "--apply"])
        self.assertEqual(code, 0, out)
        with open(os.path.join(self.kdir, ".schema")) as f:
            self.assertEqual(int(f.read().strip()),
                             self.registry.KB_SCHEMA_VERSION)
        for feat, path in paths.items():
            with open(path, "rb") as f:
                self.assertEqual(f.read(), before[feat],
                                 "migration chain rewrote %s" % feat)

    def test_backfill_guard_asks_the_writers_own_question(self):
        """The guard's predicate and the writer's REMOVAL must be one predicate.

        Asserted as a coupling rather than by re-stating the regex: for every
        shape, "the guard counts this line" must equal "the writer would delete
        it" — the property whose absence WAS the bug. `> Status:draft` is the
        deliberate exception in the other direction (the strict reader parses it,
        the writer leaves it), and it must still count, or the migration stamps a
        second header onto a plan that already has one.
        """
        for line in ANNOTATED_STATUS_LINES + ("> Status: running",
                                              ">  Status:   done  ",
                                              "> Status: draft <!-- keep -->"):
            deleted = line not in self.planstate._set_header_line(
                [line], "Status", "done")
            self.assertTrue(deleted, "writer did not delete %r" % line)
            self.assertGreaterEqual(
                self.mod._backfill_existing_status_lines(line), 1,
                "guard cannot see a line the writer deletes: %r" % line)
        for line in ("> Statuses: draft", "text > Status: draft", "# Status: x"):
            self.assertEqual(self.mod._backfill_existing_status_lines(line), 0,
                             "guard over-matched %r" % line)

    def test_backfill_unparseable_status_is_not_duplicated(self):
        """`> Status:draft` (no space) parses for the READER, not for the writer.

        Guarding on the writer's pattern ALONE would leave it in place and insert
        a second header — a duplicate the plan lint reports as an error — so the
        guard is the union of both predicates.
        """
        base = _legacy_plan("260101-nospace", done=True).replace(
            "> Created: 2026-01-01\n", "> Created: 2026-01-01\n> Status:draft\n")
        path = self.make("260101-nospace", text=base)
        with open(path, "rb") as f:
            before = f.read()
        summary = self.mod._run_state_backfill(self.kdir)
        self.assertIn("260101-nospace", summary["skipped_existing"])
        with open(path, "rb") as f:
            self.assertEqual(f.read(), before)


if __name__ == "__main__":
    unittest.main()
