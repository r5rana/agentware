"""agentware_lib.ops.fleet -- org/fleet observability + org/template lint (layer: ops).

AGGREGATOR ONLY. This package holds NO logic: it re-imports the public names of
its four domain submodules so every pre-split call site -- which reaches the module
under an alias (`import agentware_lib.ops.fleet as _fleet`) and reads
`_fleet.guard_shared_write` / `_fleet.FLEET_TERMINAL_REQUIRED` / `_fleet.lint_templates`
-- keeps resolving unchanged, so `_registration.py`'s three rows (`fleet`,
`fleet-emit`, `templates`) keep resolving their registrars off THIS dotted name,
and so every existing monkeypatch target keeps working. The submodules are:

  * `fleetshard`     -- the committed telemetry SINK: the steering-version join key
                        resolve_steering_sha, the per-USER shard paths, the tolerant
                        fleet-wide reader, the terminal-event schema constants, the
                        org-root resolver, and the emit path cmd_fleet_emit.
  * `fleetguard`     -- the shared-write governance guard (guard_shared_write/
                        _is_curator) plus the org READ guards (INJECTION_MARKERS/
                        ORG_CONFLICT_MARKER_RE/_org_read_denylist).
  * `fleetreport`    -- the `fleet issues|impact` reporter cmd_fleet and its
                        sufficiency floor FLEET_IMPACT_FLOOR_N.
  * `fleettemplates` -- the per-team template contract T1..T5 (_template_kind/
                        lint_templates/cmd_templates_lint + its vocabularies).

The union of the four submodules' `_EXPORTS` is exactly the pre-split module's
`_EXPORTS` -- no name added, none dropped -- and is restated here (in the pre-split
order) so the package namespace behaves exactly like the pre-split single module.
The boundary gates read `_EXPORTS` from DOMAIN modules only (package `__init__.py`
files are excluded), so this restatement is inert for M3/M10; the entrypoint binds
the FOUR SUBMODULES, never this package.

Owns: nothing of its own -- only the re-export surface that keeps the pre-split
import path and every `<consumer>._fleet.<name>` monkeypatch target resolving.
Does NOT own: any behaviour -- see the four submodules above; the org-invariant
lint that consumes these constants (agentware_lib.ops.orglint); the
teams/registry.json family (agentware_lib.ops.team); the metrics COMPUTE layer
(agentware_lib.ops.metrics); the OLS primitive (agentware_lib.ops.alerts).
Layer deps: ops -> its own submodules only (which reach core + knowledge.index +
the same-layer ops.alerts); nothing above ops imports it.
"""

from agentware_lib.ops.fleet.fleetshard import (
    FLEET_TERMINAL_REQUIRED,
    LOOP_OUTCOME_ENUM,
    _fleet_dir_for,
    _fleet_org_root,
    cmd_fleet_emit,
    fleet_read_all,
    fleet_shard_path,
    register_fleet_emit,
    resolve_steering_sha,
)
from agentware_lib.ops.fleet.fleetguard import (
    INJECTION_MARKERS,
    ORG_CONFLICT_MARKER_RE,
    _is_curator,
    _org_read_denylist,
    guard_shared_write,
)
from agentware_lib.ops.fleet.fleetreport import (
    FLEET_IMPACT_FLOOR_N,
    cmd_fleet,
    register_fleet,
)
from agentware_lib.ops.fleet.fleettemplates import (
    SKILL_TEMPLATE_FM,
    TEMPLATE_ORG_FM,
    TEMPLATE_REQUIRED_FM,
    _template_kind,
    cmd_templates_lint,
    lint_templates,
    register_templates,
)

# The UNION of the four submodules' _EXPORTS, in the pre-split order.
_EXPORTS = [
    'resolve_steering_sha',
    '_fleet_dir_for',
    'fleet_shard_path',
    'fleet_read_all',
    'FLEET_TERMINAL_REQUIRED',
    'LOOP_OUTCOME_ENUM',
    '_fleet_org_root',
    '_is_curator',
    'guard_shared_write',
    'FLEET_IMPACT_FLOOR_N',
    'cmd_fleet',
    'cmd_fleet_emit',
    'INJECTION_MARKERS',
    'ORG_CONFLICT_MARKER_RE',
    '_org_read_denylist',
    'TEMPLATE_REQUIRED_FM',
    'TEMPLATE_ORG_FM',
    'SKILL_TEMPLATE_FM',
    '_template_kind',
    'lint_templates',
    'cmd_templates_lint',
]
