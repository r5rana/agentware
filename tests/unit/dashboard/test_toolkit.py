"""The dashboard's TOOLKIT SEAM (`agentware_dashboard.toolkit`).

Two claims, and one retirement.

  * `get_cli()` loads the CLI entrypoint through `spec_from_loader` +
    `module_from_spec` + `exec_module` — the donor's loader, adopted at plan
    task 69 (`tests/_body_adjudication.py` floored it as a DEFERRED row naming
    this task) in place of the deprecated `SourceFileLoader.load_module()`.
  * `resolve_knowledge_dir()` is the capability that loader now serves: a
    sidecar launched WITHOUT `--kdir` resolves the knowledge dir from the
    workspace instead of serving an empty dashboard, and it degrades to None
    rather than raising when nothing is configured or the toolkit will not load.

The retirement is the transitional adapter (`_LibResolving` / `_lib_lookup`,
task 56), which resolved 35 toolkit functions by WALKING `agentware_lib` at
runtime. The read model imports the owning module by name now, so a missing
symbol is an ImportError at import time rather than an AttributeError inside one
endpoint's 500. `ReadModelBindsStaticallyTest` is the check that keeps it gone.
"""

from tests._paths import REPO_ROOT

import importlib
import os
import pkgutil
import sys
import tempfile
import unittest

SCRIPTS_DIR = os.path.join(REPO_ROOT, "scripts")
if SCRIPTS_DIR not in sys.path:
    sys.path.insert(0, SCRIPTS_DIR)

import agentware_dashboard  # noqa: E402
from agentware_dashboard import toolkit  # noqa: E402

KDIR_ENV = "AGENTWARE_KNOWLEDGE_DIR"


class _ToolkitCacheTestCase(unittest.TestCase):
    """Restores the module-level cache: a test must not leak a loaded CLI."""

    def setUp(self):
        self._cached = toolkit._CLI_MODULE
        self._cli_path = toolkit.CLI_PATH
        self.addCleanup(self._restore)

    def _restore(self):
        toolkit._CLI_MODULE = self._cached
        toolkit.CLI_PATH = self._cli_path


class GetCliTest(_ToolkitCacheTestCase):
    """The loader."""

    def test_it_loads_the_entrypoint_module(self):
        mod = toolkit.get_cli()
        self.assertTrue(callable(mod.main))
        # The facade keeps the router BY NAME; resolving it proves the file that
        # was executed is the real entrypoint and not an empty namespace.
        self.assertTrue(hasattr(mod, "_strangler"))

    def test_it_caches_the_module_object(self):
        toolkit._CLI_MODULE = None
        first = toolkit.get_cli()
        self.assertIs(first, toolkit.get_cli())

    def test_it_does_not_publish_the_facade_as_an_importable_package(self):
        # Loaded under a private name: nothing may reach the entrypoint by
        # importing `agentware` or by mistaking it for a lib module.
        toolkit.get_cli()
        self.assertNotIn("agentware_lib", toolkit._CLI_MODULE_NAME)

    def test_it_puts_scripts_on_sys_path_so_the_lib_resolves(self):
        toolkit._CLI_MODULE = None
        removed = [p for p in list(sys.path) if p == toolkit.SCRIPTS_DIR]
        for p in removed:
            sys.path.remove(p)
        self.addCleanup(lambda: sys.path.insert(0, toolkit.SCRIPTS_DIR)
                        if toolkit.SCRIPTS_DIR not in sys.path else None)
        toolkit.get_cli()
        self.assertIn(toolkit.SCRIPTS_DIR, sys.path)

    def test_a_broken_entrypoint_raises_rather_than_returning_a_stub(self):
        # A half-loaded toolkit would serve 500s forever; the loader is allowed
        # to fail loudly, and `resolve_knowledge_dir` is where that is caught.
        toolkit._CLI_MODULE = None
        toolkit.CLI_PATH = os.path.join(REPO_ROOT, "scripts", "no-such-entry")
        with self.assertRaises(Exception):
            toolkit.get_cli()


class ResolveKnowledgeDirTest(_ToolkitCacheTestCase):
    """The capability: a directly-launched sidecar finds the knowledge base."""

    def setUp(self):
        super(ResolveKnowledgeDirTest, self).setUp()
        self._env = os.environ.get(KDIR_ENV)
        self.addCleanup(self._restore_env)

    def _restore_env(self):
        if self._env is None:
            os.environ.pop(KDIR_ENV, None)
        else:
            os.environ[KDIR_ENV] = self._env

    def test_it_resolves_the_configured_knowledge_dir(self):
        kdir = tempfile.mkdtemp(prefix="agentware-kdir-")
        self.addCleanup(os.rmdir, kdir)
        os.environ[KDIR_ENV] = kdir
        self.assertEqual(os.path.realpath(toolkit.resolve_knowledge_dir()),
                         os.path.realpath(kdir))

    def test_it_returns_none_instead_of_raising_when_the_toolkit_will_not_load(self):
        toolkit._CLI_MODULE = None
        toolkit.CLI_PATH = os.path.join(REPO_ROOT, "scripts", "no-such-entry")
        self.assertIsNone(toolkit.resolve_knowledge_dir())


class ReadModelBindsStaticallyTest(unittest.TestCase):
    """The retired adapter stays retired."""

    def _package_modules(self):
        for info in pkgutil.iter_modules(agentware_dashboard.__path__):
            if info.name == "__main__":
                continue  # imported by name in its own test; not a library module
            yield importlib.import_module("agentware_dashboard.%s" % info.name)

    def test_every_module_imports_cleanly(self):
        """The static equivalent of the donor's entrypoint symbol contract: with
        the toolkit functions imported by name, importing the package IS the
        proof that every symbol the read model consumes still exists."""
        names = [m.__name__ for m in self._package_modules()]
        self.assertGreaterEqual(len(names), 10, names)

    def test_no_module_resolves_a_toolkit_name_dynamically(self):
        retired = ("_LibResolving", "_lib_lookup")
        for module in self._package_modules():
            for name in retired:
                self.assertFalse(hasattr(module, name),
                                 "%s still carries %s" % (module.__name__, name))

    def test_the_read_model_never_reaches_the_cli_module(self):
        """`get_cli` has ONE caller (`resolve_knowledge_dir`) and no read-model
        module may grow a second: reading a derivation off the entrypoint is the
        coupling task 69 removed."""
        for module in self._package_modules():
            if module.__name__.endswith(".toolkit"):
                continue
            with open(module.__file__, "r", encoding="utf-8") as fh:
                source = fh.read()
            self.assertNotIn("get_cli(", source, module.__name__)


if __name__ == "__main__":
    unittest.main()
