"""Tests for the canonical project-key normalizer (Task 1, 260712-steering-capture).

Runner:  python3 -m unittest tests.unit.ops.test_prefer_scope -v

The normalizer `_canonical_project_key(kdir, project_name)` maps a checkout signal
(the `whereami` project_name / directory basename) to the canonical KB project
slug — the `projects/<slug>/` directory whose entry id is `project-<slug>` — using
EXACT equality of `_slugify(project_name)` against an existing project dir. No
prefix / substring / fuzzy match. When there is no project signal or no exact
match it returns None, meaning GLOBAL / unscoped, so a capture is never silently
mis-scoped. It is a pure read-only lookup: no writes, no schema-guard impact.

This is the single canonical key shared by the capture verb (Task 2) and the
session-start filter (Task 3), closing the slug-mismatch gap
(basename `tokto.io` -> KB slug `tokto-io`).
"""

import os
import shutil
import tempfile
import unittest

try:
    from tests._fixtures import load_cli, build_synthetic_kb
except ImportError:
    from _fixtures import load_cli, build_synthetic_kb


def _mk_project(kdir, slug):
    """Create a projects/<slug>/index.md so it looks like a real KB project."""
    d = os.path.join(kdir, "projects", slug)
    os.makedirs(d, exist_ok=True)
    with open(os.path.join(d, "index.md"), "w", encoding="utf-8") as f:
        f.write("# %s\n\n_project._\n" % slug)


class TestCanonicalProjectKey(unittest.TestCase):
    def setUp(self):
        self.kdir = tempfile.mkdtemp(prefix="aw_prefer_scope_")
        self.addCleanup(shutil.rmtree, self.kdir, True)
        build_synthetic_kb(self.kdir)
        # Two real KB projects: an exact target and a longer-prefix sibling.
        _mk_project(self.kdir, "tokto-io")
        _mk_project(self.kdir, "foo-bar")
        self.mod = load_cli()

    def _key(self, project_name):
        return self.mod._canonical_project_key(self.kdir, project_name)

    def test_basename_slugifies_to_exact_kb_slug(self):
        """The worked example: basename `tokto.io` -> slug `tokto-io` (exists)."""
        self.assertEqual(self._key("tokto.io"), "tokto-io")

    def test_already_slug_form_matches(self):
        """A project_name that is already the slug matches its own dir."""
        self.assertEqual(self._key("tokto-io"), "tokto-io")

    def test_uppercase_basename_normalizes(self):
        """slugify lowercases, so `Tokto.IO` still resolves to `tokto-io`."""
        self.assertEqual(self._key("Tokto.IO"), "tokto-io")

    def test_sibling_prefix_does_not_match(self):
        """LOCK OUT the fuzzy leak: basename `foo` must NOT match slug `foo-bar`."""
        self.assertIsNone(self._key("foo"))

    def test_no_matching_project_returns_none(self):
        """An unknown checkout returns None (GLOBAL/unscoped), never mis-scoped."""
        self.assertIsNone(self._key("some-unrelated-repo"))

    def test_empty_signal_returns_none(self):
        """No project signal (empty / whitespace / None) -> None (GLOBAL)."""
        self.assertIsNone(self._key(""))
        self.assertIsNone(self._key("   "))
        self.assertIsNone(self._key(None))

    def test_never_misscopes_to_agentware(self):
        """With no `agentware` KB project, an agentware checkout stays unscoped."""
        self.assertIsNone(self._key("agentware"))

    def test_missing_projects_dir_returns_none(self):
        """A KB with no projects/ dir at all -> None, no crash."""
        empty = tempfile.mkdtemp(prefix="aw_prefer_scope_empty_")
        self.addCleanup(shutil.rmtree, empty, True)
        self.assertIsNone(self.mod._canonical_project_key(empty, "tokto.io"))

    def test_no_kdir_returns_none(self):
        """A None knowledge dir -> None, no crash."""
        self.assertIsNone(self.mod._canonical_project_key(None, "tokto.io"))

    def test_deterministic(self):
        """Same inputs -> same output across calls."""
        self.assertEqual(self._key("tokto.io"), self._key("tokto.io"))


class TestCurrentProjectSignal(unittest.TestCase):
    """Task 5 (audit LOW / Q4): capture/inject scoping symmetry.

    `_current_project_signal` must resolve ONLY from AGENTWARE_INVOKED_FROM — unset
    => None (GLOBAL), the SAME as the session-start injection resolver — with NO
    `os.getcwd()` fallback that would project-scope a plain-session capture the
    injection side treats as global (silent mis-scope)."""

    def setUp(self):
        self.mod = load_cli()
        self.tmp = tempfile.mkdtemp(prefix="aw_prefer_signal_")
        self.addCleanup(shutil.rmtree, self.tmp, True)
        # A real checkout whose basename resolves to a project_name.
        self.checkout = os.path.join(self.tmp, "tokto-io")
        os.makedirs(os.path.join(self.checkout, ".git"))
        self.addCleanup(os.chdir, os.getcwd())
        self._prev_env = os.environ.get("AGENTWARE_INVOKED_FROM")
        self.addCleanup(self._restore_env)

    def _restore_env(self):
        if self._prev_env is None:
            os.environ.pop("AGENTWARE_INVOKED_FROM", None)
        else:
            os.environ["AGENTWARE_INVOKED_FROM"] = self._prev_env

    def test_unset_invoked_from_resolves_global_even_inside_a_checkout(self):
        # The Q4 guarantee: chdir INTO a registered checkout, but with
        # AGENTWARE_INVOKED_FROM UNSET -> None (global) — symmetric with injection.
        os.environ.pop("AGENTWARE_INVOKED_FROM", None)
        os.chdir(self.checkout)
        self.assertIsNone(
            self.mod._current_project_signal(),
            "unset INVOKED_FROM must resolve GLOBAL (no os.getcwd fallback)")

    def test_set_invoked_from_resolves_the_checkout(self):
        # When the var IS set (loop invocation), it still resolves the checkout.
        os.environ["AGENTWARE_INVOKED_FROM"] = self.checkout
        self.assertEqual(self.mod._current_project_signal(), "tokto-io")


if __name__ == "__main__":
    unittest.main()
