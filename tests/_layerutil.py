"""tests/_layerutil.py — AST layer/dependency-direction analysis (stdlib only).

Shared by the granular INTEGRATION tests (tests/integration/test_<layer>.py) and
available to the task-17 automated modularity ratchet. It answers, WITHOUT
importing anything, "which agentware_lib layers does this module import?" —
handling absolute (`import agentware_lib.knowledge...`, `from agentware_lib.core
import x`) AND relative (`from ..knowledge import y`, `from . import z`) forms.

Underscore-prefixed so ``unittest discover`` never collects it as a test.
"""
import ast
import importlib.util
import os

# Layer subpackages (in dependency order). Top-level modules (cli.py,
# _registration.py) are treated as the `cli` leaf layer.
LAYERS = ("core", "knowledge", "retrieval", "ops", "features")
CLI_LAYER = "cli"

# What each layer is ALLOWED to import (itself is always allowed). The direction
# is: core -> nothing above; knowledge -> core; retrieval -> core(+knowledge);
# ops -> core+knowledge+retrieval; features -> any of the above; cli -> all.
ALLOWED_BELOW = {
    "core": set(),
    "knowledge": {"core"},
    "retrieval": {"core", "knowledge"},
    "ops": {"core", "knowledge", "retrieval"},
    "features": {"core", "knowledge", "retrieval", "ops"},
    "cli": {"core", "knowledge", "retrieval", "ops", "features"},
}


def lib_dir(repo_root):
    return os.path.join(repo_root, "scripts", "agentware_lib")


def scripts_dir(repo_root):
    return os.path.join(repo_root, "scripts")


def module_dotted(mod_path, scripts_root):
    """Fully-qualified dotted name, e.g. scripts/agentware_lib/core/paths.py ->
    'agentware_lib.core.paths'."""
    rel = os.path.relpath(mod_path, scripts_root)
    noext = os.path.splitext(rel)[0]
    return noext.replace(os.sep, ".")


def layer_of_dotted(dotted):
    """The layer a fully-qualified agentware_lib module name belongs to, or None
    if it is not an agentware_lib module / is the package itself."""
    parts = dotted.split(".")
    if not parts or parts[0] != "agentware_lib" or len(parts) < 2:
        return None
    second = parts[1]
    return second if second in LAYERS else CLI_LAYER


def module_layer(mod_path, lib):
    """The layer of a module identified by filesystem path."""
    rel = os.path.relpath(mod_path, lib)
    parts = rel.split(os.sep)
    return parts[0] if len(parts) > 1 else CLI_LAYER


def _resolved_targets(node, pkg):
    """Fully-qualified module names an ImportFrom references (best-effort)."""
    out = []
    prefix = "." * node.level
    try:
        if node.module:
            out.append(importlib.util.resolve_name(prefix + node.module, pkg))
        else:
            # `from . import X, Y` / `from .. import X` — each name is a submodule
            for alias in node.names:
                out.append(importlib.util.resolve_name(prefix + alias.name, pkg))
    except (ImportError, ValueError):
        # A relative import that cannot be resolved against pkg is not an
        # upper-layer edge we can attribute; skip it rather than mis-flag.
        pass
    return out


def imported_lib_layers(mod_path, scripts_root):
    """Set of agentware_lib layers this module imports (excluding its OWN layer).

    Absolute and relative imports both resolve to fully-qualified names first,
    then map to a layer. The module's own layer is removed so intra-layer imports
    are never counted as a direction violation.
    """
    with open(mod_path, encoding="utf-8") as f:
        tree = ast.parse(f.read())
    dotted = module_dotted(mod_path, scripts_root)
    pkg = dotted.rsplit(".", 1)[0] if "." in dotted else dotted
    own = layer_of_dotted(dotted)

    layers = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                lyr = layer_of_dotted(alias.name)
                if lyr:
                    layers.add(lyr)
        elif isinstance(node, ast.ImportFrom):
            for tgt in _resolved_targets(node, pkg):
                lyr = layer_of_dotted(tgt)
                if lyr:
                    layers.add(lyr)
    layers.discard(own)
    return layers


def direction_violations(mod_path, scripts_root):
    """Layers this module imports that its layer is NOT allowed to import."""
    own = module_layer(mod_path, lib_dir(os.path.dirname(scripts_root)))
    allowed = ALLOWED_BELOW.get(own, set())
    imported = imported_lib_layers(mod_path, scripts_root)
    return sorted(imported - allowed)
