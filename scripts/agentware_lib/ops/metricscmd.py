"""agentware_lib.ops.metricscmd -- the `metrics` + `dashboard` CLI verbs (layer: ops).

Owns: the two CLI surfaces over the metrics COMPUTE layer -- cmd_metrics (the
read-only `metrics` handler: resolve kdir, load the price table, collect + aggregate
+ price sessions, attach the read-only derive_* rollups -- authoring / context-tax /
plan-size / phase-costs / iterations / outcome -- then emit JSON or the text report),
its text renderer (_print_metrics_text) + the wall-clock formatter (_fmt_duration),
and cmd_dashboard (the thin localhost-only launcher that subprocess-execs the
DASHBOARD_SERVER sidecar) + the DASHBOARD_SERVER path constant, plus the two parser
registrars (register_metrics / register_dashboard).

Does NOT own: the metrics COMPUTE itself (agentware_lib.ops.metrics --
aggregate_metrics / apply_pricing / load_price_table / _DEFAULT_PRICES /
_read_metrics_jsonl, imported DOWN as _metrics); the derive_* reporting family
(agentware_lib.ops.derive, imported DOWN as _derive); session collection
(agentware_lib.ops.sessionparse, as _sessionparse); the plan-path helper
(agentware_lib.ops.planauthor._plan_path, as _planauthor); and the dashboard SERVER
implementation itself (scripts/agentware_dashboard.py -- a guard-exempt sidecar
launched by subprocess, NEVER imported here).

Layer deps: ops.metricscmd -> ops.metrics + ops.derive + ops.sessionparse +
ops.planauthor (same layer, all DOWNWARD -- none of them imports metricscmd, so no
cycle) + core.config / core.dates / core.paths (REPO_ROOT). This CLI surface is a
SEPARATE leaf module (mirroring retrieval.recallcmd / retrieval.evalcmd) rather than
living inside the pure-compute ops.metrics, precisely so the ops.metrics <->
ops.derive import cycle that co-location would create (ops.derive imports ops.metrics
since seam 9) is avoided. A LEAF: nothing imports it (fan-in 0); the scripts/agentware
entrypoint re-exports its surface BY NAME.

Determinism: read-only (INV-2). cmd_metrics never writes the KB / index; cmd_dashboard
only execs the read-only sidecar bound to 127.0.0.1.

_EXPORTS is imported BY NAME onto the scripts/agentware facade so the parser-golden
dispatch contract (metrics -> cmd_metrics, dashboard -> cmd_dashboard) + the dashboard
+ load_cli tests reach them as bare names / CLI.<name>. The two registrars are NOT in
_EXPORTS -- the assembler resolves them via importlib from _PARSER_REGISTRATION.
Fan-in: 0 (modules that import this; see catalog/modules.json depended_on_by; regen: python3 tests/_manifest_gen.py --write).
"""
import json
import os
import subprocess
import sys

import agentware_lib.core.config as config
import agentware_lib.ops.deriveretrieval as _deriveret
import agentware_lib.core.dates as dates
from agentware_lib.core.paths import REPO_ROOT
import agentware_lib.ops.derive as _derive
import agentware_lib.ops.metrics as _metrics
import agentware_lib.ops.planauthor as _planauthor
import agentware_lib.ops.sessionparse as _sessionparse


def _fmt_duration(seconds):
    seconds = int(round(seconds))
    h, rem = divmod(seconds, 3600)
    m, s = divmod(rem, 60)
    if h:
        return "%dh%02dm%02ds" % (h, m, s)
    if m:
        return "%dm%02ds" % (m, s)
    return "%ds" % s


def _print_metrics_text(rows, agg, flt):
    desc = []
    if flt.get("session"):
        desc.append("session=%s" % flt["session"])
    if flt.get("feature"):
        desc.append("feature=%s" % flt["feature"])
    if flt.get("since"):
        desc.append("since=%s" % flt["since"])
    print("metrics: %d session%s%s" % (
        len(rows), "" if len(rows) == 1 else "s",
        ("  (%s)" % ", ".join(desc)) if desc else ""))
    for r in rows:
        print("  %s  turns=%d  tools=%d  subagents=%d  tokens=%d  wall=%s"
              % (r["session_id"], r["turns"], r["tool_calls"],
                 r["subagent_count"], r["total_tokens"],
                 _fmt_duration(r["duration_seconds"])))
        if r["tools"]:
            parts = ", ".join("%s=%d" % (k, r["tools"][k])
                              for k in sorted(r["tools"]))
            print("    tool calls: %s" % parts)
    print("  ----")
    print("  TOTAL  sessions=%d  turns=%d  tools=%d  subagents=%d  tokens=%d  wall=%s"
          % (agg["session_count"], agg["turns"], agg["tool_calls"],
             agg["subagent_count"], agg["total_tokens"],
             _fmt_duration(agg["duration_seconds"])))
    u = agg["tokens"]
    print("    tokens: in=%d out=%d cache_creation=%d cache_read=%d"
          % (u["input_tokens"], u["output_tokens"],
             u["cache_creation_input_tokens"], u["cache_read_input_tokens"]))
    if "cost_usd" in agg:
        print("    cost: $%.4f  cache_read_ratio=%.3f%s"
              % (agg["cost_usd"], agg.get("cache_read_ratio", 0.0),
                 ("  cost_anomalies=%d" % len(agg["cost_anomaly_dates"]))
                 if agg.get("cost_anomaly_dates") else ""))
    if "context_tax" in agg:
        ct = agg["context_tax"]
        print("    context_tax: cache_read/turn=%.1f  injected_tokens=%d  "
              "window_util=%.1f%%%s"
              % (ct["cache_read_per_turn"], ct["injected_tokens"],
                 ct["context_window_pct"] * 100.0,
                 "  TRUNCATION-RISK" if ct["truncation_risk"] else ""))
    if agg.get("outcome"):
        print("    outcome: %s (%s)" % (agg["outcome"]["outcome"],
                                        agg["outcome"]["source"]))
    if "phase_costs" in agg:
        pc = agg["phase_costs"]["by_phase"]
        parts = ", ".join("%s=$%.4f" % (ph, pc[ph]["cost_usd"])
                          for ph in sorted(pc))
        if parts:
            print("    phase_costs: %s" % parts)


DASHBOARD_SERVER = os.path.join(REPO_ROOT, "scripts", "agentware_dashboard.py")


def cmd_dashboard(args):
    """Serve the read-only observability dashboard (localhost-only).

    The HTTP server needs `http.server`, which the toolkit's import guards
    (INV-1/INV-6, tests/test_invariants.py) forbid in this CLI source. So the
    server lives in a SEPARATE sidecar file (`scripts/agentware_dashboard.py`)
    that the guards never scan, and we launch it via subprocess — the same
    pattern as the optional embedder backend. This keeps the toolkit's
    static-import surface stdlib-only while the dashboard still runs.

    The server binds 127.0.0.1 ONLY, serves the built static bundle
    (webui/dist/) through a hardened static handler, and exposes the read-only
    JSON API. It adds NO writer.
    """
    if not os.path.isfile(DASHBOARD_SERVER):
        print("error: dashboard server missing (%s)" % DASHBOARD_SERVER,
              file=sys.stderr)
        return 1
    host = getattr(args, "host", None) or "127.0.0.1"
    dist_dir = os.path.join(REPO_ROOT, "webui", "dist")
    kdir = config.resolve_knowledge_dir()
    cmd = [sys.executable, DASHBOARD_SERVER,
           "--host", host, "--port", str(args.port), "--dist", dist_dir]
    if kdir:
        cmd += ["--kdir", kdir]
    if getattr(args, "no_open", False):
        cmd.append("--no-open")
    try:
        return subprocess.call(cmd, cwd=REPO_ROOT)
    except KeyboardInterrupt:
        return 0


def _report_retrieval(kdir, args):
    """`metrics --retrieval`: recall invocation, returned ids, read-through and
    corpus coverage, per session.

    Split from `cmd_metrics` rather than inlined — that function carries a
    declared ceiling row, and this branch is a whole second report.
    """
    report = _deriveret.derive_retrieval(kdir, session=getattr(args, "session", None))
    if getattr(args, "format", "text") == "json":
        json.dump(report, sys.stdout, ensure_ascii=False, indent=2)
        sys.stdout.write("\n")
        return 0
    print("retrieval utilization: %d session(s) sampled"
          % report["sessions_sampled"])
    cc = report["corpus_coverage"]
    print("  corpus coverage: %s/%s learning ids surfaced (ratio %s)"
          % (cc["learning_ids_surfaced"], cc["corpus_size"], cc["ratio"]))
    for s in report["sessions"]:
        rt = s["read_through"]
        print("  %s: recall=%s calls=%d returned_ids=%d read-through=%s/%s"
              % (s["session"], s["recall_invoked"], s["recall_calls"],
                 len(s["returned_ids"]), rt["read"], rt["returned_paths"]))
    return 0


def cmd_metrics(args):
    """Execution observability over session transcripts (READ-ONLY, INV-2).

    Parses logs/sessions/<sid>/main.jsonl (+ subagents/*.jsonl) for turn counts,
    wall time, token usage, tool-call counts and subagent counts. NEVER touches
    index.json or any KB file."""
    kdir = config.require_knowledge_dir()
    if not kdir:
        return 1

    # Retrieval-utilization mode: a distinct, self-contained derivation over
    # live.jsonl that SHORT-CIRCUITS the execution-cost path below. The two
    # answer different questions — "what did this cost" versus "was the knowledge
    # base actually used" — and only the second can tell you a corpus is being
    # maintained and never read.
    if getattr(args, "retrieval", False):
        return _report_retrieval(kdir, args)

    since = getattr(args, "since", None)
    if since is not None and not dates._is_valid_date(since):
        sys.stderr.write("agentware: --since must be YYYY-MM-DD\n")
        return 1
    prices = _metrics._DEFAULT_PRICES
    price_source = "default"
    prices_path = getattr(args, "prices", None)
    if prices_path:
        prices, perr = _metrics.load_price_table(prices_path)
        if perr:
            sys.stderr.write("agentware: %s\n" % perr)
            return 1
        price_source = prices_path
    rows = _sessionparse.collect_sessions(
        kdir, session=getattr(args, "session", None),
        feature=getattr(args, "feature", None), since=since)
    agg = _metrics.aggregate_metrics(rows)
    _metrics.apply_pricing(rows, agg, prices)
    flt = {"session": getattr(args, "session", None),
           "feature": getattr(args, "feature", None), "since": since}

    # Plan-authoring derivations (read-only): attributed authoring time/tokens
    # always; plan-size only when a single feature is in scope (it has a plan.md).
    agg["authoring"] = _derive.derive_authoring(rows)
    # Context-tax derivations (read-only): cache-read-per-turn series, the
    # injected MAIN.md footprint, and context-window utilization + truncation risk.
    agg["context_tax"] = _derive.derive_context_tax(
        rows, kdir, window=getattr(args, "context_window", None))
    feature = getattr(args, "feature", None)
    plan_meta = _derive.derive_plan_size(_planauthor._plan_path(kdir, feature)) if feature else None

    # Per-phase / per-iteration cost attribution + terminal run outcome (Task 5,
    # read-only). phase_costs partition the session tokens by loop phase (sums ==
    # session totals); iterations + outcome are per-feature: outcome reads the
    # Task 6/7 metrics.jsonl terminal event when present, else falls back to the
    # persisted .loop/plan/assessment artifacts.
    agg["phase_costs"] = _derive.derive_phase_costs(rows)
    if feature:
        loop_events = _metrics._read_metrics_jsonl(kdir, feature=feature)
        agg["iterations"] = _derive.derive_iteration_costs(loop_events)
        agg["outcome"] = _derive.derive_outcome(kdir, feature, events=loop_events)

    if args.format == "json":
        payload = {"filter": flt, "session_count": len(rows),
                   "price_source": price_source,
                   "sessions": rows, "aggregate": agg}
        if feature:
            payload["plan"] = plan_meta
        json.dump(payload, sys.stdout, ensure_ascii=False, indent=2)
        sys.stdout.write("\n")
        return 0 if rows else 1

    if not rows:
        print("metrics: no matching sessions under %s"
              % _sessionparse.logs_sessions_dir(kdir))
        return 1
    _print_metrics_text(rows, agg, flt)
    return 0


def register_metrics(sub, fmt_parent):
    # metrics ----------------------------------------------------------------
    p_metrics = sub.add_parser(
        "metrics", parents=[fmt_parent],
        help="execution observability over session transcripts (read-only)")
    p_metrics.add_argument("--session", default=None,
                           help="restrict to a single session id")
    p_metrics.add_argument("--feature", default=None,
                           help="sessions whose transcript references the feature")
    p_metrics.add_argument("--since", default=None,
                           help="only sessions starting on/after YYYY-MM-DD")
    p_metrics.add_argument("--prices", default=None,
                           help="path to a JSON price table ($ per 1M tokens, "
                                "keyed by model-id substring) overriding the "
                                "built-in default cost rates")
    p_metrics.add_argument("--context-window", dest="context_window",
                           type=int, default=None,
                           help="model context-window size (tokens) the "
                                "context-tax truncation-risk is measured "
                                "against (default 200000)")
    p_metrics.add_argument("--retrieval", action="store_true",
                           help="report per-session RETRIEVAL utilization over "
                                "live.jsonl (recall invoked, returned ids, "
                                "read-through, corpus coverage) instead of "
                                "execution-cost metrics")
    p_metrics.set_defaults(func=cmd_metrics)


def register_dashboard(sub, fmt_parent):
    # dashboard --------------------------------------------------------------
    p_dash = sub.add_parser(
        "dashboard",
        help="serve the read-only observability dashboard (localhost-only)")
    p_dash.add_argument("--port", type=int, default=8765,
                        help="port to bind (default: 8765)")
    p_dash.add_argument("--host", default="127.0.0.1",
                        help="loopback host to bind (default: 127.0.0.1)")
    p_dash.add_argument("--no-open", dest="no_open", action="store_true",
                        help="do not open a browser window")
    p_dash.set_defaults(func=cmd_dashboard)


_EXPORTS = [
    "_fmt_duration", "_print_metrics_text", "DASHBOARD_SERVER",
    "cmd_dashboard", "cmd_metrics",
]
