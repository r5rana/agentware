"""The dashboard ENTRYPOINT (`agentware_dashboard.__main__`).

The entrypoint is argv in, a bound server out — so what it owns is testable
without a socket: the flag contract the `dashboard` verb fills in, the
knowledge-dir default, and the two refusal paths (a non-loopback host, an
unavailable port) with their exit codes.

`make_server` is substituted per test rather than bound: the server itself has
its own coverage in `tests/integration/test_dashboard.py`, and what belongs here
is WHICH arguments the entrypoint hands it.
"""

from tests._paths import REPO_ROOT

import contextlib
import io
import os
import sys
import tempfile
import unittest

SCRIPTS_DIR = os.path.join(REPO_ROOT, "scripts")
if SCRIPTS_DIR not in sys.path:
    sys.path.insert(0, SCRIPTS_DIR)

from agentware_dashboard import __main__ as entrypoint  # noqa: E402
from agentware_dashboard import toolkit  # noqa: E402


class _FakeServer(object):
    """Enough of a bound server for the entrypoint's lifecycle, and no socket."""

    def __init__(self, kwargs):
        self.kwargs = kwargs
        self.server_address = ("127.0.0.1", 4321)
        self.dist_root = kwargs.get("dist_dir") or "/nonexistent/dist"
        self.closed = False
        self.served = False

    def serve_forever(self):
        self.served = True
        raise KeyboardInterrupt  # the operator's Ctrl-C, the normal exit path

    def server_close(self):
        self.closed = True


class _EntrypointTestCase(unittest.TestCase):
    """Substitutes the constructor and captures the banner."""

    def setUp(self):
        self._real_make_server = entrypoint.make_server
        self.addCleanup(setattr, entrypoint, "make_server",
                        self._real_make_server)
        self.built = []

    def _install(self, factory=None):
        def _make(**kwargs):
            if factory is not None:
                return factory(kwargs)
            server = _FakeServer(kwargs)
            self.built.append(server)
            return server
        entrypoint.make_server = _make

    def _run(self, argv):
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            rc = entrypoint.main(argv)
        return rc, out.getvalue(), err.getvalue()


class ArgvContractTest(unittest.TestCase):
    """The flags `scripts/agentware dashboard` fills in."""

    def test_the_defaults_are_the_documented_ones(self):
        args = entrypoint.build_parser().parse_args([])
        self.assertEqual(args.host, "127.0.0.1")
        self.assertEqual(args.port, 8765)
        self.assertIsNone(args.dist)
        self.assertIsNone(args.kdir)
        self.assertFalse(args.no_open)

    def test_every_flag_the_cli_passes_parses(self):
        args = entrypoint.build_parser().parse_args(
            ["--host", "localhost", "--port", "0", "--dist", "/d",
             "--kdir", "/k", "--no-open"])
        self.assertEqual((args.host, args.port, args.dist, args.kdir,
                          args.no_open), ("localhost", 0, "/d", "/k", True))


class KnowledgeDirDefaultTest(_EntrypointTestCase):
    """The capability task 69 adds: a directly-launched sidecar finds the KB."""

    def test_an_explicit_kdir_is_passed_through_untouched(self):
        self._install()
        rc, _out, _err = self._run(["--no-open", "--port", "0",
                                    "--kdir", "/explicit/kb"])
        self.assertEqual(rc, 0)
        self.assertEqual(self.built[0].kwargs["kdir"], "/explicit/kb")

    def test_an_omitted_kdir_is_resolved_from_the_workspace(self):
        kdir = tempfile.mkdtemp(prefix="agentware-kdir-")
        self.addCleanup(os.rmdir, kdir)
        real = toolkit.resolve_knowledge_dir
        toolkit.resolve_knowledge_dir = lambda: kdir
        self.addCleanup(setattr, toolkit, "resolve_knowledge_dir", real)
        self._install()
        rc, _out, _err = self._run(["--no-open", "--port", "0"])
        self.assertEqual(rc, 0)
        self.assertEqual(self.built[0].kwargs["kdir"], kdir)

    def test_an_unconfigured_workspace_still_serves_with_no_kdir(self):
        # `available: false` on every endpoint is a legitimate dashboard; a
        # workspace with no knowledge dir must not stop the server from binding.
        real = toolkit.resolve_knowledge_dir
        toolkit.resolve_knowledge_dir = lambda: None
        self.addCleanup(setattr, toolkit, "resolve_knowledge_dir", real)
        self._install()
        rc, _out, _err = self._run(["--no-open", "--port", "0"])
        self.assertEqual(rc, 0)
        self.assertIsNone(self.built[0].kwargs["kdir"])


class LifecycleTest(_EntrypointTestCase):
    """Serving, shutdown, and the two refusals."""

    def test_it_serves_then_closes_the_server(self):
        self._install()
        rc, out, _err = self._run(["--no-open", "--port", "0"])
        self.assertEqual(rc, 0)
        self.assertTrue(self.built[0].served)
        self.assertTrue(self.built[0].closed, "the socket was never closed")
        self.assertIn("agentware dashboard serving", out)

    def test_a_refused_host_exits_2_and_says_why(self):
        def _boom(_kwargs):
            raise ValueError("dashboard binds loopback only; refusing host "
                             "'0.0.0.0'")
        self._install(_boom)
        rc, _out, err = self._run(["--no-open", "--host", "0.0.0.0"])
        self.assertEqual(rc, 2)
        self.assertIn("loopback only", err)

    def test_an_unavailable_port_exits_1_and_names_the_address(self):
        def _boom(_kwargs):
            raise OSError("Address already in use")
        self._install(_boom)
        rc, _out, err = self._run(["--no-open", "--port", "8765"])
        self.assertEqual(rc, 1)
        self.assertIn("cannot bind 127.0.0.1:8765", err)

    def test_a_missing_bundle_is_a_note_not_a_failure(self):
        # A fresh clone that has not run `npm run build` still gets the JSON API.
        self._install()
        rc, _out, err = self._run(["--no-open", "--port", "0",
                                   "--dist", "/nonexistent/dist"])
        self.assertEqual(rc, 0)
        self.assertIn("static bundle not built yet", err)


if __name__ == "__main__":
    unittest.main()
