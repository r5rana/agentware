"""agentware_lib.ops.checkupcmd -- the checkup REPORT and the `checkup` verb (layer: ops).

Owns: the presentation half plus the entry points -- the aggregator
(`_checkup_aggregate`), the report renderer (`_checkup_render_report`), the
SessionStart banner (`_checkup_status_line`), the `cmd_checkup` handler, the tick
job body (`_checkup_run`) and the `checkup` registrar.

WHY THE STATUS LINE IS SO CONSERVATIVE
`--status-line` prints ONLY when a fresh report exists, and is a byte-identical
no-op when checkup is OFF or no report has been written. It runs at SessionStart on
every single session, so anything it prints is a permanent context cost and
anything it prints too often gets tuned out. A banner that speaks when it has
nothing to say is worse than no banner: it trains the reader to skip the one time
it matters.

The exam is OFF by default and opt-in. It writes a report and, at most, SUGGESTED
workorders — it never applies anything itself. That is the whole safety posture: a
periodic self-exam that could act on its own findings would be a scheduled
autonomous change to the operator's knowledge base.

Does NOT own: any probe (ops.checkupprobes, ops.checkuphealth); thresholds and
cadence (ops.checkupconfig); emission and the LLM narrative (ops.checkupemit).
Layer deps: ops -> core + same-layer ops (the four sibling checkup modules).
Fan-in: 0 (regen: python3 tests/_manifest_gen.py --write).

The bodies are a PURE MOVE from the monolith -- byte-identical, comments and all.
"""

import argparse
import json
import os
import sys
import time

from agentware_lib.core.config import require_knowledge_dir, resolve_knowledge_dir
from agentware_lib.core.dates import _iso_to_epoch, _now_iso
from agentware_lib.core.capture import _dream_capture
from agentware_lib.ops.planauthor.planedit import _atomic_write
from agentware_lib.ops.checkupconfig import (
    CHECKUP_ACK_REL,
    CHECKUP_DIR_REL,
    CHECKUP_LEARNING_FRESH_DAYS,
    CHECKUP_REPORT_REL,
    CHECKUP_WINDOW_DAYS,
    MIN_FROZEN_CYCLES,
    _checkup_anchor_epoch,
    _checkup_content_hash,
    _checkup_due,
    _checkup_last_run_ts,
    _checkup_read_report,
    _checkup_report_path,
    _checkup_write_last_run,
    resolve_checkup,
    resolve_checkup_llm,
)
from agentware_lib.ops.checkupemit import (
    _checkup_candidates,
    _checkup_emit_workorder,
    _checkup_llm_narrative,
    _checkup_reject_reason,
    _checkup_sig,
)
from agentware_lib.ops.checkuphealth import (
    _audit_pkg_version_check,
    _checkup_drift,
    _checkup_frozen_metrics,
    _checkup_heartbeat_stale,
    _checkup_parse_journal,
    _checkup_recall_p50_ms,
    _checkup_structure_triggers,
    _checkup_watch_dream,
    _checkup_worktree_count,
)
from agentware_lib.ops.checkupprobes import (
    _checkup_flywheel,
    _checkup_learning_freshness,
    _checkup_plan_health,
    _checkup_walk_plans,
)


def register_checkup(sub, fmt_parent):
    """Declare the `checkup` verb. Lifted verbatim from the monolith's build_parser."""
    p_checkup = sub.add_parser(
        "checkup", parents=[fmt_parent],
        help="run ONE periodic self-exam: deterministic aggregation over P1 "
             "instrumentation + one bounded REPORT-ONLY LLM narrative; writes a "
             "session-top report + tier-2 suggested workorders (opt-in, default OFF)")
    p_checkup.add_argument(
        "--status-line", action="store_true", dest="status_line",
        help="print the one-line SessionStart banner ONLY when a fresh report "
             "exists (read-only; byte-identical no-op when OFF/no-report)")
    p_checkup.set_defaults(func=cmd_checkup)


def _checkup_render_report(agg):
    """Deterministic, diff-friendly report body (fixed section order). Renders
    WINDOW AGGREGATES ONLY — NO per-run delta line, NO clock/run-id/window-boundary
    timestamp (INV-1, audit L6-2) — so an idempotent re-run within a cadence period
    is byte-identical. A pure renderer that RETURNS a string (the caller writes)."""
    fw = agg.get("flywheel", {})
    ph = agg.get("plan_health", {})
    wa = agg.get("watcher", {})
    lines = [
        "# checkup — periodic self-examination (latest)",
        "",
        "> Deterministic OFFLINE meta-audit over P1 instrumentation. Report-only",
        "> surface: window aggregates only (no clock/run-id in the body). Suggested",
        "> workorders are tier-2 (operator-approved) and built ONLY from the",
        "> DETERMINISTIC candidate set; the LLM narrative NEVER emits a workorder.",
        "",
        "## Flywheel signals",
        "- read-through: %s" % _checkup_sig(fw.get("read_through", {})),
        "- corpus-coverage: %s" % _checkup_sig(fw.get("corpus_coverage", {})),
        "- error-recovery yield: %s" % _checkup_sig(fw.get("error_recovery", {})),
        "- recurrence (retired, never a workorder driver): %s"
        % _checkup_sig(fw.get("recurrence", {})),
        "- learning-freshness (<=%dd, surfaced-by-recall lower bound): %s"
        % (CHECKUP_LEARNING_FRESH_DAYS, _checkup_sig(fw.get("learning_freshness", {}))),
        "",
        "## Plan health",
    ]
    if ph.get("insufficient_data"):
        lines.append("- insufficient data (no plans)")
    else:
        lines.append("- done: %d · rotting: %d · running: %d · ready: %d · draft: %d"
                     % (ph.get("done", 0), ph.get("rotting", 0),
                        ph.get("running", 0), ph.get("ready", 0), ph.get("draft", 0)))
        if ph.get("stalled"):
            for s in ph["stalled"]:
                lines.append("- stalled: %s" % s["rel"])
        else:
            lines.append("- stalled: none")
    lines += _render_drift_section(agg)
    lines += ["", "## Watcher-watching (dream itself)"]
    lines.append("- dream fired on cadence: %s" % wa.get("verdict", "unknown"))
    lines.append("- missed cycles in window: %d" % wa.get("missed_cycles", 0))
    if wa.get("frozen_metrics"):
        for fm in wa["frozen_metrics"]:
            lines.append("- FROZEN: %s held at %s across %d present cycles"
                         % (fm["metric"], fm["value"], fm["present_cycles"]))
    elif wa.get("insufficient_data"):
        lines.append("- frozen-metric check: insufficient data (< %d present cycles)"
                     % MIN_FROZEN_CYCLES)
    else:
        lines.append("- frozen metrics: none")
    lines += ["", "## Structure triggers"]
    for t in agg.get("triggers", []):
        tag = "FIRED" if t.get("fired") else "under threshold"
        ro = " [report-only]" if not t.get("fires_workorder") else ""
        lines.append("- %s: %s / %s — %s%s"
                     % (t["signal"], t.get("value"), t.get("threshold"), tag, ro))
    narrative = agg.get("narrative")
    if narrative:
        lines += ["", "## LLM narrative (report-only — never emits a workorder)"]
        if narrative.get("narrative"):
            lines.append(narrative["narrative"])
        for o in narrative.get("observations", []):
            lines.append("- observation: %s" % o)
    lines += _render_workorder_sections(agg)
    return "\n".join(lines).rstrip("\n") + "\n"


def _render_drift_section(agg):
    """The DRIFT section: declared state vs what the tree actually shows.

    Split from the renderer at task 44 to hold the per-function branch cap. Drift
    is the exam's sharpest signal — every row is a place the RECORD and the
    REALITY disagree (a plan declaring `running` whose claim aged out, say) — so
    each row carries declared, derived and severity rather than a verdict alone.
    """
    lines = ["", "## Drift (declared vs derived)"]
    drift = agg.get("drift", [])
    if not drift:
        lines.append("- none")
        return lines
    for d in drift:
        feat = (" [%s]" % d["feature"]) if d.get("feature") else ""
        lines.append("- %s%s: declared=%s derived=%s [%s]"
                     % (d["kind"], feat, d.get("declared"), d.get("derived"),
                        d.get("severity")))
    return lines


def _render_workorder_sections(agg):
    """The PROPOSED / DEFERRED / REJECTED tail of the report.

    Split from the renderer at task 44 to hold the per-function branch cap. All
    three sections are sorted for determinism — the report must be byte-identical
    on an idempotent re-run within a cadence period, so nothing here may depend on
    dict ordering.

    Deferred and rejected are reported WITH their reason rather than omitted: a
    candidate the emitter refused, or the governance escape-valve rejected, is
    exactly the thing an operator needs to see. A suggestion that silently
    vanishes is indistinguishable from a bug in the selector.
    """
    lines = ["", "## Proposed workorders (tier-2, operator-approved)"]
    proposed = agg.get("proposed", [])
    if not proposed:
        lines.append("- none")
    else:
        for p in sorted(proposed, key=lambda x: x.get("feature", "")):
            lines.append("- %s: %s" % (p["feature"], p["text"]))
    deferred = agg.get("deferred", [])
    if deferred:
        lines += ["", "## Deferred (emitter refused — recurs next cycle)"]
        for d in sorted(deferred, key=lambda x: x.get("text", "")):
            lines.append("- deferred (%s): %s" % (d.get("reason"), d["text"]))
    lines += ["", "## Rejected (governance escape-valve — NOT emitted)"]
    rejected = agg.get("rejected", [])
    if not rejected:
        lines.append("- none")
    else:
        for r in sorted(rejected, key=lambda x: x.get("text", "")):
            lines.append("- rejected: %s — %s" % (r.get("reason"), r["text"]))
    return lines


def _checkup_aggregate(kdir, now):
    """Compose Tasks 2-5 into ONE read-only deterministic aggregate. Every channel
    degrades to insufficient data on an empty KB (Q10)."""
    anchor = _checkup_anchor_epoch(now)
    window_lo = anchor - CHECKUP_WINDOW_DAYS * 86400
    cycles = [c for c in _checkup_parse_journal(kdir)
              if (_iso_to_epoch(c.get("ts")) or 0) >= window_lo]
    plans = _checkup_walk_plans(kdir, now)
    flywheel = _checkup_flywheel(kdir, window_lo, anchor)
    plan_health = _checkup_plan_health(kdir, now, plans=plans)
    watcher = _checkup_watch_dream(kdir, cycles, now)
    drift = _checkup_drift(kdir, now, watcher, plan_health)
    triggers = _checkup_structure_triggers(kdir)
    return {"flywheel": flywheel, "plan_health": plan_health, "drift": drift,
            "watcher": watcher, "triggers": triggers, "narrative": None,
            "proposed": [], "rejected": [], "deferred": []}


def _checkup_status_line(kdir):
    """One-line SessionStart banner — prints ONLY when checkup is ON AND a FRESH
    (content-hash unacked) report exists. Short-circuits to a byte-identical NO-OP
    (no output, NO write) when OFF / no kdir / no report / already-acked (audit
    L6-1). Writes logs/checkup/.ack AFTER printing. Never blocks; mirrors
    `update --status-line`."""
    if resolve_checkup() != "1" or not kdir:
        return 0
    body = _checkup_read_report(kdir)
    if not body:
        return 0
    cur = _checkup_content_hash(body)
    ackp = os.path.join(kdir, CHECKUP_ACK_REL)
    try:
        with open(ackp, "r", encoding="utf-8") as f:
            prev = f.read().strip()
    except OSError:
        prev = ""
    if cur == prev:
        return 0                     # already acknowledged — byte-identical no-op
    print("agentware checkup: a fresh self-exam report is ready — see "
          "logs/checkup/checkup-latest.md (run `scripts/agentware checkup`).")
    try:
        os.makedirs(os.path.dirname(ackp), exist_ok=True)
        with open(ackp, "w", encoding="utf-8") as f:
            f.write(cur + "\n")
    except OSError:
        pass
    return 0


def cmd_checkup(args):
    """The periodic self-exam: aggregate (2-5) -> report-only LLM narrative (7a) ->
    render report (6) -> deterministic-only emit (7b) -> summary. Read-only w.r.t.
    the index + all governance state; writes ONLY files under logs/checkup/ and
    sanitized work/auto/ workorders."""
    as_json = getattr(args, "format", "text") == "json"
    if getattr(args, "status_line", False):
        return _checkup_status_line(resolve_knowledge_dir())
    kdir = require_knowledge_dir()
    if not kdir:
        return 1
    now = time.time()
    agg = _checkup_aggregate(kdir, now)
    # (7a) ONE bounded REPORT-ONLY LLM narrative pass (delegates to the CLI-aware
    # seam). Disabled -> deterministic-only. NEVER emits a workorder.
    if resolve_checkup_llm() == "1":
        cfg = {"model": os.environ.get("AGENTWARE_CHECKUP_MODEL") or None,
               "effort": os.environ.get("AGENTWARE_CHECKUP_EFFORT") or None}
        agg["narrative"] = _checkup_llm_narrative(agg, cfg)
    # (7b) DETERMINISTIC-only emit: reject-scan each candidate, emit survivors via
    # the EXISTING sanitized emitter, surface drops in the Rejected section.
    for cand in _checkup_candidates(agg):
        reason = _checkup_reject_reason(cand["text"])
        if reason:
            agg["rejected"].append({"reason": reason, "text": cand["text"]})
            continue
        feat, err = _checkup_emit_workorder(kdir, cand)
        if feat:
            agg["proposed"].append({"feature": feat, "text": cand["text"]})
        else:
            agg["deferred"].append({"reason": err, "text": cand["text"]})
    # render + atomically write the byte-stable report, then stamp last-run
    body = _checkup_render_report(agg)
    _atomic_write(_checkup_report_path(kdir), body)
    _checkup_write_last_run(kdir)
    summary = {"ok": True, "report": CHECKUP_REPORT_REL,
               "proposed": [p["feature"] for p in agg["proposed"]],
               "rejected": len(agg["rejected"]),
               "deferred": len(agg["deferred"]),
               "stalled": len(agg["plan_health"].get("stalled", [])),
               "frozen_metrics": [f["metric"]
                                  for f in agg["watcher"].get("frozen_metrics", [])],
               "triggers_fired": [t["signal"] for t in agg["triggers"]
                                  if t.get("fired") and t.get("fires_workorder")],
               "narrative": bool(agg.get("narrative"))}
    if as_json:
        json.dump(summary, sys.stdout, ensure_ascii=False, indent=2)
        sys.stdout.write("\n")
    else:
        print("checkup: wrote %s (proposed %d, rejected %d, deferred %d)"
              % (CHECKUP_REPORT_REL, len(agg["proposed"]), len(agg["rejected"]),
                 len(agg["deferred"])))
        for f in summary["frozen_metrics"]:
            print("checkup: FROZEN metric %s (130-for-13-nights)" % f)
    return 0


def _checkup_run(kdir, now):
    """Dispatch ONE checkup via _dream_capture (mirrors _dream_cadence_run).
    Returns a compact status dict for the tick result list."""
    ns = argparse.Namespace(format="json", status_line=False)
    rc, _out = _dream_capture(cmd_checkup, ns)
    return {"status": "ok" if rc == 0 else "error", "rc": rc,
            "detail": "checkup dispatched cmd_checkup rc=%d" % rc}


_EXPORTS = [
    "_checkup_render_report",
    "_checkup_aggregate",
    "_checkup_status_line",
    "cmd_checkup",
    "_checkup_run",
    "register_checkup",
]

__all__ = list(_EXPORTS)
