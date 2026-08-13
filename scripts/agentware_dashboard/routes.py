"""agentware_dashboard.routes — the URL table, and nothing else.

Owns: the mapping from a request path to the read-model builder that answers it
— `API_ROUTES` for exact matches and the ORDERED `PARAM_ROUTES` for
parameterized drill-downs.
Does NOT own: dispatch (the handler walks these two tables), any payload
(`read*`), or the liveness probe (`/api`, `/api/ping`, answered by the handler
without reaching a read model).

WHY ITS OWN FILE: this table IS the served surface. Kept alone, "which routes
does the dashboard serve?" is one screen with no handler bodies in the way, and
a route added without a builder — or a builder added without a route — is
visible in a two-line diff. `tests/unit/dashboard/test_routes.py` pins the union
of both merged trees' routes against it.

A handler is a pure builder `(request, **path_params) -> (HTTPStatus, payload)`.
"""

import re

from agentware_dashboard import (readcost, readevals, readgraph, readhealth,
                                 readkb, readloops, readruns)

#: Exact-match data endpoints: "/api/<name>" -> handler(request).
API_ROUTES = {
    "/api/health": readhealth.api_health,
    "/api/quality": readevals.api_quality,
    "/api/loop": readloops.api_loop,
    "/api/agents": readloops.api_agents,
    "/api/loop-analytics": readloops.api_loop_analytics,
    "/api/loop-health": readloops.api_loop_health,
    "/api/cost": readcost.api_cost,
    "/api/authoring": readcost.api_authoring,
    "/api/context-tax": readcost.api_context_tax,
    "/api/scaling": readevals.api_scaling,
    "/api/outcomes": readruns.api_outcomes,
    "/api/evals": readevals.api_evals,
    "/api/alerts": readevals.api_alerts,
    "/api/kb": readkb.api_kb,
    "/api/kb/projects": readkb.api_kb_projects,
    "/api/kb/learnings": readkb.api_kb_learnings,
    "/api/features": readkb.api_features,
}

#: Parameterized drill-down routes: (compiled pattern, handler(request, **groups)).
#: A path segment captures everything-but-slash so ids/tags/features with hyphens
#: resolve; the more specific /api/kb/learnings/<id> is matched before the broader
#: tag route because it lives earlier in this ordered list.
PARAM_ROUTES = [
    (re.compile(r"^/api/kb/learnings/(?P<entry_id>[^/]+)$"),
     readkb.api_kb_learning_detail),
    (re.compile(r"^/api/kb/tags/(?P<tag>[^/]+)$"), readkb.api_kb_tag),
    (re.compile(r"^/api/tasks/(?P<feature>[^/]+)$"), readruns.api_tasks),
    (re.compile(r"^/api/trace/(?P<target>[^/]+)$"), readruns.api_trace),
    (re.compile(r"^/api/loop-health/(?P<feature>[^/]+)$"),
     readloops.api_loop_health_feature),
    (re.compile(r"^/api/failures/(?P<feature>[^/]+)$"), readruns.api_failures),
    (re.compile(r"^/api/assessments/(?P<feature>[^/]+)$"),
     readevals.api_assessments),
    (re.compile(r"^/api/graph/(?P<name>[^/]+)$"), readgraph.api_graph),
]


def resolve(route):
    """The (handler, path_params) serving `route`, or (None, {}).

    Exact matches first, then the ordered parameterized patterns — the same two
    steps the handler used to inline, lifted here so the table and the rule that
    reads it stay in one place.
    """
    handler = API_ROUTES.get(route)
    if handler is not None:
        return handler, {}
    for pattern, param_handler in PARAM_ROUTES:
        match = pattern.match(route)
        if match:
            return param_handler, match.groupdict()
    return None, {}
