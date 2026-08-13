"""Unit coverage of the dashboard's SECURITY KERNEL (`agentware_dashboard.confine`).

The kernel is the whole disclosure surface of a server that runs on the
operator's machine with the knowledge base in reach: which host may be bound,
which bytes under `webui/dist/` may be served, which extensions are allowed, and
which file under `<kdir>/work/` a drill-down may read. Plan task 69 gave it its
own module, and this is its own unit file — no socket, no server, no request
object, so every rule is testable as the total function it is.

`SafePathUnitTest` MOVED here from `tests/integration/test_dashboard.py` with
its class and method names verbatim: the ids are frozen (task 9), so the move is
recorded in `tests/_test_layout.RELOCATIONS` as a second successor of
`tests.test_dashboard` rather than re-derived. Never rename these methods.
"""

from tests._paths import REPO_ROOT

import os
import shutil
import sys
import tempfile
import unittest

SCRIPTS_DIR = os.path.join(REPO_ROOT, "scripts")
if SCRIPTS_DIR not in sys.path:
    sys.path.insert(0, SCRIPTS_DIR)

from agentware_dashboard import confine  # noqa: E402


class SafePathUnitTest(unittest.TestCase):
    """Direct unit coverage of the path-confinement helper."""

    def setUp(self):
        self.dist = tempfile.mkdtemp(prefix="agentware-dist-")
        self.addCleanup(shutil.rmtree, self.dist, True)
        with open(os.path.join(self.dist, "index.html"), "w") as f:
            f.write("x")

    def test_root_maps_to_index(self):
        p = confine.safe_static_path(self.dist, "/")
        self.assertEqual(os.path.basename(p), "index.html")

    def test_traversal_stays_confined(self):
        # `..` segments are collapsed by normpath at the absolute root, so a
        # traversal can only ever resolve to a path INSIDE dist (or None).
        root = os.path.realpath(self.dist)
        for p in ("/../agentware.js", "/a/../../b.js", "/../../x/y.js"):
            got = confine.safe_static_path(self.dist, p)
            if got is not None:
                self.assertTrue(got == root or got.startswith(root + os.sep),
                                "%s escaped dist -> %s" % (p, got))

    def test_symlink_escape_returns_none(self):
        # A symlink inside dist pointing OUTSIDE must not be served.
        outside_dir = tempfile.mkdtemp(prefix="agentware-outside-")
        self.addCleanup(shutil.rmtree, outside_dir, True)
        with open(os.path.join(outside_dir, "leak.js"), "w") as f:
            f.write("LEAK")
        link = os.path.join(self.dist, "link.js")
        try:
            os.symlink(os.path.join(outside_dir, "leak.js"), link)
        except (OSError, NotImplementedError):
            self.skipTest("symlinks unsupported on this platform")
        self.assertIsNone(confine.safe_static_path(self.dist, "/link.js"))

    def test_disallowed_ext_returns_none(self):
        self.assertIsNone(confine.safe_static_path(self.dist, "/x.pem"))
        self.assertIsNone(confine.safe_static_path(self.dist, "/x.py"))

    def test_allowed_ext_confined(self):
        p = confine.safe_static_path(self.dist, "/assets/app.js")
        self.assertTrue(p.endswith(os.path.join("assets", "app.js")))
        self.assertTrue(p.startswith(os.path.realpath(self.dist) + os.sep))


class BindHostAssertionTest(unittest.TestCase):
    """The loopback-bind assertion, as a function of the requested host.

    The assertion moved OUT of `make_server` into the kernel at task 69, so it is
    now provable without binding a port. `tests/integration/test_dashboard.py`
    still asserts the EFFECTIVE bound address end to end — that is the pair: the
    rule here, the wiring there.
    """

    def test_the_loopback_literal_is_returned_unchanged(self):
        self.assertEqual(confine.bind_host("127.0.0.1"), "127.0.0.1")

    def test_the_localhost_alias_normalises_to_the_literal(self):
        # A NAME could resolve to a routable interface; the literal cannot.
        self.assertEqual(confine.bind_host("localhost"), "127.0.0.1")

    def test_the_ipv6_loopback_is_allowed_and_unchanged(self):
        self.assertEqual(confine.bind_host("::1"), "::1")

    def test_every_non_loopback_host_raises(self):
        for evil in ("0.0.0.0", "::", "192.168.1.10", "example.com", "",
                     "127.0.0.2", "127.0.0.1 "):
            with self.assertRaises(ValueError, msg=evil):
                confine.bind_host(evil)

    def test_the_refusal_names_the_host_and_what_is_allowed(self):
        # The message is the operator's only feedback on a refused bind.
        with self.assertRaises(ValueError) as cm:
            confine.bind_host("0.0.0.0")
        text = str(cm.exception)
        self.assertIn("0.0.0.0", text)
        self.assertIn("127.0.0.1", text)


class MimeAllowlistTest(unittest.TestCase):
    """Only an allowlisted extension has a content type, and nothing sniffs."""

    def test_an_allowlisted_extension_maps_to_its_type(self):
        self.assertEqual(confine.mime_for("/x/app.js"),
                         "text/javascript; charset=utf-8")
        self.assertEqual(confine.mime_for("/x/INDEX.HTML"),
                         "text/html; charset=utf-8")

    def test_an_unlisted_extension_falls_back_to_octet_stream(self):
        # Unreachable through `safe_static_path` (which refuses the path first);
        # pinned so a future caller cannot end up sending a GUESSED type.
        self.assertEqual(confine.mime_for("/x/secret.pem"),
                         confine.DEFAULT_CONTENT_TYPE)

    def test_no_executable_or_secret_extension_is_allowlisted(self):
        for ext in (".py", ".sh", ".pem", ".key", ".env", ".jsonl", ".md",
                    ".yaml", ".yml", ".db"):
            self.assertNotIn(ext, confine.MIME_BY_EXT, ext)


class ConfinedWorkFileTest(unittest.TestCase):
    """The SECOND confinement: one named file under `<kdir>/work/<feature>/`."""

    def setUp(self):
        self.kdir = tempfile.mkdtemp(prefix="agentware-kdir-")
        self.addCleanup(shutil.rmtree, self.kdir, True)
        self.feature_dir = os.path.join(self.kdir, "work", "260101-demo")
        os.makedirs(self.feature_dir)
        self.target = os.path.join(self.feature_dir, "assessment.md")
        with open(self.target, "w") as f:
            f.write("# Post-Execution Assessment\n")

    def test_a_plain_feature_resolves_to_its_file(self):
        got = confine.confined_work_file(self.kdir, "260101-demo",
                                         "assessment.md")
        self.assertEqual(got, os.path.realpath(self.target))

    def test_a_feature_with_no_such_file_still_resolves_the_path(self):
        # Existence is the CALLER's question (missing -> exists:false, no error);
        # this function answers confinement only.
        got = confine.confined_work_file(self.kdir, "260102-absent",
                                         "assessment.md")
        self.assertIsNotNone(got)
        self.assertFalse(os.path.exists(got))

    def test_a_parent_traversal_is_refused(self):
        for evil in ("../../../etc/passwd", "..", "../..",
                     "260101-demo/../../..", "/etc"):
            self.assertIsNone(
                confine.confined_work_file(self.kdir, evil, "assessment.md"),
                evil)

    def test_a_symlinked_feature_pointing_outside_work_is_refused(self):
        outside = tempfile.mkdtemp(prefix="agentware-outside-")
        self.addCleanup(shutil.rmtree, outside, True)
        with open(os.path.join(outside, "assessment.md"), "w") as f:
            f.write("LEAK")
        link = os.path.join(self.kdir, "work", "escape")
        try:
            os.symlink(outside, link)
        except (OSError, NotImplementedError):
            self.skipTest("symlinks unsupported on this platform")
        self.assertIsNone(
            confine.confined_work_file(self.kdir, "escape", "assessment.md"))

    def test_a_symlink_redirecting_to_another_file_inside_work_is_refused(self):
        # The basename re-check, on the one path that reaches it: a symlink at
        # the asked-for name pointing at a DIFFERENT file that is still inside
        # work/. Confinement alone says yes (the target is under the root); the
        # basename says no, because the endpoint promised ONE artifact and this
        # would serve another one the operator never asked to expose.
        other = os.path.join(self.kdir, "work", "260102-other")
        os.makedirs(other)
        with open(os.path.join(other, "worklog.md"), "w") as f:
            f.write("SOMEONE ELSE'S WORKLOG")
        link_dir = os.path.join(self.kdir, "work", "260103-link")
        os.makedirs(link_dir)
        try:
            os.symlink(os.path.join(other, "worklog.md"),
                       os.path.join(link_dir, "assessment.md"))
        except (OSError, NotImplementedError):
            self.skipTest("symlinks unsupported on this platform")
        self.assertIsNone(
            confine.confined_work_file(self.kdir, "260103-link",
                                       "assessment.md"))


if __name__ == "__main__":
    unittest.main()
