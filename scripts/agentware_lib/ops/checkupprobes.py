"""agentware_lib.ops.checkupprobes -- the KNOWLEDGE-BASE probes (layer: ops).

Owns: the probes that grade the knowledge base itself -- the metric trend
(`_checkup_trend`), the flywheel (`_checkup_flywheel`: is the loop still turning
learnings into durable knowledge?), learning freshness
(`_checkup_learning_freshness`), and the plan sweep
(`_checkup_walk_plans` / `_checkup_plan_health` / `_checkup_plan_title`) with the
draft TTLs it ages against.

WHAT THESE ACTUALLY ASK
Not "is the code healthy" -- that is ops.checkuphealth -- but "is the KNOWLEDGE
still alive?". A knowledge base fails quietly: entries stop being added, learnings
go stale without being superseded, plans sit in `draft` forever. None of that
breaks a test, and none of it is visible from a single session, which is exactly
why a periodic exam exists.

The TTL split is deliberate: an AUTO-generated draft is aged out faster than an
OPERATOR-written one, because the machine can always regenerate its own proposal
while a human draft may be deliberate work-in-progress.

`_checkup_trend` grades DIRECTION, not level: a number that is bad but improving
is a different report from one that is bad and worsening, and only the second
deserves the operator's attention now.

Does NOT own: thresholds and cadence (ops.checkupconfig); package/runtime health
(ops.checkuphealth); workorder emission (ops.checkupemit); rendering
(ops.checkupcmd).
Layer deps: ops -> core (dates, frontmatter), knowledge.index, and same-layer ops
(metrics, deriveretrieval, planauthor.planstate for the plan lifecycle vocabulary,
premisecheck for the corpus metric). Reading the plan STATE vocabulary from its
owner rather than re-deriving it is what keeps the exam and the state machine from
disagreeing about what `stalled` means.
Fan-in: 1 at landing (regen: python3 tests/_manifest_gen.py --write).

The bodies are a PURE MOVE from the monolith -- byte-identical, comments and all.
"""

import json
import os
import re
import time

from agentware_lib.core.dates import _iso_to_epoch, _now_iso
from agentware_lib.core.frontmatter import _operator_handle
from agentware_lib.knowledge.index import load_index
from agentware_lib.ops.checkupconfig import (
    CHECKUP_CORPUS_ENTRIES_MAX,
    CHECKUP_LEARNING_FRESH_DAYS,
    CHECKUP_WINDOW_DAYS,
)
from agentware_lib.ops.deriveretrieval import derive_retrieval
from agentware_lib.ops.metrics import _read_metrics_jsonl
from agentware_lib.ops.planauthor.planstate import (
    STALLED_CLAIM_HOURS,
    _PLAN_STATES,
    _parse_claim,
    _read_plan_status,
)
from agentware_lib.ops.premisecheck import _premise_corpus_metric_value


# Plan-state DRIFT thresholds (feature 260710, report-only in dream step e):
#   stalled    = a `running` plan whose `> Claimed-by:` is older than
#                STALLED_CLAIM_HOURS (defined with the plan-state constants).
#   stale-draft = a `draft` plan idle longer than its lane's TTL (auto lane is
#                 tighter than the operator lane). `cancelled` plans are excluded.
AUTO_DRAFT_TTL_DAYS = 7


OPERATOR_DRAFT_TTL_DAYS = 90


def _checkup_trend(values):
    """rising/falling/flat from an ordered numeric list, or None when there are
    fewer than 2 samples (insufficient data — never a fabricated direction)."""
    vals = [v for v in values if isinstance(v, (int, float))]
    if len(vals) < 2:
        return None
    half = len(vals) // 2
    first = sum(vals[:half]) / half if half else 0.0
    second = sum(vals[half:]) / (len(vals) - half)
    d = second - first
    if d > 1e-6:
        return "rising"
    if d < -1e-6:
        return "falling"
    return "flat"


def _checkup_flywheel(kdir, window_lo, anchor):
    """LIVE flywheel signals over the window (Q9). Each is a pure function over a
    read-only snapshot returning {value|..., direction|gauge, sample_n}, each
    forward-compatible with an EMPTY channel -> 'insufficient data' (Q10), never a
    crash and never a fabricated number. Read-only (no writes anywhere)."""
    out = {}
    _empty_ret = {"sessions": [], "corpus_coverage": {"ratio": None,
                                                       "corpus_size": 0}}
    # read-through ratio trend (per-session, None-filtered, audit L3-4). PROBE the
    # index READ-ONLY first: a missing/corrupt index.json is a NORMAL fresh-clone
    # state, and derive_retrieval load_index's it with rebuild ON — which would
    # WRITE index.json + FEATURES.md + rosters + the BM25 cache, breaking the Q7
    # read-only-w.r.t.-index invariant (adversarial-review finding). Only call
    # derive_retrieval when the index loads read-only; else the channel is simply
    # insufficient (never a governance-state write).
    _data, _err = load_index(kdir, _allow_rebuild=False)
    if _err:
        ret = _empty_ret
    else:
        try:
            ret = derive_retrieval(kdir)
        except Exception:
            ret = _empty_ret
    ratios = [s["read_through"]["ratio"]
              for s in ret.get("sessions", [])
              if isinstance(s.get("read_through"), dict)
              and s["read_through"].get("ratio") is not None]
    out["read_through"] = ({"insufficient_data": True} if not ratios else {
        "value": round(sum(ratios) / len(ratios), 4),
        "direction": _checkup_trend(ratios), "sample_n": len(ratios)})
    # corpus-coverage: point-in-time GAUGE, NO direction (a single all-time
    # aggregate, audit L3-4)
    cc = ret.get("corpus_coverage") or {}
    out["corpus_coverage"] = ({"insufficient_data": True}
                              if cc.get("ratio") is None else {
        "value": cc.get("ratio"), "gauge": True,
        "sample_n": cc.get("corpus_size", 0)})
    # error-recovery yield — the KEPT miner arm (claude-error-records)
    er = _premise_corpus_metric_value("claude-error-records", kdir)
    out["error_recovery"] = ({"insufficient_data": True} if er is None else {
        "value": er, "gauge": True, "sample_n": er})
    # recurrence gauge — RETIRED, low-precision, NO direction, NEVER a workorder
    # driver (decide-miner-recurrence-arm-retired-keep-error-recovery, audit L3-1)
    rc = _premise_corpus_metric_value("recurrence-keys-at-n", kdir)
    out["recurrence"] = ({"insufficient_data": True} if rc is None else {
        "value": rc, "gauge": True, "low_precision": True, "sample_n": rc})
    # learning-freshness (N=14): a fresh learning (index `created` <= N days) whose
    # id was SURFACED by recall in a session — a faithful LOWER BOUND over the
    # existing derive_retrieval seam (returned_ids), NOT a phantom recall-ts join
    # (audit L3-2; see the worklog `> DECISION:`).
    out["learning_freshness"] = _checkup_learning_freshness(kdir, ret, anchor)
    return out


def _fresh_learning_ids(kdir, anchor):
    """Ids of learnings created within CHECKUP_LEARNING_FRESH_DAYS of `anchor`.

    READ-ONLY: `_allow_rebuild=False` matters — a self-exam must never trigger an
    index rebuild as a side effect of looking, or the act of measuring changes the
    thing measured. Every failure mode degrades to "no fresh learnings" rather
    than raising: an unreadable index or one malformed `created` date must not
    take down the whole exam.
    """
    import calendar
    import datetime
    try:
        data, _err = load_index(kdir, _allow_rebuild=False)
        entries = (data or {}).get("entries") or []
    except Exception:
        entries = []
    cutoff = anchor - CHECKUP_LEARNING_FRESH_DAYS * 86400
    fresh = set()
    for e in entries:
        if not isinstance(e, dict) or e.get("category") != "learnings":
            continue
        eid, created = e.get("id"), e.get("created")
        if not eid or not created:
            continue
        try:
            cepoch = calendar.timegm(
                datetime.datetime.strptime(created, "%Y-%m-%d").timetuple())
        except (ValueError, TypeError):
            continue
        if cepoch >= cutoff:
            fresh.add(eid)
    return fresh


def _checkup_learning_freshness(kdir, ret, anchor):
    """Fresh learnings (index `created` <= N days before the STABLE window anchor)
    that recall SURFACED in a session (lower bound). The cutoff keys off `anchor`
    (the cadence-bucket epoch), NEVER live now — a learning near the N-day boundary
    would otherwise flip fresh<->stale between two idempotent re-runs, leaking live
    time into the byte-stable report (adversarial-review finding). load_index is
    READ-ONLY (no rebuild write). Degrades to insufficient data on an empty
    corpus."""
    fresh = _fresh_learning_ids(kdir, anchor)
    if not fresh:
        return {"insufficient_data": True}
    surfaced = set()
    for s in ret.get("sessions", []):
        for i in (s.get("returned_ids") or []):
            surfaced.add(i)
    used = fresh & surfaced
    return {"value": len(used), "fresh_total": len(fresh),
            "gauge": True, "lower_bound": True, "sample_n": len(fresh)}


def _checkup_plan_title(ptext):
    """First `# ` heading of a plan, collapsed to one line + truncated. Best-effort
    (empty string when none). Feeds the stalled-plan candidate text so a plan about
    editing a kernel path is DROPPED by the reject scan (defense-in-depth)."""
    for ln in (ptext or "").splitlines():
        s = ln.strip()
        if s.startswith("#"):
            t = s.lstrip("#").strip()
            if t:
                return " ".join(t.split())[:140]
    return ""


def _checkup_walk_plans(kdir, now):
    """ONE read-only walk of work/<feature>/plan.md + work/auto/<slug>/plan.md,
    returning per-plan {feature, rel, state, claim_age_h, title, is_auto, mtime_d}.
    Reused by plan-health AND drift (no double walk, audit L4-4/L7-04). Reuses the
    dream drift primitives (_read_plan_status/_parse_claim/_iso_to_epoch)."""
    import glob
    work = os.path.join(kdir, "work")
    paths = sorted(set(glob.glob(os.path.join(work, "*", "plan.md")) +
                       glob.glob(os.path.join(work, "auto", "*", "plan.md"))))
    out = []
    for p in paths:
        d = os.path.dirname(p)
        feat = os.path.relpath(d, work)
        # NEVER examine checkup's OWN emitted workorders — they are checkup's
        # OUTPUT, not input. Counting them would (a) make plan-health draft/rotting
        # counts change every cycle (breaking the byte-stable report, INV-1) and
        # (b) create a self-observation feedback loop. Other services' auto-
        # workorders (dream/prefer/review) are legitimate external plans to observe.
        if feat.startswith("auto/checkup-"):
            continue
        try:
            with open(p, "r", encoding="utf-8") as f:
                ptext = f.read()
        except OSError:
            continue
        state, _cnt = _read_plan_status(ptext)
        claim = _parse_claim(ptext)
        cepoch = _iso_to_epoch(claim["ts"]) if claim else None
        age_h = ((now - cepoch) / 3600.0) if cepoch is not None else None
        try:
            mtime_d = (now - os.path.getmtime(p)) / 86400.0
        except OSError:
            mtime_d = 0.0
        out.append({"feature": feat, "rel": "work/%s/plan.md" % feat,
                    "state": state, "claim_age_h": age_h,
                    "title": _checkup_plan_title(ptext),
                    "is_auto": feat.startswith("auto/"), "mtime_d": mtime_d})
    return out


def _checkup_plan_health(kdir, now, plans=None):
    """done-vs-rotting over the 6-state lifecycle (audit L5-2): rot = `running`-
    stalled (claim >= STALLED_CLAIM_HOURS) + `draft` idle past its TTL; EXCLUDE
    cancelled/superseded/done from the rot numerator; `ready` = healthy-waiting.
    Read-only — a stalled plan is a REPORTED finding, never a state flip."""
    plans = _checkup_walk_plans(kdir, now) if plans is None else plans
    stalled, counts = [], {s: 0 for s in _PLAN_STATES}
    rotting = 0
    for p in plans:
        counts[p["state"]] = counts.get(p["state"], 0) + 1
        if p["state"] == "running" and p["claim_age_h"] is not None \
                and p["claim_age_h"] >= STALLED_CLAIM_HOURS:
            stalled.append({"feature": p["feature"], "rel": p["rel"],
                            "title": p["title"]})
            rotting += 1
        elif p["state"] == "draft":
            ttl = AUTO_DRAFT_TTL_DAYS if p["is_auto"] else OPERATOR_DRAFT_TTL_DAYS
            if p["mtime_d"] > ttl:
                rotting += 1
    done = counts.get("done", 0)
    return {"stalled": stalled, "rotting": rotting, "done": done,
            "ready": counts.get("ready", 0), "running": counts.get("running", 0),
            "draft": counts.get("draft", 0), "counts": counts,
            "total": len(plans),
            "done_ratio": (round(done / len(plans), 4) if plans else None),
            "insufficient_data": not plans}


_EXPORTS = [
    "AUTO_DRAFT_TTL_DAYS",
    "OPERATOR_DRAFT_TTL_DAYS",
    "_checkup_trend",
    "_checkup_flywheel",
    "_checkup_learning_freshness",
    "_checkup_plan_title",
    "_checkup_walk_plans",
    "_checkup_plan_health",
]

__all__ = list(_EXPORTS)
