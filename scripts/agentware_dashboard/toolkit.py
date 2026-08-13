"""agentware_dashboard.toolkit — the ONE seam between the sidecar and the CLI.

Owns: loading the agentware entrypoint (`scripts/agentware`) as a module, the
`scripts/` import bootstrap that load performs, and the knowledge-dir the
sidecar serves when the launcher did not name one.
Does NOT own: any toolkit BEHAVIOUR. Every derivation the JSON API returns is
imported from the module in `agentware_lib` that defines it, by name, from the
`read*` module that needs it — never resolved through this seam at runtime.

WHY THIS IS SMALL NOW: until this package existed the sidecar read 35 toolkit
functions off the CLI module through a dynamic lookup adapter (`_LibResolving` /
`_lib_lookup`, task 56), which walked `agentware_lib` name by name because the
monolith it used to `exec` had been deleted. That adapter was declared
transitional and it retires HERE: a read model that imports
`agentware_lib.ops.alerts` says where its behaviour comes from at the grep
surface, and cannot silently resolve a name from whichever module happened to be
imported first.

Stdlib only; no network; read-only.
"""

import importlib.util
import os
import sys
from importlib.machinery import SourceFileLoader

HERE = os.path.dirname(os.path.abspath(__file__))
SCRIPTS_DIR = os.path.dirname(HERE)
REPO_ROOT = os.path.dirname(SCRIPTS_DIR)

#: The CLI entrypoint — the facade that owns the `scripts/` sys.path bootstrap
#: for every load mode, this sidecar included (see its module docstring).
CLI_PATH = os.path.join(SCRIPTS_DIR, "agentware")

#: The loaded entrypoint module, cached for the life of the process.
_CLI_MODULE = None

#: The module name the entrypoint is loaded under. Deliberately NOT `agentware`
#: (no such importable package exists) and NOT `agentware_lib.*`, so nothing
#: mistakes the loaded facade for a lib module.
_CLI_MODULE_NAME = "agentware_cli"


def get_cli():
    """Load `scripts/agentware` as a module (cached) and return it.

    Loaded through `spec_from_loader` + `module_from_spec` + `exec_module` with
    `scripts/` placed on `sys.path` first. That import path matters twice over.
    It is the NON-DEPRECATED loader — `SourceFileLoader.load_module()`, which
    this sidecar used before, is deprecated and mutates `sys.modules` behind the
    caller's back — and executing the facade is what runs the toolkit's OWN
    bootstrap, so `import agentware_lib...` resolves in the sidecar process
    exactly the way it resolves for the CLI, from one definition rather than two
    that can drift.

    The sys.path insert here is idempotent and deliberately kept even though a
    launcher normally supplies it: this function is also the entry any test or
    embedder takes when it loads the sidecar's seam directly, and a bootstrap
    that only works under one launch mode is a bootstrap that fails on the other.

    Returns the module object. Raises whatever the entrypoint raises if the
    checkout is broken — the caller decides whether that is fatal, and the
    static layer never calls this at all.
    """
    global _CLI_MODULE
    if _CLI_MODULE is not None:
        return _CLI_MODULE
    if SCRIPTS_DIR not in sys.path:
        sys.path.insert(0, SCRIPTS_DIR)
    loader = SourceFileLoader(_CLI_MODULE_NAME, CLI_PATH)
    spec = importlib.util.spec_from_loader(_CLI_MODULE_NAME, loader)
    module = importlib.util.module_from_spec(spec)
    loader.exec_module(module)
    _CLI_MODULE = module
    return _CLI_MODULE


def resolve_knowledge_dir():
    """The knowledge dir to serve when `--kdir` was not passed, or None.

    Asks the TOOLKIT rather than re-deriving it: resolution honours
    `AGENTWARE_KNOWLEDGE_DIR`, the org keys and the config file, and a second
    implementation here would be a second answer to "where is the knowledge
    base". Loads the entrypoint first so the resolver is reachable at all.

    Returns None when nothing is configured (every endpoint then reports
    `available: false`) and on any resolution failure — an unconfigured
    workspace must degrade to an empty dashboard, never to a stack trace.
    """
    try:
        get_cli()
        from agentware_lib.core import config
        return config.resolve_knowledge_dir()
    except Exception:
        return None
