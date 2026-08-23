"""Owning unit file for `agentware_lib.features.setupcmd` — task 99 wave 17.

The module carried a `test-ownership` row. `init` is the most-exercised command
in the whole corpus — every hermetic test that needs a knowledge dir runs it —
but exercised is not owned: those runs assert that a SCRATCH KB came out usable,
so they redden when the corpus layout changes and stay green for every decision
`cmd_init` makes on the way there. The step ORDER, the repoint guard's three
distinct answers, and the idempotence of each scaffold step individually are all
invisible to a caller that only checks "the dir works afterwards".

What is asserted here is the module's PROMISE, not its shape:

  * EVERY SCAFFOLD STEP IS IDEMPOTENT ON ITS OWN. Each of the five is driven
    twice in isolation and must report nothing the second time and leave the
    bytes alone — the property that makes `init` safe to re-run, and one that a
    whole-command test cannot attribute to a step.
  * IDEMPOTENT MEANS "BY ABSENCE", NOT "BY CONTENT". An operator's edited
    template or hand-curated roster survives a re-run verbatim. The fixtures
    EDIT the file first, so a step that rewrote identical content would be
    indistinguishable without them.
  * THE STEP ORDER IS LOAD-BEARING AND IS ASSERTED. The repoint guard runs AFTER
    the scaffold (a refused repoint still leaves the requested dir structurally
    valid) and BEFORE the config write (which is the clobber it prevents). Both
    halves are pinned, the second by reading the config back after a refusal.
  * THE REPOINT GUARD HAS THREE ANSWERS, NOT TWO. Allow (no config / same dir /
    --force), refuse-a-scaffold, and refuse-a-LIVE-KB — the last differing only
    in the reason string, which is the whole point of consulting `kb_pointable`.
  * A DENORMALISED PATH IS THE SAME PATH. `~`, `..` and a trailing separator all
    resolve to one dir, so an operator cannot be refused for pointing at the dir
    they are already configured for.

This file owns the WRITER. The module's other verb — `profile`, a pure path
resolver that touches nothing — lives in the sibling `test_setupcmd_profile.py`,
split off when this file crossed the 600-line `tests/**` cap in the commit paying
four ceiling rows down: growing it would have worsened one ratchet in the change
whose purpose is to shrink another. The sibling reuses the `_InitCase` fixture
declared here.

Hermetic: every knowledge dir and every stand-in package root is a fresh
mkdtemp, `HOME_CONFIG` is redirected into the scratch tree, and the config
writer is captured rather than called. No live KB, no git, no network.
"""

from tests._paths import TESTS_DIR

import io
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

import agentware_lib.core.config as config              # noqa: E402
import agentware_lib.core.kblayout as kblayout          # noqa: E402
import agentware_lib.features.setupcmd as setupcmd      # noqa: E402
import agentware_lib.knowledge.index as index           # noqa: E402


class Args(object):
    def __init__(self, **kw):
        for k, v in kw.items():
            setattr(self, k, v)


def _read(path):
    with open(path, "r", encoding="utf-8") as f:
        return f.read()


def _write(path, text):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        f.write(text)
    return path


def _tree(root):
    out = []
    if not os.path.isdir(root):
        return ["<ABSENT>"]
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames.sort()
        for fn in sorted(filenames):
            p = os.path.join(dirpath, fn)
            out.append("%s:%d" % (os.path.relpath(p, root), os.path.getsize(p)))
    return sorted(out)


class _InitCase(unittest.TestCase):

    def setUp(self):
        self.root = tempfile.mkdtemp(prefix="aw-t99w17-init-")
        self.addCleanup(shutil.rmtree, self.root, ignore_errors=True)
        self.kdir = os.path.join(self.root, "kb")
        self.cfg = os.path.join(self.root, "home", "config.env")
        os.makedirs(os.path.dirname(self.cfg))
        self.written = []
        self._patch(config, "HOME_CONFIG", self.cfg)
        self._patch(setupcmd.config, "HOME_CONFIG", self.cfg)
        self._patch(config, "_write_config",
                    lambda k: self.written.append(k))
        self._patch(config, "_persisted_knowledge_dir", lambda: None)
        # A stand-in package root with a KNOWN templates/ dir, so the assertions
        # do not depend on how many templates this checkout happens to ship.
        self.pkg = os.path.join(self.root, "pkg")
        _write(os.path.join(self.pkg, "templates", "learning-template.md"),
               "# learning\n")
        _write(os.path.join(self.pkg, "templates", "skill-template.md"),
               "# skill\n")
        _write(os.path.join(self.pkg, "templates", "notes.txt"), "not a template\n")
        self._patch(setupcmd.paths, "REPO_ROOT", self.pkg)

    def _patch(self, obj, name, value):
        old = getattr(obj, name)
        setattr(obj, name, value)
        self.addCleanup(setattr, obj, name, old)

    def run_init(self, kdir=None, fmt="text", force=False):
        out, err = io.StringIO(), io.StringIO()
        old = sys.stdout, sys.stderr
        sys.stdout, sys.stderr = out, err
        try:
            rc = setupcmd.cmd_init(Args(knowledge_dir=kdir or self.kdir,
                                        format=fmt, force=force))
        finally:
            sys.stdout, sys.stderr = old
        return rc, out.getvalue(), err.getvalue()


# --------------------------------------------------------------------------
# _init_dirs
# --------------------------------------------------------------------------

class InitDirsTest(_InitCase):

    def test_it_creates_the_knowledge_categories_and_the_work_artifacts(self):
        created = setupcmd._init_dirs(self.kdir)
        for sub in index.KNOWLEDGE_SUBDIRS:
            self.assertTrue(os.path.isdir(os.path.join(self.kdir, sub)), sub)
        for sub in kblayout.NON_INDEXED_DIRS:
            self.assertTrue(os.path.isdir(os.path.join(self.kdir, sub)), sub)
        self.assertIn(self.kdir, created)

    def test_the_root_itself_is_created_and_reported(self):
        # The `("",) + ...` head is what makes `init` work on a path that does
        # not exist yet; without it every makedirs below would still succeed by
        # side effect and the ROOT would never appear in the report.
        self.assertIn(self.kdir, setupcmd._init_dirs(self.kdir))

    def test_it_returns_only_what_it_created(self):
        setupcmd._init_dirs(self.kdir)
        self.assertEqual(setupcmd._init_dirs(self.kdir), [])

    def test_a_partially_scaffolded_dir_gains_only_the_missing_ones(self):
        os.makedirs(os.path.join(self.kdir, index.KNOWLEDGE_SUBDIRS[0]))
        created = setupcmd._init_dirs(self.kdir)
        self.assertNotIn(os.path.join(self.kdir, index.KNOWLEDGE_SUBDIRS[0]),
                         created)
        self.assertIn(os.path.join(self.kdir, index.KNOWLEDGE_SUBDIRS[1]),
                      created)


# --------------------------------------------------------------------------
# _init_seed_index
# --------------------------------------------------------------------------

class InitSeedIndexTest(_InitCase):

    def test_an_absent_index_is_seeded_empty_and_valid(self):
        os.makedirs(self.kdir)
        self.assertTrue(setupcmd._init_seed_index(self.kdir))
        data, err = index.load_index(self.kdir)
        self.assertIsNone(err)
        self.assertEqual(data["entries"], [])

    def test_an_existing_index_is_never_clobbered(self):
        os.makedirs(self.kdir)
        p = _write(os.path.join(self.kdir, "index.json"),
                   json.dumps({"entries": [{"id": "keepme"}], "tags": {}}) + "\n")
        before = _read(p)
        self.assertFalse(setupcmd._init_seed_index(self.kdir))
        self.assertEqual(_read(p), before)

    def test_even_an_UNPARSEABLE_index_is_left_alone(self):
        # The guard is EXISTENCE, not validity, and deliberately so: a corrupt
        # index is recoverable (`load_index` rebuilds it from frontmatter), but
        # an index overwritten by a scaffolder is not.
        os.makedirs(self.kdir)
        p = _write(os.path.join(self.kdir, "index.json"), "{not json\n")
        self.assertFalse(setupcmd._init_seed_index(self.kdir))
        self.assertEqual(_read(p), "{not json\n")


# --------------------------------------------------------------------------
# _init_rosters
# --------------------------------------------------------------------------

class InitRostersTest(_InitCase):

    def setUp(self):
        super(InitRostersTest, self).setUp()
        setupcmd._init_dirs(self.kdir)

    def test_every_knowledge_subdir_gets_a_browsable_roster(self):
        setupcmd._init_rosters(self.kdir)
        for sub in index.KNOWLEDGE_SUBDIRS:
            self.assertTrue(
                os.path.isfile(os.path.join(self.kdir, sub, "index.md")), sub)

    def test_the_roster_names_its_own_category(self):
        setupcmd._init_rosters(self.kdir)
        sub = index.KNOWLEDGE_SUBDIRS[0]
        body = _read(os.path.join(self.kdir, sub, "index.md"))
        self.assertTrue(body.startswith("# %s\n" % sub.capitalize()))
        self.assertIn(sub, body)

    def test_an_existing_roster_is_never_rewritten(self):
        # The fixture EDITS the file first: a step that rewrote identical content
        # would be indistinguishable from one that skipped, and the roster is
        # exactly where an operator's curated ordering lives.
        sub = index.KNOWLEDGE_SUBDIRS[0]
        p = _write(os.path.join(self.kdir, sub, "index.md"), "# MINE\n")
        setupcmd._init_rosters(self.kdir)
        self.assertEqual(_read(p), "# MINE\n")

    def test_rosters_are_not_written_into_the_non_indexed_dirs(self):
        setupcmd._init_rosters(self.kdir)
        for sub in kblayout.NON_INDEXED_DIRS:
            self.assertFalse(
                os.path.isfile(os.path.join(self.kdir, sub, "index.md")), sub)


# --------------------------------------------------------------------------
# _init_templates
# --------------------------------------------------------------------------

class InitTemplatesTest(_InitCase):

    def test_only_markdown_templates_are_installed(self):
        installed = setupcmd._init_templates(self.kdir)
        self.assertEqual(installed,
                         ["learning-template.md", "skill-template.md"])
        self.assertFalse(os.path.exists(
            os.path.join(self.kdir, "templates", "notes.txt")))

    def test_the_installed_copy_carries_the_package_bytes(self):
        setupcmd._init_templates(self.kdir)
        self.assertEqual(
            _read(os.path.join(self.kdir, "templates", "skill-template.md")),
            "# skill\n")

    def test_they_are_installed_in_sorted_order_whatever_the_filesystem_says(self):
        # `sorted()` is what makes the REPORT deterministic across machines, and
        # a filesystem that already hands back sorted names (APFS does) cannot
        # tell it from a raw `listdir`. The order is therefore FORCED: nothing
        # else in the corpus separates the two.
        real = os.listdir
        self._patch(os, "listdir", lambda p: list(reversed(sorted(real(p)))))
        self.assertEqual(setupcmd._init_templates(self.kdir),
                         ["learning-template.md", "skill-template.md"])

    def test_the_returned_list_is_what_was_copied_not_what_exists(self):
        setupcmd._init_templates(self.kdir)
        self.assertEqual(setupcmd._init_templates(self.kdir), [])

    def test_an_operator_edited_template_survives_a_re_run(self):
        setupcmd._init_templates(self.kdir)
        p = os.path.join(self.kdir, "templates", "skill-template.md")
        _write(p, "# EDITED BY THE OPERATOR\n")
        setupcmd._init_templates(self.kdir)
        self.assertEqual(_read(p), "# EDITED BY THE OPERATOR\n")

    def test_a_package_with_no_templates_dir_installs_none_and_is_not_an_error(self):
        # A partial checkout is still a usable toolkit; this must not raise and
        # must not create an empty templates/ dir in the operator's KB.
        self._patch(setupcmd.paths, "REPO_ROOT", os.path.join(self.root, "nope"))
        self.assertEqual(setupcmd._init_templates(self.kdir), [])
        self.assertFalse(os.path.exists(os.path.join(self.kdir, "templates")))

    def test_a_newly_added_package_template_is_picked_up_on_a_re_run(self):
        setupcmd._init_templates(self.kdir)
        _write(os.path.join(self.pkg, "templates", "project-template.md"),
               "# project\n")
        self.assertEqual(setupcmd._init_templates(self.kdir),
                         ["project-template.md"])


# --------------------------------------------------------------------------
# _init_refuse_repoint — three answers, not two
# --------------------------------------------------------------------------

class InitRefuseRepointTest(_InitCase):

    def _refuse(self, as_json=False, force=False):
        out, err = io.StringIO(), io.StringIO()
        old = sys.stdout, sys.stderr
        sys.stdout, sys.stderr = out, err
        try:
            rc = setupcmd._init_refuse_repoint(self.kdir, as_json, force)
        finally:
            sys.stdout, sys.stderr = old
        return rc, out.getvalue(), err.getvalue()

    def test_no_config_FILE_at_all_is_always_allowed(self):
        # The TRIGGER is the config file's existence, so the persisted reader is
        # made to name a DIFFERENT dir: with it dry, "no config file" and "no
        # configured dir" coincide and the first-run arm passes vacuously.
        self._patch(config, "_persisted_knowledge_dir",
                    lambda: os.path.join(self.root, "elsewhere"))
        self.assertFalse(os.path.isfile(self.cfg))
        self.assertIsNone(self._refuse()[0])

    def test_force_is_always_allowed_even_against_a_live_kb(self):
        _write(self.cfg, "x\n")
        self._patch(config, "_persisted_knowledge_dir",
                    lambda: os.path.join(self.root, "elsewhere"))
        self.assertIsNone(self._refuse(force=True)[0])

    def test_an_empty_persisted_value_is_not_a_repoint(self):
        # "" is a config that names nothing; refusing on it would brick a
        # half-written config file with no way forward except --force.
        _write(self.cfg, "x\n")
        self._patch(config, "_persisted_knowledge_dir", lambda: "")
        self.assertIsNone(self._refuse()[0])

    def test_the_same_dir_is_an_idempotent_no_op(self):
        _write(self.cfg, "x\n")
        self._patch(config, "_persisted_knowledge_dir", lambda: self.kdir)
        self.assertIsNone(self._refuse()[0])

    def test_a_denormalised_path_to_the_same_dir_still_compares_equal(self):
        # `..` and a trailing separator are the same dir; a string compare would
        # refuse an operator for pointing at the dir they already use.
        _write(self.cfg, "x\n")
        weird = os.path.join(self.root, "x", "..", "kb") + os.sep
        self._patch(config, "_persisted_knowledge_dir", lambda: weird)
        self.assertIsNone(self._refuse()[0])

    def test_a_different_dir_is_refused_with_exit_three(self):
        _write(self.cfg, "x\n")
        self._patch(config, "_persisted_knowledge_dir",
                    lambda: os.path.join(self.root, "elsewhere"))
        rc, out, err = self._refuse()
        self.assertEqual((rc, out), (3, ""))
        self.assertIn("refusing to repoint", err)
        self.assertIn("Re-run with --force", err)

    def test_the_refusal_names_both_dirs_so_the_operator_can_tell_them_apart(self):
        _write(self.cfg, "x\n")
        other = os.path.join(self.root, "elsewhere")
        self._patch(config, "_persisted_knowledge_dir", lambda: other)
        _rc, _out, err = self._refuse()
        self.assertIn(other, err)
        self.assertIn(self.kdir, err)

    def test_a_non_conformant_dir_is_refused_with_its_reason(self):
        _write(self.cfg, "x\n")
        other = os.path.join(self.root, "elsewhere")
        os.makedirs(other)
        self._patch(config, "_persisted_knowledge_dir", lambda: other)
        _rc, _out, err = self._refuse()
        pointable, reason = kblayout.kb_pointable(other)
        self.assertFalse(pointable)
        self.assertIn(reason, err)
        self.assertNotIn("conformant knowledge base", err)

    def test_a_LIVE_kb_is_refused_with_a_different_reason(self):
        # The third answer. It differs from the second ONLY in the reason string,
        # which is the whole point of consulting kb_pointable — and a guard that
        # skipped the call would be invisible without this pair.
        _write(self.cfg, "x\n")
        other = os.path.join(self.root, "live")
        _write(os.path.join(other, "MAIN.md"), "# live\n")
        _write(os.path.join(other, "index.json"),
               json.dumps({"entries": [], "tags": {}}) + "\n")
        _write(os.path.join(other, ".initialized"), "handle: someone\n")
        self._patch(config, "_persisted_knowledge_dir", lambda: other)
        _rc, _out, err = self._refuse()
        self.assertIn("conformant knowledge base", err)

    def test_the_json_refusal_carries_the_machine_readable_diagnosis(self):
        _write(self.cfg, "x\n")
        other = os.path.join(self.root, "elsewhere")
        self._patch(config, "_persisted_knowledge_dir", lambda: other)
        rc, out, _err = self._refuse(as_json=True)
        payload = json.loads(out)
        self.assertEqual(rc, 3)
        self.assertFalse(payload["ok"])
        self.assertEqual(payload["error"], "kb-already-configured")
        self.assertEqual(payload["configured_dir"], other)
        self.assertEqual(payload["requested_dir"], self.kdir)
        self.assertFalse(payload["configured_dir_pointable"])
        self.assertIn("--force", payload["hint"])

    def test_the_text_refusal_is_on_stderr_and_the_json_on_stdout(self):
        # Both, in the json mode: the prose goes to a human on stderr and the
        # document to a pipe on stdout, so a `--format json` caller can parse
        # stdout without stripping the explanation.
        _write(self.cfg, "x\n")
        self._patch(config, "_persisted_knowledge_dir",
                    lambda: os.path.join(self.root, "elsewhere"))
        _rc, out, err = self._refuse(as_json=True)
        self.assertTrue(out.startswith("{"))
        self.assertIn("refusing to repoint", err)


# --------------------------------------------------------------------------
# _init_report
# --------------------------------------------------------------------------

class InitReportTest(_InitCase):

    def _report(self, as_json, seeded, created, installed, gitignore="created"):
        out = io.StringIO()
        old = sys.stdout
        sys.stdout = out
        try:
            setupcmd._init_report(self.kdir, as_json, seeded, created, installed,
                                  gitignore)
        finally:
            sys.stdout = old
        return out.getvalue()

    def test_a_seeded_index_and_a_kept_one_read_differently(self):
        self.assertIn("(seeded)", self._report(False, True, [], []))
        self.assertIn("(kept)", self._report(False, False, [], []))

    def test_the_templates_line_is_omitted_when_nothing_was_installed(self):
        self.assertNotIn("templates installed",
                         self._report(False, True, [], []))
        self.assertIn("templates installed: a.md, b.md",
                      self._report(False, True, [], ["a.md", "b.md"]))

    def test_the_text_report_points_at_the_next_step(self):
        body = self._report(False, True, [], [])
        self.assertIn("onboarding", body)
        self.assertIn(".initialized", body)

    def test_the_json_report_carries_every_step_result(self):
        payload = json.loads(self._report(True, True, ["/a"], ["t.md"], "kept"))
        self.assertEqual(payload["ok"], True)
        self.assertEqual(payload["knowledge_dir"], self.kdir)
        self.assertEqual(payload["seeded_index"], True)
        self.assertEqual(payload["created_dirs"], ["/a"])
        self.assertEqual(payload["installed_templates"], ["t.md"])
        self.assertEqual(payload["gitignore"], "kept")
        self.assertEqual(payload["config"], self.cfg)

    def test_the_json_report_prints_nothing_else(self):
        body = self._report(True, True, [], [])
        self.assertTrue(body.startswith("{"))
        self.assertNotIn("INITIALIZED", body)


# --------------------------------------------------------------------------
# cmd_init — the order the steps run in
# --------------------------------------------------------------------------

class CmdInitOrderTest(_InitCase):

    def test_a_first_run_reports_every_step_as_having_done_something(self):
        rc, out, err = self.run_init(fmt="json")
        self.assertEqual((rc, err), (0, ""))
        payload = json.loads(out)
        self.assertTrue(payload["seeded_index"])
        self.assertTrue(payload["created_dirs"])
        self.assertEqual(payload["installed_templates"],
                         ["learning-template.md", "skill-template.md"])
        self.assertEqual(payload["gitignore"], "created")
        self.assertEqual(self.written, [self.kdir])

    def test_a_second_run_reports_every_step_as_a_no_op(self):
        self.run_init()
        before = _tree(self.kdir)
        rc, out, _err = self.run_init(fmt="json")
        payload = json.loads(out)
        self.assertEqual(rc, 0)
        self.assertFalse(payload["seeded_index"])
        self.assertEqual(payload["created_dirs"], [])
        self.assertEqual(payload["installed_templates"], [])
        self.assertEqual(payload["gitignore"], "kept")
        self.assertEqual(_tree(self.kdir), before)

    def test_the_home_path_is_expanded_and_the_path_normalised(self):
        home = os.path.join(self.root, "fakehome")
        os.makedirs(home)
        old = os.environ.get("HOME")
        os.environ["HOME"] = home
        try:
            rc, out, _err = self.run_init(kdir="~/kb", fmt="json")
        finally:
            if old is None:
                os.environ.pop("HOME", None)
            else:
                os.environ["HOME"] = old
        self.assertEqual(rc, 0)
        self.assertEqual(json.loads(out)["knowledge_dir"],
                         os.path.join(home, "kb"))

    def test_a_refused_repoint_leaves_the_requested_dir_scaffolded(self):
        # The guard runs AFTER the scaffold on purpose: a refused run still
        # leaves the requested dir structurally valid, which is what makes
        # re-running with --force a no-op rather than a second scaffold.
        _write(self.cfg, "x\n")
        self._patch(config, "_persisted_knowledge_dir",
                    lambda: os.path.join(self.root, "elsewhere"))
        rc, _out, _err = self.run_init()
        self.assertEqual(rc, 3)
        self.assertTrue(os.path.isfile(os.path.join(self.kdir, "index.json")))

    def test_a_refused_repoint_never_writes_the_config(self):
        # ...and BEFORE the config write, which is the clobber it exists to
        # prevent. The two halves together pin the guard's position exactly.
        _write(self.cfg, "x\n")
        self._patch(config, "_persisted_knowledge_dir",
                    lambda: os.path.join(self.root, "elsewhere"))
        self.run_init()
        self.assertEqual(self.written, [])

    def test_a_refused_repoint_prints_no_success_report(self):
        _write(self.cfg, "x\n")
        self._patch(config, "_persisted_knowledge_dir",
                    lambda: os.path.join(self.root, "elsewhere"))
        _rc, out, _err = self.run_init()
        self.assertEqual(out, "")

    def test_force_after_a_refusal_completes_and_writes_the_config(self):
        _write(self.cfg, "x\n")
        self._patch(config, "_persisted_knowledge_dir",
                    lambda: os.path.join(self.root, "elsewhere"))
        self.run_init()
        rc, _out, _err = self.run_init(force=True)
        self.assertEqual(rc, 0)
        self.assertEqual(self.written, [self.kdir])

    def test_the_gitignore_excludes_the_transcripts(self):
        self.run_init()
        body = _read(os.path.join(self.kdir, ".gitignore"))
        self.assertIn("logs/", body)

    def test_no_initialized_sentinel_is_ever_written(self):
        # Onboarding is the author of that file; an `init` that wrote it would
        # let a never-interviewed workspace pass every bootstrap gate.
        self.run_init()
        self.assertFalse(os.path.exists(os.path.join(self.kdir, ".initialized")))


# --------------------------------------------------------------------------
# the export surface
# --------------------------------------------------------------------------

class ExportSurfaceTest(unittest.TestCase):
    """The wave's six new helpers are deliberately ABSENT from the facade
    surface: they have no monolith counterpart, and exporting them would widen
    the surface the parity machinery pins."""

    def test_exports_are_exactly_the_two_handlers(self):
        self.assertEqual(setupcmd._EXPORTS, ["cmd_init", "cmd_profile"])
        self.assertEqual(sorted(setupcmd.__all__), sorted(setupcmd._EXPORTS))

    def test_the_new_helpers_exist_but_are_not_exported(self):
        for name in ("_init_dirs", "_init_seed_index", "_init_rosters",
                     "_init_templates", "_init_refuse_repoint", "_init_report"):
            self.assertTrue(hasattr(setupcmd, name), name)
            self.assertNotIn(name, setupcmd._EXPORTS)


if __name__ == "__main__":
    unittest.main()
