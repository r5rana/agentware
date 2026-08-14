"""agentware_lib.ops.planauthor — deterministic plan authoring + work-artifact stamping (layer: ops).

AGGREGATOR ONLY. This package holds NO logic: it re-imports the public names of its
two domain submodules so every pre-split call site — which reaches the module under an
alias (`import agentware_lib.ops.planauthor as _planauthor`) and reads
`_planauthor._atomic_write` / `_planauthor._plan_path` — keeps resolving unchanged, and
so every existing monkeypatch target keeps working. The submodules are:

  * `planedit`  — the plan.md GENERATION half: the deterministic lint-passing writers
                  (_plan_skeleton/_plan_promise/_plan_path/_plan_renumber/
                  _plan_marker_indices/_load_plan_for_edit/_atomic_write/
                  _PLAN_STATUS_GLYPH), the cmd_plan_new/add_task/set_status/target_dirs
                  handlers, and the `plan` registrar.
  * `workstamp` — the work-artifact team/author STAMPING family
                  (_WORK_TEAM_STAMP_RE/_is_derived_roster/_stamp_work_artifact/
                  _ensure_work_frontmatter/cmd_work_stamp) and the `work` registrar.

The two halves share NO symbol and neither imports the other — the seam is a clean cut,
so this package is the only place they meet. The union of the two submodules' `_EXPORTS`
is exactly the pre-split module's `_EXPORTS`, in the pre-split ORDER — no name added,
none dropped.

`register_plan`/`register_work` are re-imported (NOT exported) because
`_registration.py` names this PACKAGE — `("plan", "agentware_lib.ops.planauthor",
"register_plan")` — and the assembler resolves the registrar with importlib + getattr on
the module object.

Owns: nothing of its own — only the re-export surface that keeps the pre-split import
path and every monkeypatch target resolving unchanged.
Does NOT own: any behaviour — see the two submodules above; the ENFORCEMENT half (plan
lint) -> agentware_lib.ops.planlint; the plan-tainted derive_* reporting family ->
agentware_lib.ops.derive.
Layer deps: ops -> core (config/dates/frontmatter/routing) + a same-layer ops edge to
ops.planlint — all DOWNWARD or same-layer; nothing above ops.
Fan-in: 2 (modules that import this; see catalog/modules.json depended_on_by; regen: python3 tests/_manifest_gen.py --write).
"""

# Module aliases bound here for NAMESPACE PARITY with the pre-split flat module: the
# legacy granular suite patches the core resolvers through this package
# (`mock.patch.object(planauthor.config, "require_knowledge_dir")`) and reads the
# planlint constant/handlers as `planauthor._planlint` / `planauthor._PLAN_TASK_LINE_RE`.
# Patching a MODULE object mutates the one shared module, so the submodules — which
# resolve the same names through their own aliases — see the patch. Keeping these bound
# is what makes the split invisible to existing callers and tests.
import agentware_lib.core.config as config              # noqa: F401
import agentware_lib.core.dates as dates                # noqa: F401
import agentware_lib.core.frontmatter as frontmatter    # noqa: F401
import agentware_lib.core.routing as routing            # noqa: F401
import agentware_lib.ops.planlint as _planlint          # noqa: F401
from agentware_lib.ops.planlint import _PLAN_TASK_LINE_RE  # noqa: F401

from agentware_lib.ops.planauthor.planedit import (
    _PLAN_STATUS_GLYPH,
    _atomic_write,
    _load_plan_for_edit,
    _plan_marker_indices,
    _plan_path,
    _plan_promise,
    _plan_renumber,
    _plan_skeleton,
    cmd_plan_add_task,
    cmd_plan_new,
    cmd_plan_set_status,
    cmd_plan_target_dirs,
    register_plan,
)
from agentware_lib.ops.planauthor.workstamp import (
    _WORK_TEAM_STAMP_RE,
    _ensure_work_frontmatter,
    _is_derived_roster,
    _stamp_work_artifact,
    cmd_work_stamp,
    register_work,
)

# The UNION of the two submodules' _EXPORTS, in the pre-split order. The entrypoint
# facade binds each SUBMODULE (not this package) via _FACADE_MODULES, so this list is
# the compatibility declaration of the pre-split surface, not a second facade row.
_EXPORTS = [
    "_PLAN_STATUS_GLYPH",
    "_atomic_write",
    "_plan_promise",
    "_plan_path",
    "_plan_skeleton",
    "_plan_renumber",
    "_plan_marker_indices",
    "_load_plan_for_edit",
    "cmd_plan_new",
    "_WORK_TEAM_STAMP_RE",
    "_is_derived_roster",
    "_stamp_work_artifact",
    "_ensure_work_frontmatter",
    "cmd_work_stamp",
    "cmd_plan_add_task",
    "cmd_plan_set_status",
    "cmd_plan_target_dirs",
]
__all__ = list(_EXPORTS)
