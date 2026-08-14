"""agentware_lib — the modular home of the agentware CLI engine.

Owns: the package namespace + the layered layout; it re-exports NOTHING and has no import side effects.
Does NOT own: any single domain — every domain lives in a submodule; consult catalog/modules.json (the generated discovery manifest) or scripts/agentware_lib/README.md to find the owning module.
Layer deps: the package root imports nothing; import the specific submodule you need (e.g. `from agentware_lib.core import paths`).

This package holds the agentware toolkit split into small, single-domain
modules organized by LAYER, so the dependency direction is visible in the
filesystem itself:

    core/       stable, feature-independent primitives (imports nothing above)
    knowledge/  the KB system of record (imports core)
    retrieval/  ranking + evaluation (imports core, +knowledge lazily)
    ops/        operational surfaces (imports core, knowledge, retrieval)
    features/   optional/pluggable capabilities (may import any of the above)
    cli.py      the parser ASSEMBLER leaf (may import everything; nothing imports it)

Import discipline is enforced by tests/test_module_boundaries.py: an UPWARD
import is a FAILURE, not a style note.

__all__ discipline: submodules export their public names explicitly. This
top-level package intentionally re-exports NOTHING and has no import side
effects, so callers must import the specific module they need
(`from agentware_lib.core import paths`), never rely on `agentware_lib.X`
being pulled in transitively.
"""

__all__ = []
