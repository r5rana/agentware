"""agentware_lib.ops.failureladder -- failure-ladder / error-recovery CLEAN classification primitives.

Owns: the pure, read-only (INV-2) failure-ladder classification primitives -- the
R-FAIL escalation-ladder tier order (_FAIL_LADDER_TIERS), the web-escalation tool set
(_FAIL_WEB_TOOLS) and the KB-lookup signature set (_FAIL_KB_SIGNATURES), plus the three
argument-only step classifiers that read only the bounded per-step dict the trace already
carries -- _fail_step_is_web (tool is a web search/fetch), _fail_step_is_kb_lookup (tool
invokes an agentware recall/query/audit), and _classify_recovery (map an ERR step + the
action that followed onto a ladder tier: kb/reasoning/inputs/switch/web, or None when the
run ended on the error). ALSO owns (wave-6 seam-11) the per-feature marker tally
_failure_marker_tally -- per-kind LEARNED/DECISION totals + unpromoted counts from a
feature's worklog, reusing the completion-gate regexes/stems (knowledge.worklog) so the
tally agrees with the worklog-scan gate. ALSO owns (wave-6 seam-14) the aggregator
derive_failures -- the per-feature failure-ladder / error-recovery fold (tool ERR rate,
R-FAIL tier usage, web/KB counts, self-heal re-engagements, marker tallies) -- with the
two tally helpers it folds through: _err_tallies (the three tallies that are a function
of ONE step: per-tool ERR counts, the web-escalation count, the KB-lookup count) and
_ladder_tally (the one tally that reads a step's NEIGHBOUR, which is why the parent
re-sorts into the trace's global order first), read-only
(INV-2) over the trace (ops.looptrace.derive_trace) + the outcome (ops.derive.derive_outcome)
+ the classification primitives + _failure_marker_tally here.

Does NOT own: the alerts primitives (ops.alerts), loop
feature discovery/throughput (ops.loopanalytics), the trace family (ops.looptrace),
loop-health (ops.loophealth), the metrics COMPUTE layer (ops.metrics), or the derive_*
reporting family (ops.derive).

Layer deps: ops.failureladder imports os (stdlib) + DOWNWARD knowledge.index (module alias
index, for load_index) + knowledge.worklog (_worklog, for the completion-gate regexes/stems)
-- both ops->knowledge, downward -- plus two SAME-LAYER ops->ops sibling MODULE ALIASES for
derive_failures (wave-6 seam-14): _derive (ops.derive, for derive_outcome) + _looptrace
(ops.looptrace, for derive_trace), gate-safe (a module object has no __code__) and acyclic
(neither imports ops.failureladder). No upward edge.

Determinism: read-only (INV-2), pure functions of their argument step dict(s).

_EXPORTS is imported BY NAME onto the scripts/agentware facade so the sidecar reaches
cli._FAIL_LADDER_TIERS / cli.derive_failures and load_cli tests reach them as CLI.<name>.
_failure_marker_tally + derive_failures reference cross-module FUNCTIONS via module ALIASES
(index/_worklog + _derive/_looptrace), which are gate-safe. None of the 8 is monkeypatched
by any test (grep-verified).
Fan-in: 0 (modules that import this; see catalog/modules.json depended_on_by; regen: python3 tests/_manifest_gen.py --write).
"""

import os

import agentware_lib.knowledge.index as index
import agentware_lib.knowledge.worklog as _worklog
import agentware_lib.ops.derive as _derive
import agentware_lib.ops.looptrace as _looptrace


# --- Failure-ladder & error-recovery (Task 32) -------------------------------
# agentware-specific, high value: WHERE runs get stuck (tool ERR rate from the
# live.jsonl per-action stream) and HOW they recovered along the R-FAIL
# escalation ladder — (1) knowledge base -> (2) own reasoning -> (3) change
# inputs -> (4) switch approach -> (5) web search (R-FAIL-01). ALL derivations
# are PURE + READ-ONLY (INV-2): they fold the existing trace (live.jsonl) + the
# per-feature worklog markers + the loop self-heal count, never writing. Each
# signal degrades to a zeroed tally when its source channel is absent.

# The R-FAIL escalation-ladder tiers, in order (R-FAIL-01). The dashboard plots
# WHERE a run climbed the ladder to recover from a tool failure.
_FAIL_LADDER_TIERS = ["kb", "reasoning", "inputs", "switch", "web"]

# Tools that count as a web escalation (ladder tier 5, R-FAIL-06/R-WEB-01).
_FAIL_WEB_TOOLS = {"WebSearch", "WebFetch"}

# Substrings in a tool's input that mark a knowledge-base lookup (ladder tier 1,
# R-FAIL-02): the agent re-queries the KB by the error signature before retrying.
_FAIL_KB_SIGNATURES = ("agentware recall", "agentware query", "agentware audit")


def _fail_step_is_web(step):
    """A step is a web escalation iff its tool is a web-search/fetch tool."""
    return (step.get("tool") or "") in _FAIL_WEB_TOOLS


def _fail_step_is_kb_lookup(step):
    """A step is a KB lookup iff it invokes an agentware recall/query/audit (read
    from the bounded tool-input string the trace already carries)."""
    if (step.get("tool") or "") in _FAIL_WEB_TOOLS:
        return False
    args = step.get("args") or ""
    return any(sig in args for sig in _FAIL_KB_SIGNATURES)


def _classify_recovery(err_step, next_step):
    """Map an ERR step + the action that FOLLOWED it onto an R-FAIL ladder tier.

    The recovery is an OBSERVABLE proxy read from the NEXT tool call (the run's
    actual next action after the failure):
      - a web search/fetch         -> "web"     (tier 5)
      - a KB recall/query/audit    -> "kb"      (tier 1)
      - the SAME tool, CHANGED args -> "inputs" (tier 3, R-FAIL-04)
      - the SAME tool, SAME args    -> "reasoning" (tier 2 — a reasoned re-attempt)
      - a DIFFERENT tool           -> "switch"  (tier 4, R-FAIL-05)
    No next step -> the run ended ON the error (unrecovered) -> None.
    """
    if next_step is None:
        return None
    if _fail_step_is_web(next_step):
        return "web"
    if _fail_step_is_kb_lookup(next_step):
        return "kb"
    if next_step.get("tool") == err_step.get("tool"):
        if (next_step.get("args") or "") != (err_step.get("args") or ""):
            return "inputs"
        return "reasoning"
    return "switch"


def _failure_marker_tally(kdir, feature):
    """Per-kind LEARNED/DECISION tallies (total + unpromoted) from the feature's
    worklog (read-only). Delegates to `scan_worklog_markers` so the tally uses
    the exact same regexes, cross-category resolution, and WAIVED handling as the
    completion gate — the two can never drift.

    It previously re-implemented the scan loop here instead, and had already
    drifted: it recognized neither `> WAIVED:` nor a cross-category promotion
    reference, so a waived marker and a `> LEARNED:` promoted as a skill both
    counted as unpromoted in the tally while the gate itself counted them closed.
    Two copies of one rule is the defect; delegation is the fix."""
    out = {"learned": {"total": 0, "unpromoted": 0},
           "decision": {"total": 0, "unpromoted": 0}}
    wl = os.path.join(kdir, "work", feature, "worklog.md")
    if not os.path.isfile(wl):
        return out
    data, _err = index.load_index(kdir)
    return _worklog.scan_worklog_markers(wl, kdir, data)["per_kind"]


def _err_tallies(steps):
    """The three ARGUMENT-ONLY tallies over one ordered step list, in one pass:
    `(err_by_tool, web_search_count, kb_lookup_count)`.

    Each reads a classifier that already exists (`_fail_step_is_web`,
    `_fail_step_is_kb_lookup`) or the step's own status; none of them looks at a
    step's NEIGHBOURS, which is exactly what separates them from the ladder
    tally below. An ERR step with no tool at all is counted under `<unknown>`
    rather than dropped — a failure whose tool the trace could not record is
    still a failure, and dropping it would make the rate disagree with
    `err_count`.
    """
    err_by_tool = {}
    web_search_count = 0
    kb_lookup_count = 0
    for s in steps:
        if s.get("status") == "ERR":
            t = s.get("tool") or "<unknown>"
            err_by_tool[t] = err_by_tool.get(t, 0) + 1
        if _fail_step_is_web(s):
            web_search_count += 1
        if _fail_step_is_kb_lookup(s):
            kb_lookup_count += 1
    return err_by_tool, web_search_count, kb_lookup_count


def _ladder_tally(steps):
    """The R-FAIL tier each ERR was recovered by: `(ladder, unrecovered)`.

    The one tally that is NOT a function of a step alone — it reads the step
    that FOLLOWED each ERR, so it depends on `steps` being in the trace's
    deterministic GLOBAL order (the caller re-sorts by `index` before calling).
    An ERR with no following step means the run ended ON the error, which is
    `unrecovered` rather than a tier.
    """
    ladder = {t: 0 for t in _FAIL_LADDER_TIERS}
    unrecovered = 0
    for i, s in enumerate(steps):
        if s.get("status") != "ERR":
            continue
        nxt = steps[i + 1] if i + 1 < len(steps) else None
        tier = _classify_recovery(s, nxt)
        if tier is None:
            unrecovered += 1
        else:
            ladder[tier] += 1
    return ladder, unrecovered


def derive_failures(kdir, feature):
    """Failure-ladder & error-recovery for one feature (pure, read-only, INV-2).

    Folds the read-only trace (live.jsonl) + the per-feature worklog markers +
    the loop terminal self-heal count into: tool ERR rate (+ per-tool tallies),
    R-FAIL ladder tier usage (kb/reasoning/inputs/switch/web — classified from
    the action that followed each ERR), the web-search escalation count, KB-lookup
    count, self-heal re-engagements, and the DECISION/LEARNED marker tallies.
    Empty/missing logs yield zeroed tallies (forward-compatible, never crashes)."""
    trace = _looptrace.derive_trace(kdir, feature=feature)
    steps = []
    for grp in trace.get("iterations", []):
        steps.extend(grp.get("steps", []))
    # Restore the deterministic GLOBAL order (the trace assigns each step a
    # monotonic `index`) so recovery classification reads the true next action.
    steps.sort(key=lambda s: s.get("index", 0))

    step_count = trace.get("step_count", len(steps))
    err_count = trace.get("err_count",
                          sum(1 for s in steps if s.get("status") == "ERR"))
    err_rate = round(err_count / step_count, 4) if step_count else 0.0

    err_by_tool, web_search_count, kb_lookup_count = _err_tallies(steps)
    ladder, unrecovered = _ladder_tally(steps)

    outcome = _derive.derive_outcome(kdir, feature) or {}
    self_heal = outcome.get("self_heal_count")
    if not isinstance(self_heal, int) or isinstance(self_heal, bool):
        self_heal = 0

    markers = _failure_marker_tally(kdir, feature)

    return {
        "feature": feature,
        "scope": "feature",
        "step_count": step_count,
        "err_count": err_count,
        "err_rate": err_rate,
        "err_by_tool": err_by_tool,
        "ladder": ladder,
        "ladder_order": list(_FAIL_LADDER_TIERS),
        "unrecovered": unrecovered,
        "web_search_count": web_search_count,
        "kb_lookup_count": kb_lookup_count,
        "self_heal_count": self_heal,
        "markers": markers,
        "tool_summary": trace.get("tool_summary", {}),
        "sessions": trace.get("sessions", []),
    }



_EXPORTS = [
    '_FAIL_LADDER_TIERS',
    '_FAIL_WEB_TOOLS',
    '_FAIL_KB_SIGNATURES',
    '_fail_step_is_web',
    '_fail_step_is_kb_lookup',
    '_classify_recovery',
    '_failure_marker_tally',
    'derive_failures',
]
