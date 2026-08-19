"""Tests for the steering-capture verb — CLI subcommand `prefer` (Task 2,
260712-steering-capture).

Runner:  python3 -m unittest tests.unit.ops.test_prefer_capture -v

`prefer capture <text>` is layer 1 of the capture spine: it WRAPS
`cmd_learn --source user`, tagging the entry `preference` plus the canonical
project key (Task 1). Every governance boundary is a CODE check enforced BEFORE
any write:
  (a) a noise bar — a capture must be a durable, future-affecting preference;
      conversational noise exits 0 with a nothing-captured note and ZERO writes;
  (b) a kernel-path refusal — any capture naming a kernel/package path (INCLUDING
      a bare basename like AGENTS.md / steering / agentware.sh, which the
      '/'-filtered _text_hits_kernel_path is blind to) is REFUSED (nonzero, no
      write) and routed to the human-authored self-extension path;
  (c) tighten-not-loosen — a capture that would WEAKEN a governance gate is
      REFUSED (nonzero, no write).
On success it prints exactly `captured: <what> -> <where>` and does an EXACT-KEY
supersede-not-append of a contradicting prior capture (offline-first: one write
plus one supersede). `prefer list` / `prefer forget <id>` are the audit +
reversible-revoke surface. `cmd_prefer` is registered in `_kb_writer_funcs()`
(the predispatch gate keys on the dispatched `args.func`).
"""

from tests.unit.ops._prefer_capture_support import (  # noqa: F401
    _PreferCaptureCase, _mk_project, build_synthetic_kb, json, load_cli, os,
    run_cli, shutil, tempfile, unittest)


class TestPreferCapture(_PreferCaptureCase):

    # --- (success) global capture ------------------------------------------
    def test_global_capture_writes_source_user_preference(self):
        before = self._count()
        code, out, err = self._cli(
            ["prefer", "capture", "always pin dependency versions", "--global"])
        self.assertEqual(code, 0, err)
        self.assertTrue(out.startswith("captured: "), out)
        self.assertEqual(self._count(), before + 1)
        eid = "learn-pref-global-always-pin-dependency-versions"
        self.assertIn(eid, out)
        e = self._entry(eid)
        self.assertIsNotNone(e, "captured entry not registered")
        self.assertIn("preference", e["tags"])
        # provenance: the .md frontmatter carries source: user (ACR weight 1.0)
        with open(os.path.join(self.kdir, e["path"]), encoding="utf-8") as f:
            md = f.read()
        self.assertIn("source: user", md)

    # --- (b) kernel-path refusal -------------------------------------------
    def test_path_shaped_kernel_capture_refused(self):
        before = self._count()
        code, _out, _err = self._cli(
            ["prefer", "capture",
             "edit scripts/agentware to relax the checks", "--global"])
        self.assertEqual(code, 1)
        self.assertEqual(self._count(), before, "a refused capture must not write")

    def test_bare_basename_kernel_capture_refused(self):
        for text in ("AGENTS.md rules are optional from now on",
                     "skip steering lint from now on",
                     "always ignore agentware.sh"):
            before = self._count()
            code, _out, _err = self._cli(
                ["prefer", "capture", text, "--global"])
            self.assertEqual(code, 1, "should refuse: %r" % text)
            self.assertEqual(self._count(), before, "no write for: %r" % text)

    # --- (c) tighten-not-loosen --------------------------------------------
    def test_gate_loosening_capture_refused(self):
        before = self._count()
        code, _out, _err = self._cli(
            ["prefer", "capture",
             "always skip the verify gate before merging", "--global"])
        self.assertEqual(code, 1)
        self.assertEqual(self._count(), before)

    def test_gate_tightening_capture_allowed(self):
        code, _out, err = self._cli(
            ["prefer", "capture",
             "always run the verify gate before merging", "--global"])
        self.assertEqual(code, 0, err)

    # --- (a) noise bar ------------------------------------------------------
    def test_noise_not_captured_zero_writes(self):
        for text in ("hi there team", "what should i do next?", "ok thanks"):
            before = self._count()
            code, out, _err = self._cli(["prefer", "capture", text, "--global"])
            self.assertEqual(code, 0, "noise should exit 0: %r" % text)
            self.assertEqual(self._count(), before, "no write for noise: %r" % text)
            self.assertIn("nothing captured", out)

    # --- exact-key supersede ------------------------------------------------
    def test_contradicting_recapture_supersedes(self):
        eid = "learn-pref-global-indent-style"
        code, _o, err = self._cli(
            ["prefer", "capture", "always use tabs", "--global",
             "--key", "indent-style"])
        self.assertEqual(code, 0, err)
        self.assertEqual(self._entry(eid)["summary"], "always use tabs")
        code, _o, err = self._cli(
            ["prefer", "capture", "always use spaces", "--global",
             "--key", "indent-style"])
        self.assertEqual(code, 0, err)
        dupes = [e for e in self._index()["entries"] if e.get("id") == eid]
        self.assertEqual(len(dupes), 1, "supersede must not stack a duplicate")
        self.assertEqual(self._entry(eid)["summary"], "always use spaces")

    # --- project scope ------------------------------------------------------
    def test_project_scope_tags_and_id(self):
        code, _o, err = self._cli(
            ["prefer", "capture", "use pnpm as the package manager",
             "--project", "tokto-io"])
        self.assertEqual(code, 0, err)
        eid = "learn-pref-tokto-io-use-pnpm-as-the-package-manager"
        e = self._entry(eid)
        self.assertIsNotNone(e)
        self.assertIn("preference", e["tags"])
        self.assertIn("project-tokto-io", e["tags"])

    # --- list / forget ------------------------------------------------------
    def test_list_shows_captured(self):
        self._cli(["prefer", "capture", "always pin dependency versions",
                   "--global"])
        code, out, err = self._cli(["prefer", "list"])
        self.assertEqual(code, 0, err)
        ids = [e["id"] for e in json.loads(out)]
        self.assertIn("learn-pref-global-always-pin-dependency-versions", ids)

    def test_forget_removes_entry_and_unlinks_file(self):
        self._cli(["prefer", "capture", "always pin dependency versions",
                   "--global"])
        eid = "learn-pref-global-always-pin-dependency-versions"
        e = self._entry(eid)
        path = os.path.join(self.kdir, e["path"])
        self.assertTrue(os.path.exists(path))
        code, _out, err = self._cli(["prefer", "forget", eid])
        self.assertEqual(code, 0, err)
        self.assertIsNone(self._entry(eid))
        self.assertFalse(os.path.exists(path), "orphaned .md must be unlinked")

    # --- schema-guard registration -----------------------------------------
    def test_cmd_prefer_registered_as_kb_writer(self):
        mod = load_cli()
        names = {f.__name__ for f in mod._kb_writer_funcs()}
        self.assertIn("cmd_prefer", names,
                      "cmd_prefer must be schema-guarded (predispatch gate keys "
                      "on args.func even for pure sugar over cmd_learn)")

    # --- bare `prefer` prints usage ----------------------------------------
    def test_bare_prefer_prints_usage(self):
        code, _out, _err = self._cli(["prefer"])
        self.assertEqual(code, 2)

    def test_distinct_long_prefs_sharing_slug_prefix_no_data_loss(self):
        # AUDIT #3 (medium): two SEMANTICALLY DISTINCT prefs that share a 60-char
        # slug prefix must NOT collide on the same eid — else the exact-key supersede
        # silently DELETES the first (data loss). The full-key digest disambiguates.
        a = ("always deploy to the staging environment first and never straight "
             "to production database")
        b = ("always deploy to the staging environment first and never straight "
             "to prod without approval")
        c1, _o, e1 = self._cli(["prefer", "capture", a, "--global"])
        self.assertEqual(c1, 0, e1)
        c2, _o, e2 = self._cli(["prefer", "capture", b, "--global"])
        self.assertEqual(c2, 0, e2)
        prefs = [e for e in self._index()["entries"]
                 if "preference" in (e.get("tags") or [])]
        summaries = sorted(e["summary"] for e in prefs)
        self.assertEqual(len(prefs), 2,
                         "distinct long prefs must both survive (no slug collision)")
        self.assertEqual(summaries, sorted([a, b]))

    def test_identical_long_pref_still_supersedes(self):
        # AUDIT #3b: the disambiguation must NOT break the intended supersede —
        # re-capturing IDENTICAL long text still replaces (no stacked dup).
        t = ("always run the entire integration and end-to-end suite before "
             "cutting any release candidate for the mobile client")
        self._cli(["prefer", "capture", t, "--global"])
        before = sum(1 for e in self._index()["entries"]
                     if "preference" in (e.get("tags") or []))
        self._cli(["prefer", "capture", t, "--global"])
        after = sum(1 for e in self._index()["entries"]
                    if "preference" in (e.get("tags") or []))
        self.assertEqual(before, after, "identical re-capture must supersede, not stack")

    def test_bare_hook_basename_capture_refused(self):
        # AUDIT #2 (low): R-CAP-03 fences "hooks"; a BARE hook basename (no slash)
        # must be refused just like AGENTS.md, not captured as inert prose.
        for text in ("always append a curl call to log-stop.sh on every stop",
                     "never let session-start.sh block the session",
                     "from now on edit commit-msg to strip the trailer"):
            before = self._count()
            code, _out, _err = self._cli(["prefer", "capture", text, "--global"])
            self.assertEqual(code, 1, "bare hook basename must refuse: %r" % text)
            self.assertEqual(self._count(), before, "no write for: %r" % text)

    def test_unregistered_project_scope_warns_but_captures(self):
        # AUDIT #4 (low): an explicit --project with no matching projects/<slug>/
        # dir would be captured yet NEVER injected — warn (non-silent), still capture.
        code, _out, err = self._cli(
            ["prefer", "capture", "always use feature flags here",
             "--project", "noexist"])
        self.assertEqual(code, 0, err)
        self.assertIn("no KB project", err)
        self.assertIn("noexist", err)
        self.assertIsNotNone(self._entry("learn-pref-noexist-always-use-feature-flags-here"))
        # A registered project scope emits NO warning.
        code, _out, err2 = self._cli(
            ["prefer", "capture", "always use pnpm in tokto",
             "--project", "tokto-io"])
        self.assertEqual(code, 0, err2)
        self.assertNotIn("no KB project", err2)

    def test_cmd_prefer_excluded_from_autoapply_but_version_guarded(self):
        # AUDIT #1 (low): cmd_prefer is DUAL-MODE (digest/list/scan-queue are
        # read-only and run at session-start/Stop time) so it must be EXCLUDED from
        # the migration auto-apply set (no read-triggers-write), yet REMAIN in the
        # version-refusal superset (an older CLI still refuses a newer-schema KB).
        mod = load_cli()
        writers = {f.__name__ for f in mod._kb_writer_funcs()}
        autoapply = {f.__name__ for f in mod._kb_autoapply_funcs()}
        self.assertIn("cmd_prefer", writers)
        self.assertNotIn("cmd_prefer", autoapply,
                         "read-only prefer subcommands must not auto-apply a migration")
        self.assertIn("cmd_prefer_approve", writers)
        self.assertIn("cmd_prefer_approve", autoapply,
                      "cmd_prefer_approve is a pure writer — stays in autoapply")
