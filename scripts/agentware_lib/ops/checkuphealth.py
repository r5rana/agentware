"""agentware_lib.ops.checkuphealth -- the RUNTIME and PACKAGE probes (layer: ops).

Owns: the probes that grade the machine rather than the knowledge -- config drift
(`_checkup_drift`), a stale updater heartbeat (`_checkup_heartbeat_stale`), the
dream journal reader and its watch (`_checkup_parse_journal` /
`_checkup_watch_dream`), frozen-metric detection (`_checkup_frozen_metrics`),
worktree count (`_checkup_worktree_count`), retrieval latency
(`_checkup_recall_p50_ms`), the KB-structure migration triggers
(`_checkup_structure_triggers`) and the package-version check
(`_audit_pkg_version_check`).

WHAT THESE ACTUALLY ASK
"Is the machine still doing what the operator configured?" Every one of these can
be wrong while every test passes: an updater that silently stopped checking in, a
dream that has not run for a week, a retrieval p50 that has quietly tripled, a
worktree count that keeps growing because something is not cleaning up. None of
that fails a build; all of it degrades the system.

A STALE HEARTBEAT is the sharpest of these, and the reason the whole exam exists:
a silent updater and a healthy one look identical from outside, so absence of news
has to be measured explicitly rather than assumed to be good news.

`_checkup_frozen_metrics` is the inverse guard: a metric that has NOT moved for
MIN_FROZEN_CYCLES is suppressed, so the exam does not repeat itself into being
ignored. Both directions matter -- report what changed, stay quiet about what did
not.

Does NOT own: knowledge-base probes (ops.checkupprobes); thresholds and cadence
(ops.checkupconfig); emission (ops.checkupemit); rendering (ops.checkupcmd).
Layer deps: ops -> core (config, dates, gitio, pkgstate/pkgchannel/pkgtrust for the
updater's own view of itself) + features (dream journal and health, pkgapply,
pkgupdater) + same-layer ops. The package probes read the SAME state the updater
writes rather than re-deriving it, so the exam cannot disagree with the thing it
is examining.
Fan-in: 1 at landing (regen: python3 tests/_manifest_gen.py --write).

The bodies are a PURE MOVE from the monolith -- byte-identical, comments and all.
"""

import json
import os
import re
import subprocess
import time

import agentware_lib.core.config as config
from agentware_lib.core.config import (
    KB_MODE_TEAM,
    _read_config_key,
    resolve_kb_mode,
    resolve_knowledge_dir,
)
from agentware_lib.core.dates import _iso_to_epoch, _now_iso
from agentware_lib.core.frontmatter import _operator_handle
from agentware_lib.core.gitio import REPO_ROOT, _git
from agentware_lib.core.routing import resolve_dream, resolve_user_handle
from agentware_lib.core.pkghome import _PkgStateError
from agentware_lib.core.pkgstate import _pkg_state_read
from agentware_lib.core.pkgchannel import (
    _pkg_channel_tags,
    _pkg_verify_tag,
    _pkg_version_tuple,
)
from agentware_lib.core.pkgtrust import (
    _pkg_allowed_signers_path,
    _pkg_list_signers,
)
from agentware_lib.features.pkgapply import _pkg_version
from agentware_lib.features.pkgupdater import resolve_updater
from agentware_lib.features.dream import DREAM_STALE_HOURS, _audit_dream_health_check
from agentware_lib.features.dreamjournal import DREAM_JOURNAL_REL
from agentware_lib.knowledge.index import load_index
from agentware_lib.ops.checkupconfig import (
    CHECKUP_CORPUS_ENTRIES_MAX,
    CHECKUP_FROZEN_METRICS,
    CHECKUP_HEARTBEAT_STALE_H,
    CHECKUP_RECALL_P50_MS_MAX,
    CHECKUP_WORKTREE_MAX,
    MIN_FROZEN_CYCLES,
)
from agentware_lib.ops.metrics import _read_metrics_jsonl
from agentware_lib.ops.planauthor.planstate import STALLED_CLAIM_HOURS


def _audit_pkg_version_check(kdir):
    """Rule-7 rollout visibility: flag THIS machine as BEHIND the newest VERIFIED
    stable tag in the local channel. INERT when the updater is OFF (opt-in default)
    or pre-go-live (no signed channel / no pinned key). Read-only (no network
    fetch). The cross-machine fleet-coverage sink is DEFERRED to team-mode.

    A tampered HOME state sidecar or trust anchor is reported as a clean FAIL check,
    NEVER an unhandled traceback that crashes the whole `audit` command: EVERY
    sidecar/anchor read here (_pkg_state_read directly AND _pkg_version, which falls
    back to the sidecar's last_applied_version) can raise _PkgStateError, so the
    whole body fails closed to ok=False (audit A5)."""
    name = "pkg_version"
    try:
        if resolve_updater() != "1":
            return {"name": name, "ok": True, "pkg_version": _pkg_version(),
                    "details": ["inert: automatic updates OFF (opt-in; enable with "
                                "`scripts/agentware config --set-updater on`)"]}
        try:
            signers_present = bool(_pkg_list_signers())
        except _PkgStateError as exc:
            return {"name": name, "ok": False,
                    "details": ["trust anchor tampered — %s" % exc]}
        if not signers_present:
            return {"name": name, "ok": True, "pkg_version": _pkg_version(),
                    "details": ["inert: no publisher key pinned (pre-go-live)"]}
        state = _pkg_state_read()
        if state.get("needs_manual_recovery"):
            return {"name": name, "ok": False, "pkg_version": _pkg_version(),
                    "details": ["needs_manual_recovery is set — a prior apply is "
                                "stranded (see ~/.agentware/update-state.json)"]}
        target = REPO_ROOT
        signers = _pkg_allowed_signers_path()
        newest_verified = None
        for tag in _pkg_channel_tags(target):
            ok, _d = _pkg_verify_tag(target, tag, signers)
            if ok:
                newest_verified = tag
                break
        applied_str = _pkg_version(target)
        base = {"name": name, "pkg_version": applied_str,
                "newest_verified_tag": newest_verified}
        if not newest_verified:
            return dict(base, ok=True,
                        details=["no verified stable tag in the local channel yet"])
        nv = _pkg_version_tuple(newest_verified)
        applied = _pkg_version_tuple(applied_str)
        if applied is None or (nv is not None and nv > applied):
            return dict(base, ok=False,
                        details=["BEHIND: newest verified stable tag %s > applied %s"
                                 " — the updater has not applied it yet (check the "
                                 "tick / run `scripts/agentware update`)"
                                 % (newest_verified, applied_str or "none")])
        return dict(base, ok=True,
                    details=["up to date: applied %s (newest verified %s)"
                             % (applied_str, newest_verified)])
    except _PkgStateError as exc:
        return {"name": name, "ok": False,
                "details": ["update-state sidecar tampered — %s" % exc]}


def _checkup_drift(kdir, now, watcher, plan_health):
    """Declared-vs-derived drift, each {kind, declared, derived, severity}. STRICTLY
    read-only; renders BOOLEAN crossings only (no live ages) for byte-stability."""
    out = []
    # (i) dream declared ON but no cycle fired on cadence — REFERENCE Task 4's
    # single computation, never recompute (audit L4-4).
    if resolve_dream() == "1" and not watcher.get("fired_ok"):
        out.append({"kind": "dream-declared-not-fired", "declared": "dream ON",
                    "derived": watcher.get("verdict") or "no fresh cycle",
                    "severity": "warn"})
    # (ii) header `running` whose claim went stale (declared-active vs derived-
    # stalled) — reuse plan-health's stalled set (no re-walk).
    for s in plan_health.get("stalled", []):
        out.append({"kind": "running-but-stalled", "declared": "Status: running",
                    "derived": "claim aged past %dh" % STALLED_CLAIM_HOURS,
                    "severity": "warn", "feature": s["feature"]})
    # (iii) version drift split: applied-vs-available (honors updater-OFF inert)
    # and heartbeat-ts freshness as tick-liveness — NOT a tautological version-vs-
    # version compare (audit L3-3).
    try:
        vc = _audit_pkg_version_check(kdir)
    except Exception:
        vc = {"ok": True}
    if not vc.get("ok"):
        out.append({"kind": "version-drift",
                    "declared": "applied %s" % (vc.get("pkg_version") or "?"),
                    "derived": "; ".join(vc.get("details", [])) or "behind",
                    "severity": "warn"})
    if _checkup_heartbeat_stale(kdir, now):
        out.append({"kind": "heartbeat-stale",
                    "declared": "tick expected ~30m", "derived": "no fresh "
                    "pkg_version heartbeat (> %dh)" % CHECKUP_HEARTBEAT_STALE_H,
                    "severity": "info"})
    return out


def _checkup_heartbeat_stale(kdir, now):
    """True iff the latest pkg_version heartbeat in logs/metrics.jsonl is older than
    the tick-liveness threshold. Boolean crossing only (no live age rendered).
    Absent channel -> not-stale (fail-open, never a false alarm on a bare KB)."""
    try:
        events = _read_metrics_jsonl(kdir)
    except Exception:
        return False
    ts = None
    for e in events:
        if isinstance(e, dict) and e.get("event") == "pkg_version" and e.get("ts"):
            ts = e["ts"]
    if not ts:
        return False
    epoch = _iso_to_epoch(ts)
    if epoch is None:
        return False
    return (now - epoch) > CHECKUP_HEARTBEAT_STALE_H * 3600


def _checkup_parse_journal(kdir):
    """Parse logs/dream-journal.md into per-cycle {ts, metrics:{"<step>.<field>":
    float}} records (fixed-field lines, audit L4-2). ONLY numeric, space-free
    values are captured — booleans (True/False) and dash placeholders naturally
    fail float() and are skipped; failed_tests/reason carry spaces and are excluded
    by the space-free rule. Read-only; tolerant of a missing/garbled journal ([])."""
    path = os.path.join(kdir, DREAM_JOURNAL_REL)
    try:
        with open(path, "r", encoding="utf-8") as f:
            text = f.read()
    except OSError:
        return []
    step_re = re.compile(r"^- step (\S+) \([^)]*\):\s+\S+(?:\s+(.*))?$")
    cycles, cur = [], None
    for ln in text.splitlines():
        m = re.match(r"^## dream (\S+)\s*$", ln)
        if m:
            cur = {"ts": m.group(1), "metrics": {}}
            cycles.append(cur)
            continue
        if cur is None:
            continue
        sm = step_re.match(ln)
        if not sm:
            continue
        step = sm.group(1)
        tail = sm.group(2) or ""
        for kv in tail.split():
            if "=" not in kv:
                continue
            k, _, v = kv.partition("=")
            try:
                val = float(v)
            except (ValueError, TypeError):
                continue     # non-numeric (bool/dash/string) -> skip
            cur["metrics"]["%s.%s" % (step, k)] = val
    return cycles


def _checkup_frozen_metrics(cycles):
    """The "130-for-13-nights" check: for each ALLOWLISTED metric, gather its
    PRESENT (ts, value) samples across the window cycles; flag frozen ONLY when
    there are >= MIN_FROZEN_CYCLES present samples spanning distinct cycles AND the
    spread (max-min) is within the metric's tolerance band. Fewer present samples
    -> NOT frozen (never a false freeze on n<MIN, audit L4-1/L4-5). A change-gate-
    skipped step is simply ABSENT here (a GAP, not a repeated value)."""
    frozen = []
    for metric, tol in sorted(CHECKUP_FROZEN_METRICS.items()):
        samples = [(c["ts"], c["metrics"][metric]) for c in cycles
                   if metric in c.get("metrics", {})]
        if len(samples) < MIN_FROZEN_CYCLES:
            continue
        vals = [v for _ts, v in samples]
        if (max(vals) - min(vals)) <= tol:
            frozen.append({"metric": metric, "value": vals[0],
                           "present_cycles": len(samples), "tolerance": tol})
    return frozen


def _checkup_watch_dream(kdir, cycles, now):
    """REUSE _audit_dream_health_check for the single last-cycle fired/stale verdict
    (audit L7-03), and own ONLY what dream_health cannot: the window-level missed-
    cycle COUNT (gaps >= DREAM_STALE_HOURS with dream ON) and the frozen-metric
    check over the journal. Returns {fired_ok, verdict, missed_cycles,
    frozen_metrics, insufficient_data}."""
    try:
        health = _audit_dream_health_check(kdir)
    except Exception:
        health = {"ok": True, "details": ["dream_health unavailable"]}
    verdict = "; ".join(health.get("details", [])) or ("ok" if health.get("ok")
                                                        else "fail")
    # window-level missed-cycle count (only meaningful when dream is declared ON)
    missed = 0
    if resolve_dream() == "1":
        epochs = sorted(e for e in (_iso_to_epoch(c["ts"]) for c in cycles)
                        if e is not None)
        gap = DREAM_STALE_HOURS * 3600
        for a, b in zip(epochs, epochs[1:]):
            if (b - a) > gap:
                missed += 1
        if epochs and (now - epochs[-1]) > gap:
            missed += 1
        elif not epochs:
            missed += 1     # dream ON but no cycle in the window at all
    frozen = _checkup_frozen_metrics(cycles)
    # PRESENT-sample sufficiency: fewer than MIN across all metrics AND no cycles
    # -> insufficient data for the frozen check (never a false freeze).
    return {"fired_ok": bool(health.get("ok")), "verdict": verdict,
            "missed_cycles": missed, "frozen_metrics": frozen,
            "cycles_in_window": len(cycles),
            "insufficient_data": len(cycles) < MIN_FROZEN_CYCLES}


def _checkup_worktree_count():
    """Distinct git worktrees for the package repo via a READ-ONLY `git worktree
    list --porcelain`, or None on error (uncheckable -> never fires)."""
    try:
        cp = _git(REPO_ROOT, "worktree", "list", "--porcelain")
    except Exception:
        return None
    if cp.returncode != 0:
        return None
    return sum(1 for ln in cp.stdout.splitlines() if ln.startswith("worktree "))


def _checkup_recall_p50_ms():
    """recall p50 latency from the benchmarks/history.jsonl `latency_ms_p50` eval
    proxy (env-dependent + stale between eval runs), or None. Report-only — NEVER
    fires a workorder on its own (Q8). There is NO per-recall latency in
    metrics.jsonl, so this is the only (weak) available source."""
    path = os.path.join(REPO_ROOT, "benchmarks", "history.jsonl")
    val = None
    try:
        with open(path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    rec = json.loads(line)
                except ValueError:
                    continue
                if isinstance(rec, dict) and rec.get("latency_ms_p50") is not None:
                    val = rec.get("latency_ms_p50")
    except OSError:
        return None
    return val


def _checkup_structure_triggers(kdir):
    """Measure ONLY the structure-change signals that have a READ-ONLY source (Q8).
    rebuild-time is DROPPED — inert by construction (no passive source; timing a
    rebuild would WRITE index.json, breaking Q7). Each: {signal, value, threshold,
    fired, source, fires_workorder}. At solo scale EVERY signal is far under
    threshold, so this ships inert. Read-only."""
    out = []
    # corpus size — the one clean live threshold. load_index is READ-ONLY
    # (_allow_rebuild=False): a missing/corrupt index is a normal fresh-clone state,
    # and the default rebuild-on-load would WRITE index.json, breaking Q7 (adversarial-
    # review finding). A missing index -> None (uncheckable -> never fires).
    try:
        data, err = load_index(kdir, _allow_rebuild=False)
        n = None if err else len((data or {}).get("entries") or [])
    except Exception:
        n = None
    out.append({"signal": "corpus-size", "value": n,
                "threshold": CHECKUP_CORPUS_ENTRIES_MAX,
                "fired": bool(n is not None and n >= CHECKUP_CORPUS_ENTRIES_MAX),
                "source": "len(index.json entries)", "fires_workorder": True})
    # git-worktree count
    wt = _checkup_worktree_count()
    out.append({"signal": "git-worktrees", "value": wt,
                "threshold": CHECKUP_WORKTREE_MAX,
                "fired": bool(wt is not None and wt >= CHECKUP_WORKTREE_MAX),
                "source": "git worktree list", "fires_workorder": True})
    # multi-tenancy — team-mode flag + distinct provenance handles
    try:
        team = resolve_kb_mode() == KB_MODE_TEAM
    except Exception:
        team = False
    handles = set()
    uh = resolve_user_handle()
    if uh:
        handles.add(uh)
    try:
        handles.add(_operator_handle(kdir))
    except Exception:
        pass
    nh = len(handles)
    out.append({"signal": "multi-tenancy", "value": (max(nh, 2) if team else nh),
                "threshold": 2,
                "fired": bool(team or nh >= 2),
                "source": "KB_MODE_TEAM + distinct handles",
                "fires_workorder": True})
    # recall p50 latency — REPORT-ONLY, never fires a workorder on its own
    p50 = _checkup_recall_p50_ms()
    out.append({"signal": "recall-p50-ms", "value": p50,
                "threshold": CHECKUP_RECALL_P50_MS_MAX,
                "fired": bool(p50 is not None and p50 >= CHECKUP_RECALL_P50_MS_MAX),
                "source": "benchmarks/history.jsonl (eval proxy, stale)",
                "fires_workorder": False})
    return out


_EXPORTS = [
    "_checkup_drift",
    "_checkup_heartbeat_stale",
    "_checkup_parse_journal",
    "_checkup_frozen_metrics",
    "_checkup_watch_dream",
    "_checkup_worktree_count",
    "_checkup_recall_p50_ms",
    "_checkup_structure_triggers",
    "_audit_pkg_version_check",
]

__all__ = list(_EXPORTS)
