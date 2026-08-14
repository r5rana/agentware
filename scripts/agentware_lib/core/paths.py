"""agentware_lib.core.paths — repo-root + CLI-self anchors (layer: core).

Owns: the REPO_ROOT marker-walk and the CLI_SELF entrypoint-path constant.
Does NOT own: config values or git runners — see agentware_lib.core.config and agentware_lib.core.gitio.
Layer deps: core -> stdlib only (imports nothing else from agentware_lib).
Fan-in: 20 (modules that import this; see catalog/modules.json depended_on_by; regen: python3 tests/_manifest_gen.py --write).

REPO_ROOT is resolved by WALKING UP from this file until the AGENTS.md marker
is found — NOT by a fixed number of parent hops. The nested package layout
(core/ under agentware_lib/ under scripts/) makes any hardcoded hop count wrong
and brittle: a 3-hop guess lands on scripts/ (which has no AGENTS.md) and would
silently empty every REPO_ROOT-derived glob (STEERING_GLOB, the gold-fixture
paths, the dashboard server path). The marker walk is depth-independent, so a
later reorganization that changes this module's depth cannot break it, and it
raises loudly instead of silently returning a wrong root.

core is the STABLE, feature-independent layer: this module imports only the
stdlib and nothing else from agentware_lib.
"""

import os


def _find_repo_root(start):
    """Walk parent directories from ``start`` until an AGENTS.md marker is found.

    Returns the absolute directory containing AGENTS.md. Raises a loud
    RuntimeError (never returns a wrong root) if the filesystem root is reached
    without finding the marker.
    """
    directory = os.path.dirname(os.path.abspath(start))
    while True:
        if os.path.isfile(os.path.join(directory, "AGENTS.md")):
            return directory
        parent = os.path.dirname(directory)
        if parent == directory:
            raise RuntimeError(
                "agentware_lib.core.paths: reached the filesystem root while "
                "walking up from %r without finding the AGENTS.md repo-root "
                "marker" % (os.path.abspath(start),))
        directory = parent


# Repo root, resolved by marker walk (see module docstring).
REPO_ROOT = _find_repo_root(__file__)

# The extensionless CLI entrypoint (scripts/agentware).
CLI_SELF = os.path.join(REPO_ROOT, "scripts", "agentware")

__all__ = ["REPO_ROOT", "CLI_SELF"]
