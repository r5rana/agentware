"""agentware_dashboard.readruns — the per-RUN drill-down read model.

Owns: the payloads that answer "what happened in this run" — terminal outcomes
across every feature (`/api/outcomes`), the task-marker transitions and plan
burndown for one feature (`/api/tasks/<feature>`), the step-level tool-call
timeline (`/api/trace/<target>`), and the failure-ladder / error-recovery tally
(`/api/failures/<feature>`).
Does NOT own: fleet-level loop state or the health badge (`readloops`), the
feature listing (`workfeatures`), or any derivation — trace, failures and
outcome all belong to `agentware_lib.ops.*`.

READ-ONLY. Each endpoint degrades to a zeroed/empty payload rather than an
error when its sources are absent, because a feature with no sessions yet is a
normal state, not a fault.
"""

import os
import urllib.parse
from http import HTTPStatus

from agentware_lib.ops import derive, failureladder, looptrace
from agentware_lib.ops.metrics import metricsemit
from agentware_lib.ops.sessionparse import sessionread

from agentware_dashboard.reqparams import NO_KDIR, kdir_of
from agentware_dashboard.workfeatures import list_work_features


def api_outcomes(request):
    """`/api/outcomes` — the terminal outcome of every feature, plus a tally."""
    kdir = kdir_of(request)
    if not kdir:
        return HTTPStatus.OK, dict(NO_KDIR, features=[], summary={})
    out = []
    summary = {}
    for feature in list_work_features(kdir):
        oc = derive.derive_outcome(kdir, feature)
        rec = dict(oc or {"outcome": "unknown"})
        rec["feature"] = feature
        out.append(rec)
        key = rec.get("outcome", "unknown")
        summary[key] = summary.get(key, 0) + 1
    return HTTPStatus.OK, {"features": out, "summary": summary}


def api_tasks(request, feature):
    """`/api/tasks/<feature>` — task-marker transitions + the plan burndown."""
    kdir = kdir_of(request)
    feature = urllib.parse.unquote(feature)
    if not kdir:
        return HTTPStatus.OK, dict(NO_KDIR, feature=feature, transitions=[])
    events = metricsemit._read_metrics_jsonl(kdir, feature=feature)
    transitions = [e for e in events if e.get("event") == "task_transition"]
    open_n, done_n = derive._plan_marker_status_counts(
        os.path.join(kdir, "work", feature, "plan.md"))
    return HTTPStatus.OK, {
        "feature": feature,
        "transitions": transitions,
        "transition_count": len(transitions),
        "plan": {"open": open_n, "done": done_n, "total": open_n + done_n},
    }


def api_trace(request, target):
    """Step-level run trace: an ordered tool-call timeline grouped by loop
    iteration, built from logs/sessions/<sid>/{live.jsonl,main.jsonl} + the
    metrics.jsonl emission. `target` is matched as a SESSION id first (a session
    dir exists), else treated as a FEATURE. Delegates to the read-only toolkit."""
    kdir = kdir_of(request)
    target = urllib.parse.unquote(target)
    if not kdir:
        return HTTPStatus.OK, dict(NO_KDIR, target=target, iterations=[],
                                   step_count=0)
    root = sessionread.logs_sessions_dir(kdir)
    if os.path.isdir(os.path.join(root, target)):
        return HTTPStatus.OK, looptrace.derive_trace(kdir, session=target)
    return HTTPStatus.OK, looptrace.derive_trace(kdir, feature=target)


def api_failures(request, feature):
    """Failure-ladder & error-recovery: tool ERR rate (live.jsonl) + per-tool
    tallies, R-FAIL ladder tier usage (kb->reasoning->inputs->switch->web),
    web-search escalations, KB-lookup count, self-heal re-engagements, and the
    DECISION/LEARNED marker tallies for ONE feature. Delegates to the read-only
    toolkit derivation; degrades to a zeroed tally when sources are absent."""
    kdir = kdir_of(request)
    feature = urllib.parse.unquote(feature)
    if not kdir:
        tiers = failureladder._FAIL_LADDER_TIERS
        return HTTPStatus.OK, dict(
            NO_KDIR, feature=feature, scope="feature", step_count=0,
            err_count=0, err_rate=0.0, err_by_tool={},
            ladder={t: 0 for t in tiers},
            ladder_order=list(tiers), unrecovered=0,
            web_search_count=0, kb_lookup_count=0, self_heal_count=0,
            markers={"learned": {"total": 0, "unpromoted": 0},
                     "decision": {"total": 0, "unpromoted": 0}},
            tool_summary={}, sessions=[])
    return HTTPStatus.OK, failureladder.derive_failures(kdir, feature)
