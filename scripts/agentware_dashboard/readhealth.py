"""agentware_dashboard.readhealth — the /api/health read model.

Owns: the knowledge-base health payload — the seven index/steering/personal-data
checks plus the dream heartbeat — as the dashboard's Health pillar reads them.
Does NOT own: any check itself. Every one is the toolkit's own read-only
function (`agentware_lib.knowledge.index.*`, `agentware_lib.ops.audit`,
`agentware_lib.features.dream`), called here and never re-implemented, so the
dashboard and `agentware audit` can never disagree about what "healthy" means.

READ-ONLY: no writer, no collection; the payload is a projection of files the
toolkit already reads.
"""

from http import HTTPStatus

from agentware_lib.features import dream
from agentware_lib.knowledge.index import indexread, indexrebuild, indexvalidate
from agentware_lib.ops import audit

from agentware_dashboard.reqparams import NO_KDIR, kdir_of


def api_health(request):
    """`/api/health` — every KB health check, plus an overall ok flag.

    The dream heartbeat is INERT when dream mode is off, so it never
    false-alarms the default; when on, its check dict carries last_run /
    age_hours / outcome (ok|fail|partial), which flow through this JSON verbatim
    so the dream_health panel can surface last-run age and warn on
    stale-or-failed.
    """
    kdir = kdir_of(request)
    if not kdir:
        return HTTPStatus.OK, NO_KDIR
    data, err = indexread.load_index(kdir)
    if err is not None:
        checks = [{"name": "index_validate", "ok": False, "details": [err]}]
    else:
        ve = indexvalidate.validate_index(kdir, data)
        un = indexrebuild.scan_unindexed(kdir, data)
        te = indexvalidate.tag_consistency_errors(data)
        tce = indexvalidate.template_conformance_errors(kdir, data)
        oe = indexvalidate.orphaned_learning_errors(kdir, data)
        checks = [
            {"name": "index_validate", "ok": not ve, "details": ve},
            {"name": "filesystem_sync", "ok": not un, "details": un},
            {"name": "tag_consistency", "ok": not te, "details": te},
            {"name": "template_conformance", "ok": not tce, "details": tce},
            {"name": "orphaned_learnings", "ok": not oe, "details": oe},
            audit._audit_steering_lint_check(),
            audit._audit_personal_data_check(kdir),
            dream._audit_dream_health_check(kdir),
        ]
    ok = all(c["ok"] for c in checks)
    return HTTPStatus.OK, {"ok": ok, "checks": checks}
